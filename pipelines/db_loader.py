from database import SessionLocal, engine
from sqlalchemy.orm import Session
from fastapi import HTTPException
from models import Question, Subject, PaperAppearances, Base
from schemas import QuestionCreate, SubjectCreate, PaperAppearancesCreate
import json

def load_question(question: QuestionCreate, db: Session): 
    db_question = db.query(Question).filter(Question.text == question.text, Question.year == question.year).first()
    if db_question: 
        raise HTTPException(status_code=409, detail=f"\nQUESTION {question.text} ALREADY EXISTS\n")
    new_question = Question(**question.model_dump())
    db.add(new_question)
    db.commit()
    db.refresh(new_question)
    return new_question

def load_subject(subject: SubjectCreate, db: Session):
    db_subject = db.query(Subject).filter(Subject.subject_code == subject.subject_code, Subject.department == subject.department).first()
    if db_subject:
        raise HTTPException(status_code=409, detail=f"SUBJECT {subject.subject_code} ALREADY EXISTS\n")
    new_subject = Subject(**subject.model_dump())
    db.add(new_subject)
    db.commit()
    db.refresh(new_subject)

def load_paper_appearance(appearance: PaperAppearancesCreate, db: Session):
    db_appearance = db.query(PaperAppearances).filter(PaperAppearances.question_id == appearance.question_id).first()
    if db_appearance:
        raise HTTPException(status_code=409, detail=f"PAPER APPEARANCE qid: {appearance.question_id} ALREADY EXISTS")
    new_appearance = PaperAppearances(**appearance.model_dump())
    db.add(new_appearance)
    db.commit()
    db.refresh(new_appearance)

def parse_difficulty(difficulty_level: int):
    if 0 < difficulty_level <= 2: return 'Easy'
    elif difficulty_level <= 4: return 'Medium'
    elif difficulty_level <= 6: return 'Hard'
    return 'None'


def db_loader(path='pipelines/extracted_data.json'):
    Base.metadata.create_all(bind=engine)
    with open(path, 'r', encoding='utf-8') as f:
        clean_lines = [
            line for line in f 
            if not line.lstrip().startswith('//') and not line.lstrip().startswith('#')
        ]
        data = json.loads(''.join(clean_lines))
    with SessionLocal() as db:
        for paper in data['papers']:
            subject = paper['subject']
            departments = subject['department'].split(', ')
            for dept in departments:
                subj = SubjectCreate(subject_code=subject['subject_code'],
                                    semester=subject['semester'],
                                    department=dept,
                                    name=subject['name'],
                                    regulation_code=subject['regulation_code'])
                try:
                    load_subject(subject=subj, db=db)
                except HTTPException as e:
                    if e.status_code == 409:
                        print(f"ERROR: {e.detail} SKIPPING {subj.subject_code}\n\n")
            question_list = paper['questions']
            paper_info = paper['paperInfo']
            for q in question_list:
                question = QuestionCreate(text=q['text'],
                                        subject_code=q['subject_code'],
                                        difficulty=parse_difficulty(q['difficulty_level']),
                                        unit=q['unit'],
                                        year=q['year'],
                                        marks=q['marks'],
                                        image_urls=q['image_urls'],
                                        topic = q['topic'])
                
                try:
                    db_question = load_question(question=question, db=db)
                except HTTPException as e:
                    if e.status_code == 409:
                        print(f"Failed to load question '{q['text'][:20]}...': {e}")
                        continue
                if db_question:
                    q_id = db_question.id
                    appearance = PaperAppearancesCreate(year=paper_info['year'],
                                                        paper_name=paper_info['paper_name'],
                                                        question_id=q_id)
                    try:
                        load_paper_appearance(appearance=appearance, db=db)
                    except HTTPException as e:
                        if e.status_code == 409:
                            print(f"ERROR: {e.detail} SKIPPING appearance of qid: {appearance.question_id}")

db_loader()