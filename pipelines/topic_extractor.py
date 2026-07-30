from google import genai
from google.genai import types
from pydantic import BaseModel
from typing import List
import pymupdf 
import os
import json
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TARGET_FOLDER = '/Users/anuragmac/Documents/projects/smart_question_back_project/backend/pipelines/split_subjects'
target_folder = Path(TARGET_FOLDER)

class UnitTopics(BaseModel):
    unit: int
    topics: List[str]

class SyllabusTopics(BaseModel):
    subject_code: str
    units: List[UnitTopics]

class ExtractedSyllabusTopics(BaseModel):
    syllabus_topics: List[SyllabusTopics]

client = genai.Client(api_key=GEMINI_API_KEY)

EXTRACTION_PROMPT = """You are parsing university course syllabus images for a question-bank tagging system.
A single API call may contain images from MULTIPLE subjects, and a single subject's 
syllabus may span MULTIPLE consecutive images (pages). Your first job is to correctly 
group images by subject; your second job is to extract fine-grained, atomic topic tags 
per unit.

INPUT HANDLING
- Images are provided in order.
- A NEW subject begins on an image that shows a subject code (pattern like "22CSE19", 
  "22ITC10N" — typically 2 digits + 2-4 letters + digits, top-left of the page) 
  immediately followed by a course title.
- subject code dosen't contains any spacing in between.
- Any image that does NOT begin with a new subject code is a CONTINUATION of the subject 
  immediately preceding it — keep appending its units to that same subject, do not start 
  a new entry.
- If one unit's content is split across two images (e.g. UNIT-III starts at the bottom of 
  page 2 and continues at the top of page 3), merge them into ONE unit entry — never 
  create two entries for the same unit number under the same subject.
- If the last visible line on one image and the first visible line on the next image are 
  the same fragment repeated (common with overlapping scans), extract it only once.
- If a topic phrase is visibly cut off at a page edge, reconstruct the full phrase from 
  both images before extracting it — never output a broken half-phrase as its own topic.

EXTRACTION TASK (per subject, per unit)
Extract every distinct topic as a separate string in "topics". These are short tags a 
question could be labeled with — not full syllabus sentences.

SPLITTING RULES
- Commas, semicolons, and dashes used as separators all introduce new topics. A dash with 
  spaces around it ("- " or " -") between phrases is a separator. A dash inside a single 
  word with no spaces ("well-known", "point-to-point") is part of the word — never split 
  those.
- HEADING-WITH-CHILDREN: when a general heading is immediately followed by its own 
  specific sub-items (via colon or dash), extract ONLY the sub-items, not the heading 
  itself.
  Example: "Reference Models- The OSI Reference Model- the TCP/IP Reference Model" 
  → ["OSI reference model", "TCP/IP reference model"]  (drop "reference models")
- CONTEXT PRESERVATION: if a sub-item is generic on its own (e.g. "Design Issues", 
  "Services", "Applications"), prefix it with its parent concept so it stays searchable.
  Example: "Data Link Layer: Design Issues" → "data link layer design issues"
  But: "Guided Transmission Media" stays as-is — already specific without a prefix.
- Do not join two distinct concepts/techniques with "and" — split them.
  Example: "Circuit Switching and Virtual Circuit Switching" 
  → ["circuit switching", "virtual circuit switching"]
- Keep genuinely comparative pairs as ONE topic (splitting would lose the comparison).
  Example: "A Comparison of the OSI and TCP/IP Reference Model" 
  → "OSI vs TCP/IP reference models"
  Example: "Hadoop 1 vs Hadoop 2" stays as one topic.
- Each topic should read as a short noun phrase, ideally 2-6 words — not a clause.

NORMALIZATION RULES
- Lowercase all topics EXCEPT standard acronyms/proper nouns, which keep their usual form 
  (HDFS, YARN, MapReduce, Hadoop, Spark, Hive, Pig, SQL, RDBMS, ETL, ELT, OSI, TCP/IP, TCP, 
  UDP, IP, DNS, SMTP, FTP, TELNET, SNMP, BGP, OSPF).
- Expand version numbers to standard short form: "IP Version 4" → "IPv4".
- Strip filler/structural words that aren't part of the technical term itself 
  ("Introduction to", "Overview of", "Typical", trailing periods).
- Do not invent topics not stated or clearly implied in the text.
- Do not duplicate a topic within the same unit.

EXCLUSIONS
Ignore non-unit content on any page: course title, instruction hours, SEE/CIE marks, 
credits, prerequisites, course objectives, course outcomes, CO-PO articulation matrix.

WORKED EXAMPLE 1 (comma-separated list)
Given: "Introduction to Big Data: Data and its types: Unstructured, Semi-structured, 
Structured – Sources of data – Evolution and Definition of Big Data – 
Characteristics(3Vs/5Vs) and Challenges – Need for Big Data, Big data integration 
process, Applications."

Correct topics:
["unstructured data", "semi-structured data", "structured data", "sources of data", 
"evolution of big data", "definition of big data", "3Vs of big data", "5Vs of big data", 
"challenges of big data", "need for big data", "big data integration process", 
"big data applications"]

WORKED EXAMPLE 2 (dash-chained heading pattern)
Given: "Introduction: Network Hardware- Network Topologies- Reference Models- The OSI 
Reference Model- the TCP/IP Reference Model- A Comparison of the OSI and TCP/IP 
Reference Model- Packet Switching, Circuit switching and virtual circuit switching."

Correct topics:
["network hardware", "network topologies", "OSI reference model", "TCP/IP reference model", 
"OSI vs TCP/IP reference models", "packet switching", "circuit switching", 
"virtual circuit switching"]

Note: "Introduction" and "Reference Models" were dropped as bare category labels since 
their specific children were listed. Apply this same granularity throughout.

OUTPUT
Return only JSON matching this structure — no markdown fences, no commentary:
{
  "subjects": [
    {
      "subject_code": string,
      "units": [
        {"unit": int, "topics": [string, ...]}
      ]
    }
  ]
}
One entry per distinct subject found across all provided images, in the order each 
subject code first appears. Every unit and topic for that subject must be merged under 
its single entry, even if the subject's pages were non-contiguous in the input.
"""

def pdf_to_images(pdf_path, output_path, dpi=300):
    os.makedirs(output_path, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img_path = os.path.join(output_path, f"page_{page.number+1}.png")
        pix.save(img_path)
        images.append(img_path)
    return images 

def extract_topics(image_paths):
    uploaded_pages = []
    for img_path in image_paths:
        uploaded_file = client.files.upload(file=img_path)
        uploaded_pages.append(uploaded_file)

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite", 
        contents=[
            *uploaded_pages,
            types.Part.from_text(text=EXTRACTION_PROMPT),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedSyllabusTopics,
            temperature=0.0, 
        ),
    )
    return response

def add_to_json(file_path, subject_code, new_data):
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        with open(file_path, 'r') as file:
            try:
                data = json.load(file)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}

    data[subject_code] = new_data

    with open(file_path, 'w') as file:
        json.dump(data, file, indent=4)

def get_sujbect_code(name: str):
    i = 0
    subject_code = ""
    while name[i] != '_':
      subject_code += name[i]
      i += 1
    return subject_code

def extractor():
    for regulation in target_folder.iterdir():
      for pdf_path in regulation.glob("*.pdf"):
          base_name = pdf_path.stem
          regulation_code = f'{regulation.name}'
          output_file_path = f'pipelines/extracted_topics/{regulation_code}.json'
          images_output_folder = f'pipelines/topic_images/{regulation_code}/{base_name}'
          subject_code = get_sujbect_code(base_name)
          
          print(f"Processing: {regulation_code}/{base_name}")
          image_paths = pdf_to_images(pdf_path=pdf_path, output_path=images_output_folder)
          
          response = extract_topics(image_paths)
          results: ExtractedSyllabusTopics | None = response.parsed
          
          for result in results.syllabus_topics:
            units_dict = [unit.model_dump() for unit in result.units]
            add_to_json(output_file_path, subject_code=subject_code, new_data=units_dict)

if __name__ == "__main__":
    extractor()