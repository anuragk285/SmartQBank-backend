from fastapi import APIRouter, Depends, HTTPException, Query
from schemas import QuestionResponse, PaginatedQuestions
from models import Question, Subject
from sqlalchemy.orm import Session
from database import get_db
from typing import List, Optional
from sqlalchemy import case, asc, desc
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
                      topic: Optional[str] = None,
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
            subject_id=subject_id, topic=topic, units=units, difficulty=difficulty,
            marks=marks, sort_by=sort_by, sort_order=sort_order, page=page, page_size=page_size,
        )
        cached = await cache.get(cache_key, eviction_group="questions")
        if cached is not None:
            return cached
    except Exception as e:     
        print(f"Cache error")
    query = (db.query(Question).join(Subject, Question.subject_code == Subject.subject_code).filter(Subject.id == subject_id))
    if topic:
        query = query.filter(Question.topic == topic)
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

    result = {
        "questions": [QuestionResponse.model_validate(q).model_dump() for q in questions],
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

