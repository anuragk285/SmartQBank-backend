import os, cv2, re
from pathlib import Path
import pymupdf
import pytesseract
from doclayout_yolo import YOLOv10
from huggingface_hub import hf_hub_download
from PIL import ImageDraw, Image

TARGET_FOLDER = 'pipelines/uploads'
target_folder = Path(TARGET_FOLDER)

_layout_model_path = hf_hub_download(
    repo_id="juliozhao/DocLayout-YOLO-DocStructBench",
    filename="doclayout_yolo_docstructbench_imgsz1024.pt",
)
layout_model = YOLOv10(_layout_model_path)
FIGURE_LABELS = {"figure", "isolate_formula", "table"}

_DATE_LIKE = re.compile(r'^\s*\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}\s*$')

_SECTION_HEADER_RE = re.compile(
    r'(?i)('
    r'\bpart\s*[-–—]?\s*[a-z0-9]\b|'           # Part - A, PART B
    r'\bsection\s*[-–—]?\s*[a-z0-9]\b|'        # Section - A
    r'\b\d+\s*q\s*[xX*].*?marks?\b|'           # 5Q X 2M = 10 Marks
    r'\b\d+\s*[xX*]\s*\d+\s*m\b|'              # 5 X 10M
    r'\bmax\.?\s*marks\b'                      # Max Marks
    r')'
)

def page_num(path):
    match = re.search(r'page_(\d+)', os.path.basename(str(path)))
    return int(match.group(1)) if match else 0


def pdf_to_images(pdf_path, output_path, dpi=300):
    try:
        os.makedirs(output_path)
    except (FileExistsError):
        return None
    doc = pymupdf.open(pdf_path)
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img_path = output_path + f"/page_{page.number+1}.png"
        pix.save(img_path)
        images.append(img_path)
    return images  # returns list of paths of images


def is_mostly_blank(cropped_bgr, ink_thresh=0.01):
    """Pure pixel check, no OCR, no class-specific assumptions — safe on
    every class. Catches a stray empty/whitespace box; can't misfire on real
    content, since a genuine figure, table, or matrix always carries far
    more ink than this threshold."""
    if cropped_bgr.size == 0:
        return True
    gray = cv2.cvtColor(cropped_bgr, cv2.COLOR_BGR2GRAY)
    ink_ratio = (gray < 200).sum() / gray.size
    return ink_ratio < ink_thresh


def is_section_header(text):
    """Detects exam section headers and marking metadata."""
    return bool(_SECTION_HEADER_RE.search(text))

def is_equation_or_noise(text, min_chars=8):
    """Now takes pre-extracted OCR text to save processing time."""
    compact = re.sub(r'\s+', '', text)
    if len(compact) < min_chars:
        return True                                          
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) == 1 and _DATE_LIKE.match(lines[0]):
        return True                                           
    operator_lines = sum(1 for ln in lines if re.search(r'[=≤≥<>]', ln))
    return (operator_lines / len(lines)) >= 0.5   # Fixed inequality to >=

def iou(box_a, box_b):
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a, area_b = (ax2 - ax1) * (ay2 - ay1), (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter + 1e-6)


def containment_ratio(box_a, box_b):
    """How much of the SMALLER box's area sits inside the other box. IoU
    alone misses this: a small box nested inside a much larger one can score
    well under 0.5 IoU purely because the union is dominated by the larger
    box — even though the smaller box is 100% redundant with it."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a, area_b = (ax2 - ax1) * (ay2 - ay1), (bx2 - bx1) * (by2 - by1)
    return inter / (min(area_a, area_b) + 1e-6)


def dedupe_overlapping(detected_elements, iou_thresh=0.5, containment_thresh=0.8):
    """Catches same-region-two-classes duplicates (plain IoU) AND a smaller
    box nested fully inside a bigger one (containment ratio — low IoU, but
    100% redundant). Sorted by area, not confidence: when one box contains
    another, keep the larger/complete one regardless of which scored
    higher — keeping only the smaller nested crop would silently drop
    everything around it."""
    by_area_desc = sorted(
        detected_elements,
        key=lambda e: (e["box"][2] - e["box"][0]) * (e["box"][3] - e["box"][1]),
        reverse=True
    )
    kept = []
    for element in by_area_desc:
        is_dup = any(
            iou(element["box"], k["box"]) > iou_thresh
            or containment_ratio(element["box"], k["box"]) > containment_thresh
            for k in kept
        )
        if not is_dup:
            kept.append(element)
    return kept


def pad_box(xmin, ymin, xmax, ymax, img_shape, pad=20):
    h, w = img_shape[:2]
    xmin = max(0, xmin - pad)
    ymin = max(0, ymin - pad)
    xmax = min(w, xmax + pad)
    ymax = min(h, ymax + pad)
    return xmin, ymin, xmax, ymax


def _detect_elements(image_path, conf):
    """One detection pass for a page. Filters to FIGURE_LABELS, drops blanks, 
    headers, and noise, dedupes, and returns elements sorted top-to-bottom."""
    img_bgr = cv2.imread(image_path)
    results = layout_model.predict(image_path, imgsz=1024, conf=conf, iou=0.5, agnostic_nms=True, verbose=False)
    result = results[0]
    candidates = []
    
    for i, cls in enumerate(result.boxes.cls):
        class_id = int(cls.item())
        label = result.names[class_id].lower()
        if label not in FIGURE_LABELS:
            continue
            
        box = result.boxes.xyxy[i].cpu().numpy().astype(int)
        box_conf = float(result.boxes.conf[i].item())
        x1, y1, x2, y2 = box
        crop = img_bgr[y1:y2, x1:x2]
        
        if is_mostly_blank(crop):
            print(f"  [reject:blank]  {label:16s} conf={box_conf:.2f} box={list(box)}")
            continue
            
        # Extract OCR text ONCE per crop to be used by all text-based filters
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        text = pytesseract.image_to_string(Image.fromarray(rgb), config='--psm 6').strip()

        # Reject section titles / mark distributions (fixes the "Part - A" bug)
        if is_section_header(text):
            print(f"  [reject:header] {label:16s} conf={box_conf:.2f} box={list(box)}")
            continue

        if label == "isolate_formula" and is_equation_or_noise(text):
            print(f"  [reject:noise]  {label:16s} conf={box_conf:.2f} box={list(box)}")
            continue
            
        candidates.append({"box": box, "label": label, "conf": box_conf})
        
    kept = dedupe_overlapping(candidates)
    if len(kept) < len(candidates):
        print(f"  [dedupe] {len(candidates) - len(kept)} overlapping/contained box(es) dropped")
        
    kept = sorted(kept, key=lambda e: e["box"][1])  # canonical top-to-bottom order -> tag numbers
    return kept, img_bgr


def process_page(image_path, outlined_output_path, crops_output_dir, conf=0.5):
    """Single detection pass per page, reused for both the numbered outline
    preview (what Gemini reads) and the actual crop files (what gets
    uploaded). Each box gets a small numbered tag at its top-left corner,
    matching the index in its crop's filename (page_N_crop_<tag>.png) —
    Gemini reads that number directly instead of inferring position, and
    reports it back per-question so a tag can be attributed to more than one
    question when a single image is legitimately shared (e.g. a table given
    once before two sub-parts that both need it)."""
    detected_elements, img_bgr = _detect_elements(image_path, conf)

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    for idx, element in enumerate(detected_elements, start=1):
        box = list(element["box"])
        draw.rectangle(box, outline="red", width=4)
        label = str(idx)
        tag_w, tag_h = 18 + 13 * len(label), 24
        draw.rectangle([box[0], box[1], box[0] + tag_w, box[1] + tag_h], fill="red")
        draw.text((box[0] + 6, box[1] + 3), label, fill="white")
    img.save(outlined_output_path)
    print(f"{len(detected_elements)} figure(s) detected -> {outlined_output_path}")

    os.makedirs(crops_output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    for idx, element in enumerate(detected_elements, start=1):
        xmin, ymin, xmax, ymax = element["box"]
        xmin, ymin, xmax, ymax = pad_box(xmin, ymin, xmax, ymax, img_bgr.shape, pad=20)
        cropped_segment = img_bgr[ymin:ymax, xmin:xmax]
        output_filename = f"{crops_output_dir}/{base_name}_crop_{idx}.png"
        cv2.imwrite(output_filename, cropped_segment)
        print(f"-> Saved index {idx}: {output_filename} (Bounding Box: {xmin}, {ymin}, {xmax}, {ymax})")


def generate_crop_pngs_from_pdf():
    for pdf_path in target_folder.glob("*.pdf"):
        file_name = os.path.splitext(os.path.basename(pdf_path))[0]
        parent_folder = 'pipelines/ml_images'
        full_path = os.path.join(parent_folder, file_name)
        if os.path.isdir(full_path):
            print(f"ml_images of {file_name} already exists")
            continue
        os.makedirs(full_path, exist_ok=True)
        curr_file_pngs = f"{full_path}/pngs"
        image_paths = pdf_to_images(pdf_path=pdf_path, output_path=curr_file_pngs)
        outlined_folder = f"{full_path}/outlined"
        crops_folder = f"{full_path}/crops"
        os.makedirs(outlined_folder, exist_ok=True)
        for i, image_path in enumerate(image_paths):
            process_page(
                image_path=image_path,
                outlined_output_path=f"{outlined_folder}/page_{i+1}.png",
                crops_output_dir=crops_folder,
            )


generate_crop_pngs_from_pdf()