from fastapi import APIRouter, Depends, HTTPException
from schemas import SubjectResponse
from models import Subject, Question
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from database import get_db
from typing import List

router = APIRouter(
    prefix="/api/subjects", 
    tags=["Subjects"])

@router.get("/{department}/{semester}/{regulation_code}", response_model=List[SubjectResponse])
def get_subjects(department: str, semester: int, regulation_code: str, db: Session = Depends(get_db)):
    stmt = (select(Subject, func.count(Question.id).label("question_count"))
            .outerjoin(Question, Question.subject_code == Subject.subject_code)
            .where(Subject.department == department, Subject.semester == semester, Subject.regulation_code == regulation_code)
            .group_by(Subject.id))
    stmt_results = db.execute(stmt).all()
    results = [
        SubjectResponse(subject_code=subject.subject_code,
                        name=subject.name, id=subject.id,
                        department=subject.department,
                        semester=subject.semester,
                        regulation_code=subject.regulation_code,
                        question_count=count)
        for subject, count in stmt_results
    ]
    return results


@router.get("/{subject_id}", response_model=SubjectResponse)
def get_subject(subject_id: int, db: Session = Depends(get_db)):
    subject_in_db = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject_in_db:
        raise HTTPException(status_code=404, detail="SUBJECT NOT FOUND")
    return subject_in_db
