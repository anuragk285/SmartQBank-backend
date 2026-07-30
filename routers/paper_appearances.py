from fastapi import APIRouter
from fastapi import APIRouter, Depends, HTTPException
from schemas import PaperAppearancesCreate, PaperAppearancesResponse
from models import PaperAppearances, Question
from sqlalchemy.orm import Session
from database import get_db
from typing import List

router = APIRouter(
    prefix = "/questions/{question_id}/appearances",
    tags = ["Paper Apperances"]
)

@router.get("/", response_model=List[PaperAppearancesResponse])
def get_appearances(question_id: int, db: Session = Depends(get_db)):
    appearances = db.query(Question).filter(Question.id == question_id).all()
    return appearances

