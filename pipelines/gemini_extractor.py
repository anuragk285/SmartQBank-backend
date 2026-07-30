from google import genai
from google.genai import types
import os, re, time
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import List
import json
import cloudinary
import cloudinary.uploader
from schemas import SubjectCreate
from pathlib import Path
from pipelines.prompt import prompt
from pipelines.crop_loader import generate_crop_pngs_from_pdf
from collections import defaultdict
from pipelines.topic_matcher import match_topics

load_dotenv()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
CLOUDINARY_API_SECRET = os.getenv('CLOUDINARY_API_SECRET')
CLOUDINARY_CLOUD_NAME = os.getenv('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = os.getenv('CLOUDINARY_API_KEY')
TARGET_FOLDER = 'pipelines/ml_images'
target_folder = Path(TARGET_FOLDER)

client = genai.Client(api_key=GEMINI_API_KEY)
#embed_model = SentenceTransformer("all-mpnet-base-v2")

cloudinary.config( 
    cloud_name =  CLOUDINARY_CLOUD_NAME,
    api_key = CLOUDINARY_API_KEY,
    api_secret = CLOUDINARY_API_SECRET,
    secure=True
)

CROP_FILENAME_RE = re.compile(r'^page_(\d+)_crop_(\d+)\.png$')

class Question(BaseModel):
    text: str
    subject_code: str
    unit: int
    difficulty_level: int
    marks: int
    year: int
    page_number: int
    image_indices: List[int]

class QuestionListContainer(BaseModel):
    questions: List[Question]

class PaperInfo(BaseModel):
    paper_name: str
    year: int

class ExtractionResult(BaseModel):
    subject: SubjectCreate
    paperInfo: PaperInfo
    questions: List[Question]

class ExtractedQuestion(BaseModel):
    unit: int
    question_id: int
    topic: str

class ExtractedQuestionList(BaseModel):
    questions: List[ExtractedQuestion]

def upload_and_get_url(image_path):
    result = cloudinary.uploader.upload(image_path, folder="question_images")
    return result["secure_url"]

def page_num(path):
    match = re.search(r'page_(\d+)', os.path.basename(str(path)))
    return int(match.group(1)) if match else 0

def existing_crop_indices(pdf_paper):
    """page_number -> set of crop indices that actually exist on disk for
    that page, parsed straight from the crop filenames doclayout-yolo wrote."""
    by_page = defaultdict(set)
    for f in (pdf_paper / 'crops').glob("*.png"):
        m = CROP_FILENAME_RE.match(f.name)
        if m:
            by_page[int(m.group(1))].add(int(m.group(2)))
    return by_page

def make_call(uploaded_pages, temperature):
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[
            *[types.Part.from_uri(file_uri=f.uri, mime_type=f.mime_type)for f in uploaded_pages],
            types.Part.from_text(text=prompt),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractionResult,
            temperature=temperature,
            max_output_tokens=8192
        ),
    )
    return response

def make_call_with_retry(uploaded_pages, max_retries=3):
    temperatures = [0.1, 0.3, 0.5, 0.7]
    for attempt in range(max_retries):
        response = make_call(uploaded_pages=uploaded_pages, temperature=temperatures[attempt])
        candidate = response.candidates[0] if response.candidates else None
        finish_reason = candidate.finish_reason if candidate else None
        if response.parsed is not None:
            return response
        if finish_reason and "RECITATION" in str(finish_reason):
            print(f"RECITATION hit, retry {attempt+1}/{max_retries}")
            time.sleep(2 * (attempt + 1))
            continue
        return response  
    return response

generate_crop_pngs_from_pdf()

for pdf_paper in target_folder.iterdir():
    base_name = os.path.splitext(os.path.basename(pdf_paper))[0]
    print(f"Found folder/file: {base_name}")
    try:
        if pdf_paper.is_dir():
            try:                
                with open('pipelines/extraction_complete.json', 'r') as f:
                    subjects = json.load(f)
                    exists = any(obj['name'] == base_name for obj in subjects['subject_names'])
                    if exists: continue
            except(KeyError, json.decoder.JSONDecodeError):
                subjects = {}
            outlined_paths = sorted((pdf_paper / 'outlined').iterdir(), key=page_num)
            uploaded_pages = [client.files.upload(file=png) for png in outlined_paths]
            response = make_call_with_retry(uploaded_pages=uploaded_pages)
            result: ExtractionResult | None = response.parsed
            candidate = response.candidates[0] if response.candidates else None
            finish_reason = candidate.finish_reason if candidate else "NO_CANDIDATE"
            if result is None:
                print(f"{base_name}: parsed=None, finish_reason={finish_reason}")
                print("Raw text (truncated to 2000 chars):")
                print((response.text or "")[:2000])
                continue  
            questions = result.questions

            # Verify every index Gemini referenced actually exists as a crop file, and
            # every crop file that exists got claimed by at least one question. This
            # compares SETS per page rather than summing counts, so a tag legitimately
            # shared by two sub-parts (same index inside two questions' image_indices)
            # is correctly NOT flagged — only a real disagreement is.
            existing_by_page = existing_crop_indices(pdf_paper)
            referenced_by_page = defaultdict(set)
            for q in questions:
                referenced_by_page[q.page_number].update(q.image_indices)

            mismatch_msgs = []
            for page in set(existing_by_page) | set(referenced_by_page):
                existing = existing_by_page.get(page, set())
                referenced = referenced_by_page.get(page, set())
                hallucinated = referenced - existing
                unclaimed = existing - referenced
                if hallucinated:
                    mismatch_msgs.append(f"page {page}: Gemini referenced index(es) {sorted(hallucinated)} with no matching crop file")
                if unclaimed:
                    mismatch_msgs.append(f"page {page}: crop index(es) {sorted(unclaimed)} exist but were never claimed by a question")

            if mismatch_msgs:
                print(f"{pdf_paper.name}: mismatch => SKIPPED SUCCESSFULLY")
                for msg in mismatch_msgs:
                    print(f"  {msg}")
                print()
                continue

            question_text_list = [q.text for q in questions]
            regulation_code = result.subject.regulation_code
            subject_code = result.subject.subject_code
            matched_questions = match_topics(regulation_code, subject_code, question_text_list)

            # Cache by (page, index) so a crop shared across multiple questions (e.g. a
            # stem table used by two sub-parts) gets uploaded to Cloudinary once, not
            # once per question that references it.
            _url_cache = {}
            def get_url_for(page, idx):
                key = (page, idx)
                if key not in _url_cache:
                    crop_path = pdf_paper / f"crops/page_{page}_crop_{idx}.png"
                    _url_cache[key] = upload_and_get_url(image_path=crop_path)
                return _url_cache[key]

            final_question_list = []
            for i, q in enumerate(questions):
                new_q = {
                    "text": q.text,
                    "subject_code": q.subject_code,
                    "unit": matched_questions[i]['unit'],
                    "difficulty_level": q.difficulty_level,
                    "year": q.year,
                    "marks": q.marks,
                    "image_urls": [get_url_for(q.page_number, idx) for idx in sorted(q.image_indices)],
                    "topic": matched_questions[i]['topic']
                }
                final_question_list.append(new_q)

            extracted_data = {
                "subject": result.subject.model_dump(),
                "paperInfo": result.paperInfo.model_dump(),
                "questions": final_question_list
            }
            try:
                with open('pipelines/extracted_data.json', 'r') as f:
                    existing_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                existing_data = {"papers": []}

            existing_data.setdefault("papers", []).append(extracted_data)

            with open("pipelines/extracted_data.json", "w") as f:
                json.dump(existing_data, f, indent=4, ensure_ascii=False)

            try:
                with open('pipelines/extraction_complete.json', 'r') as f:
                    subjects = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                subjects = {"subject_names": []}

            subjects.setdefault("subject_names", []).append({"name": base_name})
            with open('pipelines/extraction_complete.json', 'w') as f:
                json.dump(subjects, f, indent=4)
    except Exception as e:
        resp_text = response.text if 'response' in dir() else "(no response received this iteration)"
        print(f"Error: {e} while extracting on {base_name}\nresponse.text:\n\n{resp_text}\n\n")
        raise