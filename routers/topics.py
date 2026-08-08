from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from models import Topic, Subject
from sqlalchemy import select
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
