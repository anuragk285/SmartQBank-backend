from sqlalchemy import select, func
from database import SessionLocal
import asyncio
from models import Question, Topic
async def main():
    async with SessionLocal() as db:
        mismatched = await db.execute(
        select(Question.id, Question.subject_code, Topic.subject_code.label("topic_subject_code"))
        .join(Topic, Topic.id == Question.topic_id)
        .where(Question.subject_code != Topic.subject_code)
    )
    rows = mismatched.all()
    print(f"{len(rows)} mismatched questions found total")

    by_subject = {}
    for q_id, q_code, t_code in rows:
        by_subject.setdefault(q_code, set()).add(t_code)
    for q_code, t_codes in by_subject.items():
        print(q_code, "->", t_codes)

asyncio.run(main())