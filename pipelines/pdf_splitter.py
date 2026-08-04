import os, re, pathlib
from pypdf import PdfReader, PdfWriter

# Flexible pattern for course code (e.g., "18 IT C01", "18ITC01", "18 IT01")
code_pattern = re.compile(
    r"\b\d{2}\s?[A-Z]{2,4}\s?[A-Z0-9]{2,4}\b", re.IGNORECASE
)

# Flexible patterns for key syllabus headings
objectives_pattern = re.compile(r"COURSE\s+OBJECTIVES?", re.IGNORECASE)
instruction_pattern = re.compile(r"INSTRUCTION\b", re.IGNORECASE)
index_pattern = re.compile(
    r"SCHEME\s+OF\s+(INSTRUCTION|EXAMINATION)", re.IGNORECASE
)


def split_syllabus_by_subject(pdf_path, output_folder="pipelines/split_subjects"):
    os.makedirs(output_folder, exist_ok=True)
    reader = PdfReader(pdf_path)

    subjects = []
    current_subject = None

    print(f"\nProcessing PDF: {pdf_path.name} ({len(reader.pages)} pages)")

    for page_num, page in enumerate(reader.pages):
        text = page.extract_text() or ""

        code_match = code_pattern.search(text)
        has_objectives = bool(objectives_pattern.search(text))
        has_instruction = bool(instruction_pattern.search(text))
        is_index_page = bool(index_pattern.search(text))

        # Subject start condition
        is_subject_start = (
            code_match
            and (has_objectives or has_instruction)
            and not is_index_page
        )

        if is_subject_start:
            raw_code = code_match.group(0).strip()
            course_code = "".join(re.sub(r"\s+", "_", raw_code).split("_"))

            if current_subject:
                subjects.append(current_subject)

            lines = [line.strip() for line in text.split("\n") if line.strip()]
            subject_title = course_code

            # Extract title from neighboring lines
            for i, line in enumerate(lines):
                if raw_code in line:
                    for next_line in lines[i + 1 : i + 5]:
                        if not any(
                            k in next_line.upper()
                            for k in [
                                "INSTRUCTION",
                                "CBIT",
                                "WITH EFFECT",
                                "DURATION",
                                "OBJECTIVES",
                            ]
                        ):
                            subject_title = next_line
                            break
                    break

            current_subject = {
                "code": course_code,
                "title": subject_title,
                "pages": [page_num],
            }
        elif current_subject:
            current_subject["pages"].append(page_num)

    if current_subject:
        subjects.append(current_subject)

    if not subjects:
        print("  ⚠️ Warning: No subject starting markers found in this PDF.")
        return

    # Export split PDFs
    for sub in subjects:
        writer = PdfWriter()
        for p in sub["pages"]:
            writer.add_page(reader.pages[p])

        clean_title = re.sub(r"[^\w\s-]", "", sub["title"]).strip().replace(" ", "_")
        output_filename = f"{sub['code']}_{clean_title}.pdf"
        output_filepath = os.path.join(output_folder, output_filename)

        with open(output_filepath, "wb") as output_file:
            writer.write(output_file)

        print(
            f"  ✅ Generated: {output_filename} (Pages {sub['pages'][0] + 1} to {sub['pages'][-1] + 1})"
        )

def get_regulation_code(name: str):
    return name.split('-')[0] if '-' in name else name

if __name__ == "__main__":
    # Use relative pathing or verify absolute path
    pdf_dir = pathlib.Path('/Users/anuragmac/Documents/projects/smart_question_back_project/backend/pipelines/topic_pdfs/')
    
    if not pdf_dir.exists():
        print(f"❌ Error: Directory does not exist: {pdf_dir}")
    else:
        pdf_files = list(pdf_dir.glob("*.pdf"))
        print(f"Found {len(pdf_files)} PDF file(s) in {pdf_dir}")
        
        for path in pdf_files:
            regulation_code = get_regulation_code(path.stem)
            split_syllabus_by_subject(pdf_path=path, output_folder=f'pipelines/split_subjects/{regulation_code}')