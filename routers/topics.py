from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from models import Topic, Subject, CanonicalTopic, Question, SubjectContent
from sqlalchemy import select, func
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

@router.get("/topics/{subject_id}/important-topics")
async def get_important_topics(
    subject_id: int,
    crossRegulation: bool = Query(
        True,
        description="If true, aggregate across every regulation in the subject's group. "
                    "If false, scope to just this subject's own regulation.",
    ),
    db: AsyncSession = Depends(get_db),
):
    subject = await db.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")

    if subject.subject_content_id is None:
        raise HTTPException(status_code=400, detail="Subject has no subject_content mapping")

    subject_content = await db.get(SubjectContent, subject.subject_content_id)
    if subject_content is None:
        raise HTTPException(status_code=400, detail="Subject has no subject_content mapping")

    if crossRegulation:
        group_id = subject_content.subject_group_id
        if group_id is None:
            raise HTTPException(status_code=400, detail="Subject has no subject_group mapping")
        scope_filter = SubjectContent.subject_group_id == group_id
    else:
        scope_filter = SubjectContent.id == subject_content.id

    total_papers_stmt = (
        select(func.count(func.distinct(
            func.concat(Question.year, "_", Topic.regulation_code)
        )))
        .select_from(Question)
        .join(Topic, Topic.id == Question.topic_id)
        .join(SubjectContent, SubjectContent.id == Topic.subject_content_id)
        .where(scope_filter)
    )
    total_papers = (await db.execute(total_papers_stmt)).scalar() or 1
    if total_papers == 0:
        return {"total_papers_analyzed": 0, "topics": []}

    group_key = func.coalesce(Topic.canonical_topic_id, -Topic.id)
    topic_label = func.coalesce(CanonicalTopic.label, func.min(Topic.topic))

    stmt = (
        select(
            group_key.label("group_key"),
            topic_label.label("topic_label"),
            func.count(Question.id).label("question_count"),
            func.coalesce(func.sum(Question.marks), 0).label("total_marks"),
            func.count(func.distinct(
                func.concat(Question.year, "_", Topic.regulation_code)
            )).label("years_appeared"),
        )
        .select_from(Topic)
        .join(SubjectContent, SubjectContent.id == Topic.subject_content_id)
        .join(Question, Question.topic_id == Topic.id)
        .outerjoin(CanonicalTopic, CanonicalTopic.id == Topic.canonical_topic_id)
        .where(scope_filter)
        .group_by(group_key, CanonicalTopic.label)
        .order_by(func.coalesce(func.sum(Question.marks), 0).desc())
    )

    rows = (await db.execute(stmt)).all()
    topic_id_stmt = (
        select(
            func.coalesce(Topic.canonical_topic_id, -Topic.id).label("group_key"),
            Topic.id,
        )
        .select_from(Topic)
        .join(SubjectContent, SubjectContent.id == Topic.subject_content_id)
        .where(scope_filter)
    )
    topic_id_rows = (await db.execute(topic_id_stmt)).all()

    topic_ids_by_group: dict[int, list[int]] = defaultdict(list)
    for r in topic_id_rows:
        topic_ids_by_group[r.group_key].append(r.id)

    return {
        "cross_regulation": crossRegulation,
        "total_papers_analyzed": total_papers,
        "topics": [
            {
                "topic": r.topic_label,
                "question_count": r.question_count,
                "years_appeared": r.years_appeared,
                "avg_marks_per_paper": round(r.total_marks / total_papers, 1),
                "topic_ids": topic_ids_by_group[r.group_key],
            }
            for r in rows
        ],
    }

# @router.get("/topics/{subject_id}/important-topics")
# async def get_important_topics(subject_id: int, db: AsyncSession = Depends(get_db)):
#     subject = await db.get(Subject, subject_id)
#     if subject is None:
#         raise HTTPException(status_code=404, detail="Subject not found")

#     sc_id = subject.subject_content_id

#     total_years_stmt = (
#         select(func.count(func.distinct(Question.year)))
#         .join(Topic, Topic.id == Question.topic_id)
#         .where(Topic.subject_content_id == sc_id)
#     )
#     total_years = (await db.execute(total_years_stmt)).scalar() or 1

#     group_key = func.coalesce(Topic.canonical_topic_id, -Topic.id)
#     topic_label = func.coalesce(CanonicalTopic.label, func.min(Topic.topic))

#     stmt = (
#         select(
#             group_key.label("group_key"),
#             topic_label.label("topic_label"),
#             Topic.unit.label("unit"),                       # real column now, not aggregated
#             func.count(Question.id).label("question_count"),
#             func.coalesce(func.sum(Question.marks), 0).label("total_marks"),
#             func.count(func.distinct(Question.year)).label("years_appeared"),
#         )
#         .select_from(Topic)
#         .join(Question, Question.topic_id == Topic.id)
#         .outerjoin(CanonicalTopic, CanonicalTopic.id == Topic.canonical_topic_id)
#         .where(Topic.subject_content_id == sc_id)
#         .group_by(group_key, CanonicalTopic.label, Topic.unit)   # unit added here
#         .order_by(func.coalesce(func.sum(Question.marks), 0).desc())
#     )

#     rows = (await db.execute(stmt)).all()

#     # topic_ids also grouped by (group_key, unit) to stay consistent with rows above
#     topic_id_stmt = (
#         select(
#             func.coalesce(Topic.canonical_topic_id, -Topic.id).label("group_key"),
#             Topic.unit,
#             Topic.id,
#         )
#         .where(Topic.subject_content_id == sc_id)
#     )
#     topic_id_rows = (await db.execute(topic_id_stmt)).all()

#     topic_ids_by_group: dict[tuple[int, int], list[int]] = defaultdict(list)
#     for r in topic_id_rows:
#         topic_ids_by_group[(r.group_key, r.unit)].append(r.id)

        

#     return {
#         "total_papers_analyzed": total_papers,
#         "topics": [
#             {
#                 "topic": r.topic_label,
#                 "question_count": r.question_count,
#                 "years_appeared": r.years_appeared,
#                 "avg_marks_per_paper": round(r.total_marks / total_papers, 1),
#                 "topic_ids": topic_ids_by_group[r.group_key],
#             }
#             for r in rows
#         ],
#     }