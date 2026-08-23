from collections import defaultdict
from typing import List
import os

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Query
from google import genai
from google.genai import types
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Topic, Subject, CanonicalTopic, Question, SubjectContent, AITopicDescription
from schemas import TopicResponse, TopicDescription

import logging
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix='/api',
    tags=["Topics"]
)

load_dotenv()

SYSTEM_PROMPT = """You generate structured study content for a web app that
CBIT Hyderabad engineering students use for last-minute exam revision.

Your output is rendered directly as a study card in the app UI. It is never
shown as a chat message and never read as conversation, so it must contain
only exam-relevant content — no greetings, no preamble, no meta-commentary
about yourself or the task, no closing remarks, and no filler phrases like
"it is important to note that" or "in conclusion".

You will be given a subject, a unit, and a topic. Produce content a student
can revise from in under two minutes.

Field rules:
- summary: 2-3 sentences giving the core definition. Always required.
- key_points: 3-6 short bullets (each under 15 words) of facts the student
  must remember. Include only if the topic has genuinely separable facts.
- table: include ONLY if the topic is inherently a comparison between two
  or more things (e.g. "OSI vs TCP/IP"). Omit for non-comparative topics.
- mermaid_diagram: include this for any topic that has a sequence,
  multi-step process, protocol exchange, algorithm, or architecture with
  interacting parts — err toward including it whenever the topic has 3 or
  more ordered steps or components. Only omit it for topics that are a
  single flat definition with no internal steps (e.g. "primary key",
  "atomicity"). When you include it, follow these syntax rules exactly:
  - Use "flowchart TD" for steps/stages, "sequenceDiagram" for exchanges
    between named parties.
  - 3-6 nodes maximum. Each node label under 4 words.
  - Every node must use bracket syntax, e.g. A[Client sends request] --> B[Server processes].
  - Never use parentheses, colons, or quotation marks inside a node label —
    plain words only, they break the parser.
- formula: include ONLY if there's a specific formula or precise technical
  definition worth highlighting on its own.
- exam_tip: one sentence on how this topic is typically asked. Include
  only if you're confident about the pattern — omit rather than guess.

Do not invent facts, formulas, or university-specific details you're not
certain about. Leave a field out entirely rather than fabricate content for it."""


def get_genai_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")
    return genai.Client(api_key=api_key)


async def generate_topic_description_async(subject_name: str, topic_name: str) -> TopicDescription:
    client = get_genai_client()
    response = await client.aio.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=f"Subject: {subject_name}\nTopic: {topic_name}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=TopicDescription,
        ),
    )
    if response.parsed is None:
        raise ValueError(f"Gemini returned no valid structured output for: {topic_name}")
    return response.parsed


@router.get("/topics/{subject_id}", response_model=List[TopicResponse])
async def get_topics(subject_id: int, db: AsyncSession = Depends(get_db)):
    stmt1 = select(Subject).where(Subject.id == subject_id)
    result1 = await db.execute(stmt1)
    subject_in_db = result1.scalar_one_or_none()
    if not subject_in_db:
        raise HTTPException(status_code=404, detail=f"Subject {subject_id} not found")
        
    stmt2 = select(Topic).where(Topic.subject_code == subject_in_db.subject_code)
    result2 = await db.execute(stmt2)
    return result2.scalars().all()


@router.get("/topics/{subject_id}/important-topics")
async def get_important_topics(
    subject_id: int,
    crossRegulation: bool = Query(
        True,
        description="If true, aggregate across every regulation in the subject's group. "
                    "If false, scope to just this subject's own regulation.",
    ),
    db: AsyncSession = Depends(get_db),
):
    subject = await db.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")

    if subject.subject_content_id is None:
        raise HTTPException(status_code=400, detail="Subject has no subject_content mapping")

    subject_content = await db.get(SubjectContent, subject.subject_content_id)
    if subject_content is None:
        raise HTTPException(status_code=400, detail="Subject has no subject_content mapping")

    if crossRegulation:
        group_id = subject_content.subject_group_id
        if group_id is None:
            raise HTTPException(status_code=400, detail="Subject has no subject_group mapping")
        scope_filter = SubjectContent.subject_group_id == group_id
    else:
        scope_filter = SubjectContent.id == subject_content.id

    total_papers_stmt = (
        select(func.count(func.distinct(
            func.concat(Question.year, "_", Topic.regulation_code)
        )))
        .select_from(Question)
        .join(Topic, Topic.id == Question.topic_id)
        .join(SubjectContent, SubjectContent.id == Topic.subject_content_id)
        .where(scope_filter)
    )
    total_papers = (await db.execute(total_papers_stmt)).scalar() or 1
    if total_papers == 0:
        return {"total_papers_analyzed": 0, "topics": []}

    group_key = func.coalesce(Topic.canonical_topic_id, -Topic.id)
    topic_label = func.coalesce(CanonicalTopic.label, func.min(Topic.topic))

    stmt = (
        select(
            group_key.label("group_key"),
            topic_label.label("topic_label"),
            func.count(Question.id).label("question_count"),
            func.coalesce(func.sum(Question.marks), 0).label("total_marks"),
            func.count(func.distinct(
                func.concat(Question.year, "_", Topic.regulation_code)
            )).label("years_appeared"),
        )
        .select_from(Topic)
        .join(SubjectContent, SubjectContent.id == Topic.subject_content_id)
        .join(Question, Question.topic_id == Topic.id)
        .outerjoin(CanonicalTopic, CanonicalTopic.id == Topic.canonical_topic_id)
        .where(scope_filter)
        .group_by(group_key, CanonicalTopic.label)
        .order_by(func.coalesce(func.sum(Question.marks), 0).desc())
    )

    rows = (await db.execute(stmt)).all()
    
    topic_id_stmt = (
        select(
            func.coalesce(Topic.canonical_topic_id, -Topic.id).label("group_key"),
            Topic.id,
        )
        .select_from(Topic)
        .join(SubjectContent, SubjectContent.id == Topic.subject_content_id)
        .where(scope_filter)
    )
    topic_id_rows = (await db.execute(topic_id_stmt)).all()

    topic_ids_by_group: dict[int, list[int]] = defaultdict(list)
    for r in topic_id_rows:
        topic_ids_by_group[r.group_key].append(r.id)

    return {
        "cross_regulation": crossRegulation,
        "total_papers_analyzed": total_papers,
        "topics": [
            {
                "topic": r.topic_label,
                "question_count": r.question_count,
                "years_appeared": r.years_appeared,
                "avg_marks_per_paper": round(r.total_marks / total_papers, 1),
                "topic_ids": topic_ids_by_group[r.group_key],
            }
            for r in rows
        ],
    }


@router.get("/topics/{topic_id}/ai-description", response_model=TopicDescription)
async def get_ai_description(topic_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Topic)
        .options(selectinload(Topic.subject_content))
        .where(Topic.id == topic_id)
    )
    result = await db.execute(stmt)
    topic = result.scalar_one_or_none()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    # 2. Check cache
    stmt_cache = select(AITopicDescription).where(
        AITopicDescription.topic_id == topic_id
    )
    result_cache = await db.execute(stmt_cache)
    cached = result_cache.scalar_one_or_none()
    if cached:
        return TopicDescription.model_validate(cached.payload)

    # 3. Generate description using native async client

    try:
        subject_name = topic.subject_content.name if topic.subject_content else ""

        ai_result = await generate_topic_description_async(
            subject_name=subject_name,
            topic_name=topic.topic,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("AI generation failed for topic_id=%s", topic_id)
        raise HTTPException(status_code=502, detail=f"AI Generation failed: {str(e)}")

    # 4. Save to cache with collision safety
    try:
        db.add(AITopicDescription(topic_id=topic_id, payload=ai_result.model_dump()))
        await db.commit()
    except IntegrityError:
        await db.rollback()

    return ai_result