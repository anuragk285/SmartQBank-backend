from fastapi import APIRouter, Depends, HTTPException
from schemas import SubjectResponse
from models import Subject, Question
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from database import get_db
from typing import List

router = APIRouter(
    prefix="/api/subjects", 
    tags=["Subjects"])

@router.get("/{department}/{semester}/{regulation_code}", response_model=List[SubjectResponse])
async def get_subjects(department: str, semester: int, regulation_code: str, db: AsyncSession = Depends(get_db)):
    stmt = (select(Subject, func.count(Question.id).label("question_count"))
            .outerjoin(Question, and_(Question.subject_code == Subject.subject_code, Question.regulation_code == Subject.regulation_code))
            .where(Subject.regulation_code == regulation_code, Subject.department == department, Subject.semester == semester)
            .group_by(Subject.id))
    result = await db.execute(stmt)
    stmt_results = result.all()
    results = [
        SubjectResponse(subject_code=subject.subject_code,
                        name=subject.name, id=subject.id,
                        department=subject.department,
                        semester=subject.semester,
                        regulation_code=subject.regulation_code,
                        question_count=count,
                        subject_content_id=subject.subject_content_id)
        for subject, count in stmt_results
    ]
    return results


@router.get("/{subject_id}", response_model=SubjectResponse)
async def get_subject(subject_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Subject).where(Subject.id == subject_id)
    result = await db.execute(stmt)
    subject_in_db = result.scalar_one_or_none()
    if not subject_in_db:
        raise HTTPException(status_code=404, detail="SUBJECT NOT FOUND")
    return subject_in_db
