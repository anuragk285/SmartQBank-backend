from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from models import Topic, Subject, CanonicalTopic, Question
from sqlalchemy import select, func
from schemas import TopicResponse
from typing import List
router = APIRouter(
    prefix='/api',
    tags=["Topics"]
)

@router.get("/topics/{subject_id}", response_model=List[TopicResponse])
async def get_topics(subject_id: int, db: AsyncSession = Depends(get_db)):
    stmt1 = select(Subject).where(Subject.id == subject_id)
    result1 = await db.execute(stmt1)
    subject_in_db = result1.scalar_one_or_none()
    if not subject_in_db:
        raise HTTPException(status_code=404, detail=f"SUBJECT {subject_id} NOT FOUND")
    stmt2 = select(Topic).where(Topic.subject_code == subject_in_db.subject_code)
    result2 = await db.execute(stmt2)
    topics = result2.scalars().all()
    return topics

@router.get("/topics/{subject_id}/important-topics")
async def get_important_topics(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
):
    subject = await db.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")

    group_key = func.coalesce(Topic.canonical_topic_id, -Topic.id)
    topic_label = func.coalesce(CanonicalTopic.label, func.min(Topic.topic))

    stmt = (
        select(
            group_key.label("group_key"),
            topic_label.label("topic_label"),
            func.count(Question.id).label("question_count"),
            func.coalesce(func.sum(Question.marks), 0).label("total_marks"),
            func.count(func.distinct(Question.year)).label("years_appeared"),
        )
        .select_from(Topic)
        .join(Question, Question.topic_id == Topic.id)
        .outerjoin(CanonicalTopic, CanonicalTopic.id == Topic.canonical_topic_id)
        .where(Topic.subject_content_id == subject.subject_content_id)
        .group_by(group_key, CanonicalTopic.label)
        .order_by(func.count(Question.id).desc())
    )

    rows = (await db.execute(stmt)).all()
    total_questions = sum(r.question_count for r in rows)

    return [
        {
            "topic": r.topic_label,
            "question_count": r.question_count,
            "total_marks": r.total_marks,
            "years_appeared": r.years_appeared,
            "weightage_percent": round(r.question_count * 100 / total_questions, 1) if total_questions else 0,
        }
        for r in rows
    ]