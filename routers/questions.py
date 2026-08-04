from fastapi import APIRouter, Depends, HTTPException, Query
from schemas import QuestionResponse, PaginatedQuestions
from models import Question, Subject
from sqlalchemy.orm import Session
from database import get_db
from typing import List, Optional
from sqlalchemy import case, asc, desc
import json, hashlib
from redis_fastapi import CacheBackendDep
from routers.topics import get_topic


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
                      db: Session = Depends(get_db)):
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
    query = (db.query(Question).join(Subject, Question.subject_code == Subject.subject_code).filter(Subject.id == subject_id))
    if topic_ids:
        query = query.filter(Question.topic_id.in_(topic_ids))
    if units:
        query = query.filter(Question.unit.in_(units))
    if marks:
        query = query.filter(Question.marks.in_(marks))
    if difficulty:
        query = query.filter(Question.difficulty.in_(difficulty))

    if sort_by:
            column = SORTABLE_COLUMNS.get(sort_by.lower())
            if column is None:
                raise HTTPException(status_code=400, detail=f"Cannot sort by '{sort_by}'")
            query = query.order_by(desc(column) if sort_order == "desc" else asc(column))

    total = query.count()
    questions = query.offset((page - 1) * page_size).limit(page_size).all()
    formatted_questions = []
    unique_topic_ids = {getattr(q, "topic_id", None) for q in questions if getattr(q, "topic_id", None)}
    topic_map = {topic_id: get_topic(topic_id=topic_id, db=db).topic for topic_id in unique_topic_ids}
    for q in questions:
        q_data = q.__dict__.copy() if hasattr(q, "__dict__") else dict(q)
        topic_id = getattr(q, "topic_id", None)
        q_data["topic"] = topic_map.get(topic_id)
        q_data["topic_id"] = topic_id
        formatted_questions.append(QuestionResponse.model_validate(q_data).model_dump())
    result = {
        "questions": formatted_questions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    }
    try:
        await cache.set(cache_key, result, ttl=300, eviction_group="questions")
    except Exception as e:
        print(f"Cache set error: {e}")
    return result

