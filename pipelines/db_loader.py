from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from fastapi import HTTPException
from sqlalchemy import select
from models import Question, Subject, PaperAppearances, Base, Topic
from schemas import QuestionCreate, SubjectCreate, PaperAppearancesCreate
import json, os, asyncio
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "smart-question-bank-database.db")

DATABASE_URL = f"sqlite+aiosqlite:///{os.path.abspath(DB_PATH)}"
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def load_question(question: QuestionCreate, db: AsyncSession): 
    stmt = select(Question).where(Question.text == question.text, Question.year == question.year)
    #db_question = db.query(Question).filter(Question.text == question.text, Question.year == question.year).first()
    result = await db.execute(stmt)
    db_question = result.scalar_one_or_none()
    if db_question: 
        raise HTTPException(status_code=409, detail=f"\nQUESTION {question.text} ALREADY EXISTS\n")
    new_question = Question(**question.model_dump())
    db.add(new_question)
    await db.commit()
    await db.refresh(new_question)
    return new_question

async def load_subject(subject: SubjectCreate, db: AsyncSession):
    stmt = select(Subject).where(Subject.subject_code == subject.subject_code, Subject.department == subject.department)
    result = await db.execute(stmt)
    db_subject = result.scalar_one_or_none()
    #db_subject = db.query(Subject).filter(Subject.subject_code == subject.subject_code, Subject.department == subject.department).first()
    if db_subject:
        raise HTTPException(status_code=409, detail=f"SUBJECT {subject.subject_code} ALREADY EXISTS\n")
    new_subject = Subject(**subject.model_dump())
    db.add(new_subject)
    await db.commit()
    await db.refresh(new_subject)

async def load_paper_appearance(appearance: PaperAppearancesCreate, db: AsyncSession):
    #db_appearance = db.query(PaperAppearances).filter(PaperAppearances.question_id == appearance.question_id).first()
    stmt = select(PaperAppearances).where(PaperAppearances.question_id == appearance.question_id)
    result = await db.execute(stmt)
    db_appearance = result.scalar_one_or_none()
    if db_appearance:
        raise HTTPException(status_code=409, detail=f"PAPER APPEARANCE qid: {appearance.question_id} ALREADY EXISTS")
    new_appearance = PaperAppearances(**appearance.model_dump())
    db.add(new_appearance)
    await db.commit()
    await db.refresh(new_appearance)

def parse_difficulty(difficulty_level: int):
    if 0 < difficulty_level <= 2: return 'Easy'
    elif difficulty_level <= 4: return 'Medium'
    elif difficulty_level <= 6: return 'Hard'
    return 'None'

async def get_topic_id(topic_name: str, subject_code: str, db: AsyncSession):
    stmt = select(Topic).where(Topic.topic == topic_name, Topic.subject_code == subject_code)
    result = await db.execute(stmt)
    
    # Fetch the result directly without looping first
    db_topic = result.scalars().first()
    
    if db_topic:
        # Optional: Print for debugging
        print("#"*30)
        print(db_topic.topic)
        print("#"*30)
        return db_topic.id
    else:
        # Now safely prints the original string parameter
        print(f"TOPIC '{topic_name}' NOT FOUND IN DATABASE")
        return None

async def db_loader(path='pipelines/extracted_data.json'):
    with open(path, 'r', encoding='utf-8') as f:
        clean_lines = [
            line for line in f 
            if not line.lstrip().startswith('//') and not line.lstrip().startswith('#')
        ]
        data = json.loads(''.join(clean_lines))
    async with AsyncSessionLocal() as db:
        for paper in data['papers']:
            subject = paper['subject']
            departments = subject['department'].split(', ')
            for dept in departments:
                subj = SubjectCreate(subject_code=subject['subject_code'],
                                    semester=subject['semester'],
                                    department=dept,
                                    name=subject['name'],
                                    regulation_code=subject['regulation_code'],
                                    subject_content_id=None
                                    )
                try:
                    await load_subject(subject=subj, db=db)
                except HTTPException as e:
                    if e.status_code == 409:
                        print(f"ERROR: {e.detail} SKIPPING {subj.subject_code}\n\n")
            question_list = paper['questions']
            paper_info = paper['paperInfo']
            for q in question_list:
                topic_id = await get_topic_id(q['topic'], q['subject_code'], db)
                if topic_id is None:
                    print(f"SKIPPING question '{q['text'][:40]}...' — no matching topic for {q['subject_code']}")
                    continue
                question = QuestionCreate(text=q['text'],
                                        subject_code=q['subject_code'],
                                        difficulty=parse_difficulty(q['difficulty_level']),
                                        unit=q['unit'],
                                        year=q['year'],
                                        marks=q['marks'],
                                        image_urls=q['image_urls'],
                                        topic_id = topic_id)
                
                try:
                    db_question = await load_question(question=question, db=db)
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
                        await load_paper_appearance(appearance=appearance, db=db)
                    except HTTPException as e:
                        if e.status_code == 409:
                            print(f"ERROR: {e.detail} SKIPPING appearance of qid: {appearance.question_id}")

asyncio.run(db_loader())