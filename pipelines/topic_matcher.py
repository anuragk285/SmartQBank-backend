import json, os
from typing import List
from pydantic import BaseModel
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

client = genai.Client(api_key=GEMINI_API_KEY)

class ExtractedQuestion(BaseModel):
    unit: int
    question_id: int
    topic: str

class ExtractedQuestionList(BaseModel):
    questions: List[ExtractedQuestion]


def encode_topics(topics: list[str], unit: int):
    encoded_topics: list[str] = []
    for topic in topics:
        new_topic = f'U{unit}::{topic}'
        encoded_topics.append(new_topic)
    return encoded_topics


def load_encoded_topics(regulation_code: str, subject_code: str):
    all_encoded_topics = []
    try:
        with open(f'pipelines/extracted_topics/{regulation_code}.json') as f:
            data = json.load(f)
        units = data[f'{subject_code}']
        for unit in units:
            encoded_topics = encode_topics(topics=unit['topics'], unit=unit['unit'])
            all_encoded_topics.extend(encoded_topics)
    except Exception as e:
        print(f"ERROR OCCURED at load_encoded_topics: {e}")
        raise
    return all_encoded_topics


def encode_questions(questions: list[str]):
    encoded_questions: list[str] = []
    for i, q in enumerate(questions):
        encoded_question = f'Q{i}::{q}'
        encoded_questions.append(encoded_question)
    return encoded_questions  # was missing — this silently returned None before


TOPIC_MATCH_SYSTEM_PROMPT = """You are a curriculum-mapping assistant. You match exam questions to the syllabus topic they are testing.

INPUT FORMAT
- TOPICS: one per line, formatted as U<unit_number>::<topic_name>
  e.g. "U2::Binary Search Trees" means the topic "Binary Search Trees" belongs to Unit 2.
- QUESTIONS: one per line, formatted as Q<question_id>::<question_text>
  e.g. "Q7::Explain how a BST maintains O(log n) search." means this question's id is 7.

TASK
Assign every question to exactly one topic from TOPICS — whichever topic a subject teacher would file that question under, based on meaning, not shared keywords.

RULES
1. Return exactly one entry per question_id. Never omit a question — if no topic is a perfect fit, pick the closest one.
2. Only use topics that appear verbatim in TOPICS. Never invent, merge, or reword a topic.
3. "topic" in your output must be the exact topic text with the "U<unit>::" prefix stripped — copy it character-for-character, do not paraphrase.
4. "unit" is the integer parsed from that topic's own "U<unit>::" prefix (not the question's index).
5. "question_id" is the integer parsed from the question's "Q<id>::" prefix.

Example: if TOPICS contains "U2::Binary Search Trees" and a question asks about the time complexity of searching a balanced BST, its unit is 2 and its topic is "Binary Search Trees" — copied exactly, not shortened or reworded."""


def build_matching_prompt(encoded_topics: list[str], encoded_questions: list[str]) -> str:
    topics_block = "\n".join(encoded_topics)
    questions_block = "\n".join(encoded_questions)
    return f"TOPICS:\n{topics_block}\n\nQUESTIONS:\n{questions_block}"

def match_topics(regulation_code, subject_code, questions: list[str]):
    encoded_topics = load_encoded_topics(regulation_code=regulation_code, subject_code=subject_code)
    encoded_questions = encode_questions(questions)
    prompt = build_matching_prompt(encoded_topics, encoded_questions)
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=[prompt],
        config=types.GenerateContentConfig(
            system_instruction=TOPIC_MATCH_SYSTEM_PROMPT,
            response_mime_type='application/json',
            response_schema=ExtractedQuestionList,
            temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        )
    )

    result = response.parsed
    if result is None:
        result = ExtractedQuestionList.model_validate(json.loads(response.text))
        print("ERROR OCCURED, RESULT IS NONE at match_topics")
    matched_questions = []
    result_dict = result.model_dump()
    for i, q in enumerate(result_dict['questions']):
        matched_question = {}
        matched_question['unit'] = q['unit']
        matched_question['topic'] = q['topic']
        matched_question['question'] = questions[i]
        matched_questions.append(matched_question)
    return matched_questions

    
