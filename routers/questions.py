from fastapi import APIRouter, Depends, HTTPException, Query
from schemas import QuestionResponse, PaginatedQuestions
from models import Question, Subject, Topic
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from typing import List, Optional
from sqlalchemy import case, asc, desc, select, func
import json, hashlib
from redis_fastapi import CacheBackendDep

router = APIRouter(
    prefix = "/api/subjects",
    tags = ["Questions"]
)

DIFFICULTY_ORDER = case(
    {"Easy": 1, "Medium": 2, "Hard": 3},
    value=Question.difficulty
)

SORTABLE_COLUMNS = {
    "marks": Question.marks,
    "difficulty": DIFFICULTY_ORDER,
    "unit": Question.unit 
}

def build_cache_key(**kwargs) -> str:
    normalized = {
        k: (sorted(v) if isinstance(v, list) else v)
        for k, v in kwargs.items()
    }
    raw = json.dumps(normalized, sort_keys=True, default=str)
    return "questions:" + hashlib.md5(raw.encode()).hexdigest()


@router.get("/{subject_id}/questions", response_model=PaginatedQuestions)
async def get_all_questions(subject_id: int,
                      cache: CacheBackendDep,
                      topic_ids: Optional[List[int]] = Query(None),
                      units: Optional[List[int]] = Query(None),
                      difficulty: Optional[List[str]] = Query(None),
                      marks: Optional[List[int]] = Query(None),
                      sort_by: Optional[str] = None,
                      sort_order: str = Query("asc", pattern="^(asc|desc)$"),
                      page: int = Query(1, ge=1),
                      page_size: int = Query(10, ge=1, le=50),
                      db: AsyncSession = Depends(get_db)):
    try:
        cache_key = build_cache_key(
            subject_id=subject_id, topic_ids=topic_ids, units=units, difficulty=difficulty,
            marks=marks, sort_by=sort_by, sort_order=sort_order, page=page, page_size=page_size,
        )
        cached = await cache.get(cache_key, eviction_group="questions")
        if cached is not None:
            return cached
    except Exception as e:     
        print(f"Cache error")
    stmt = select(Question).join(Subject, Question.subject_code == Subject.subject_code).where(Subject.id == subject_id)
    if topic_ids:
        stmt = stmt.where(Question.topic_id.in_(topic_ids))
    if units:
        stmt = stmt.where(Question.unit.in_(units))
    if marks:
        stmt = stmt.where(Question.marks.in_(marks))
    if difficulty:
        stmt = stmt.where(Question.difficulty.in_(difficulty))
    
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()
    if sort_by:
            column = SORTABLE_COLUMNS.get(sort_by.lower())
            if column is None:
                raise HTTPException(status_code=400, detail=f"Cannot sort by '{sort_by}'")
            stmt = stmt.order_by(desc(column) if sort_order == "desc" else asc(column))
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    questions = result.scalars().all()

    unique_topic_ids = {q.topic_id for q in questions if q.topic_id is not None}
    topic_map = {}
    if unique_topic_ids:
        topic_stmt = select(Topic.id, Topic.topic).where(Topic.id.in_(unique_topic_ids))
        topic_res = await db.execute(topic_stmt)
        topic_map = {id: topic for id, topic in topic_res.all()}

    formatted_questions = []
    for q in questions:
        q_data = {
            column.name: getattr(q, column.name) 
            for column in q.__table__.columns
        }
        q_data["topic"] = topic_map.get(q.topic_id)
        formatted_questions.append(QuestionResponse.model_validate(q_data).model_dump())
    result = {
        "questions": formatted_questions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
    }
    try:
        await cache.set(cache_key, result, ttl=300, eviction_group="questions")
    except Exception as e:
        print(f"Cache set error: {e}")
    return result

@router.get('/{subject_id}/topic-questions', response_model=List[QuestionResponse])
async def get_questions_on_topic(subject_id: int, topic_ids: Optional[List[int]] = Query(default=None), db: AsyncSession = Depends(get_db)):
    if not topic_ids:
        return []
    stmt = select(Question).where(Question.topic_id.in_(topic_ids))
        
    result = await db.execute(stmt)
    questions = result.scalars().all()

    unique_topic_ids = {q.topic_id for q in questions if q.topic_id is not None}
    topic_map = {}
    if unique_topic_ids:
        topic_stmt = select(Topic.id, Topic.topic).where(Topic.id.in_(unique_topic_ids))
        topic_res = await db.execute(topic_stmt)
        topic_map = {id: topic for id, topic in topic_res.all()}

    formatted_questions = []
    for q in questions:
        q_data = {
            column.name: getattr(q, column.name) 
            for column in q.__table__.columns
        }
        q_data["topic"] = topic_map.get(q.topic_id)
        formatted_questions.append(QuestionResponse.model_validate(q_data).model_dump())

    return formatted_questions
