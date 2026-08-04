from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from sqlalchemy.orm import Session
from models import Topic, Subject
from schemas import TopicResponse
from typing import List
router = APIRouter(
    prefix='/api',
    tags=["Topics"]
)

@router.get("/topics/{subject_id}", response_model=List[TopicResponse])
def get_topics(subject_id: int, db: Session = Depends(get_db)):
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    topics = db.query(Topic).filter(Topic.subject_code == subject.subject_code).all()
    return topics

@router.get("/topic/{topics_id}", response_model=TopicResponse)
def get_topic(topic_id: int, db: Session = Depends(get_db)):
    db_topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if db_topic:
        return db_topic
    else:
        raise HTTPException(status_code=404, detail=f"TOPIC {topic_id} NOT FOUND")

