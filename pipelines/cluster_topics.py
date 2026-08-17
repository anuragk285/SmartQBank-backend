"""
canonicalize_topics.py

Groups similar topics within each SubjectGroup into CanonicalTopics, using
Gemini for the semantic decision (world-knowledge cases like "Dijkstra's
algorithm" == "shortest path problem"), with an optional local-embedding
pre-filter to resolve trivial near-duplicate topics without spending an
LLM call on them.

Incremental by design: a subject group is only sent to Gemini while it has
Topic rows with match_status == "unmatched". Once processed, topics are
marked "suggested" (pending human review in your admin UI) and the group
is skipped on future runs unless new unmatched topics appear (e.g. next
year's papers get extracted).

Setup
-----
    pip install google-genai
    # optional, only needed if ENABLE_EMBEDDING_PREFILTER=true:
    pip install sentence-transformers numpy

    export GEMINI_API_KEY=...
    export DATABASE_URL=...             # optional, same as your FastAPI app
    export ENABLE_EMBEDDING_PREFILTER=true   # optional

Run
---
    python canonicalize_topics.py

Requires the `SubjectContent` table and `Topic.subject_content_id` FK
described alongside this script - topics are scoped to (subject_code,
regulation_code) via SubjectContent, shared across every department that
teaches it, rather than to one department-specific Subject row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from typing import Optional
from dotenv import load_dotenv

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from google import genai
from google.genai import types

from database import SessionLocal
from models import SubjectContent, SubjectGroup, Topic, Question, CanonicalTopic

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("canonicalize_topics")

GEMINI_MODEL = "gemini-3.6-flash"
MAX_CONCURRENT_GROUPS = 3          # subject groups processed in parallel
SAMPLE_QUESTIONS_PER_TOPIC = 2     # grounds the LLM with real exam-question phrasing
MAX_TOPICS_PER_CALL = 200          # safety valve, see warning in process_group()

ENABLE_EMBEDDING_PREFILTER = os.getenv("ENABLE_EMBEDDING_PREFILTER", "false").lower() == "true"
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
PREFILTER_SIMILARITY_THRESHOLD = 0.93  # conservative on purpose: only catches near-identical strings,
                                        # NOT the hard semantic cases — that's still Gemini's job
_gemini_client: Optional[genai.Client] = None


def get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Set the GEMINI_API_KEY environment variable before running this script.")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


class TopicCluster(BaseModel):
    matches_existing_canonical_id: Optional[int] = Field(
        default=None,
        description=(
            "If this cluster IS the same concept as one of the entries in "
            "existing_canonical_topics below, put that id here and leave "
            "new_canonical_label/new_canonical_description null. Otherwise null."
        ),
    )
    new_canonical_label: Optional[str] = Field(
        default=None,
        description="Required if matches_existing_canonical_id is null. A short, standardized topic name.",
    )
    new_canonical_description: Optional[str] = Field(
        default=None,
        description="Required if matches_existing_canonical_id is null. One sentence describing what this topic covers.",
    )
    topic_ids: list[int] = Field(
        description="IDs (from topics_to_classify below) that belong to this canonical topic."
    )
    confidence: float = Field(
        ge=0, le=1,
        description="Your confidence that every topic_id here truly represents the same underlying concept.",
    )


class CanonicalizationResult(BaseModel):
    clusters: list[TopicCluster]


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are standardizing exam-topic names for the subject group "{subject_group_name}" \
(department: {department}) across different curriculum regulations at an engineering college. Each \
regulation's topics were extracted independently, so the same underlying concept may appear under \
different names, and wording may vary even when the tested concept is identical.

Your job: group "topics_to_classify" into canonical topics.

Rules:
1. Merge topics that test the SAME underlying concept even if the wording is completely different. \
Example: "finding shortest path problem" and "dijkstra algorithm" belong together if their sample \
questions are both about computing shortest paths - the algorithm name and the problem name are the \
same concept from a student's point of view.
2. Do NOT merge topics that are lexically similar but conceptually distinct. Example: "relational \
database" and "non relational database" are different topics - do not merge them just because most \
of the words overlap. Watch for this pattern generally: negation words ("non-", "un-", "without", \
"vs"), and pairs like "supervised/unsupervised" or "synchronous/asynchronous".
3. If a topic doesn't fit any existing canonical topic and doesn't match any other new topic either, \
it still needs an entry: give it its own single-member cluster.
4. Every id in topics_to_classify must appear in exactly one cluster's topic_ids. Do not omit any and \
do not put the same id in two clusters.
5. If existing_canonical_topics is non-empty, prefer matching into an existing one over creating a \
near-duplicate new one with slightly different wording.

existing_canonical_topics (already-established canonical topics for this subject group; may be empty):
{existing_canonical_topics_json}

topics_to_classify (each has an id, the topic text, the subject/regulation it came from, and a couple \
of real exam questions filed under it for context):
{topics_json}
"""


async def build_prompt(
    group: SubjectGroup,
    topics: list[Topic],
    sample_questions: dict[int, list[str]],
    existing_canonicals: list[CanonicalTopic],
) -> str:
    topics_payload = [
        {
            "id": t.id,
            "topic": t.topic,
            "subject": t.subject_content.name if t.subject_content else t.subject_code,
            "regulation": t.subject_content.regulation_code if t.subject_content else None,
            "sample_questions": sample_questions.get(t.id, []),
        }
        for t in topics
    ]
    existing_payload = [
        {"id": c.id, "label": c.label, "description": c.description or ""}
        for c in existing_canonicals
    ]
    return PROMPT_TEMPLATE.format(
        subject_group_name=group.canonical_name,
        department=group.department or "unspecified",
        existing_canonical_topics_json=json.dumps(existing_payload, indent=2),
        topics_json=json.dumps(topics_payload, indent=2),
    )


async def call_gemini(prompt: str) -> CanonicalizationResult:
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            response = await get_gemini_client().aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CanonicalizationResult,
                    temperature=0.1,
                ),
            )
            if response.parsed is not None:
                return response.parsed
            return CanonicalizationResult.model_validate_json(response.text)
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            logger.warning("Gemini call failed (attempt %d/3): %s - retrying in %ds", attempt + 1, e, wait)
            await asyncio.sleep(wait)
    assert last_err is not None
    raise last_err

async def get_pending_subject_groups(db: AsyncSession) -> list[SubjectGroup]:
    """
    A group is 'pending' if any of its topics (across any SubjectContent -
    i.e. any regulation, shared across departments - in the group) has
    match_status == 'unmatched'. Fully-processed groups (everything at
    least 'suggested') are skipped automatically.

    Requires Topic.subject_content_id (see the SubjectContent model).
    """
    stmt = (
        select(SubjectGroup)
        .join(SubjectContent, SubjectContent.subject_group_id == SubjectGroup.id)
        .join(Topic, Topic.subject_content_id == SubjectContent.id)
        .where(Topic.match_status == "unmatched")
        .distinct()
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_group_topics(db: AsyncSession, group_id: int) -> list[Topic]:
    stmt = (
        select(Topic)
        .join(SubjectContent, Topic.subject_content_id == SubjectContent.id)
        .where(SubjectContent.subject_group_id == group_id)
        .options(selectinload(Topic.subject_content))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_sample_questions(db: AsyncSession, topic_ids: list[int]) -> dict[int, list[str]]:
    if not topic_ids:
        return {}
    stmt = select(Question.topic_id, Question.text).where(Question.topic_id.in_(topic_ids))
    result = await db.execute(stmt)
    by_topic: dict[int, list[str]] = defaultdict(list)
    for topic_id, text in result.all():
        if len(by_topic[topic_id]) < SAMPLE_QUESTIONS_PER_TOPIC:
            by_topic[topic_id].append(text[:200])  # keep the prompt compact
    return dict(by_topic)


_embedding_model = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s ...", EMBEDDING_MODEL_NAME)
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedding_model


def prefilter_exact_duplicates(unmatched: list[Topic]) -> tuple[list[Topic], list[list[Topic]]]:
    """
    Auto-groups near-identical topic strings (e.g. the exact same topic
    re-extracted across regulations) using cosine similarity, so they don't
    take up space in the Gemini batch. This threshold is intentionally
    high/conservative - it is NOT meant to catch the hard semantic cases
    (Dijkstra/shortest-path); that's still Gemini's job.

    Persists computed embeddings back onto Topic.embedding so they aren't
    recomputed on the next run.

    Returns (remaining_topics_for_llm, auto_merged_groups).
    """
    if not ENABLE_EMBEDDING_PREFILTER or len(unmatched) < 2:
        return unmatched, []

    import numpy as np

    model = _get_embedding_model()

    to_encode = [t for t in unmatched if t.embedding is None]
    if to_encode:
        fresh = model.encode([t.topic for t in to_encode], normalize_embeddings=True)
        for t, emb in zip(to_encode, fresh):
            t.embedding = emb.tolist()

    embeddings = np.array([t.embedding for t in unmatched])

    n = len(unmatched)
    visited = [False] * n
    auto_merged: list[list[Topic]] = []

    for i in range(n):
        if visited[i]:
            continue
        group = [unmatched[i]]
        visited[i] = True
        for j in range(i + 1, n):
            if visited[j]:
                continue
            sim = float(np.dot(embeddings[i], embeddings[j]))
            if sim >= PREFILTER_SIMILARITY_THRESHOLD:
                group.append(unmatched[j])
                visited[j] = True
        if len(group) > 1:
            auto_merged.append(group)

    merged_ids = {t.id for g in auto_merged for t in g}
    remaining = [t for t in unmatched if t.id not in merged_ids]
    return remaining, auto_merged


async def apply_result(
    db: AsyncSession,
    group: SubjectGroup,
    result: CanonicalizationResult,
    topics_by_id: dict[int, Topic],
    existing_by_id: dict[int, CanonicalTopic],
) -> None:
    existing_by_label = {c.label.strip().lower(): c for c in existing_by_id.values()}
    seen_topic_ids: set[int] = set()

    for cluster in result.clusters:
        cluster_topic_ids = [tid for tid in cluster.topic_ids if tid in topics_by_id]
        skipped = set(cluster.topic_ids) - topics_by_id.keys()
        if skipped:
            logger.warning("Group %s: ignoring hallucinated topic_ids %s", group.id, skipped)

        dupes = seen_topic_ids & set(cluster_topic_ids)
        if dupes:
            logger.warning(
                "Group %s: topic_ids %s appear in multiple clusters, keeping first assignment",
                group.id, dupes,
            )
            cluster_topic_ids = [tid for tid in cluster_topic_ids if tid not in dupes]
        if not cluster_topic_ids:
            continue
        seen_topic_ids.update(cluster_topic_ids)

        canonical_topic = None
        if cluster.matches_existing_canonical_id is not None:
            canonical_topic = existing_by_id.get(cluster.matches_existing_canonical_id)
            if canonical_topic is None:
                logger.warning(
                    "Group %s: model referenced unknown existing canonical id %s, treating as new",
                    group.id, cluster.matches_existing_canonical_id,
                )

        if canonical_topic is None:
            if not cluster.new_canonical_label:
                logger.warning(
                    "Group %s: cluster missing both existing id and new label, skipping topic_ids %s",
                    group.id, cluster_topic_ids,
                )
                continue
            label_key = cluster.new_canonical_label.strip().lower()
            canonical_topic = existing_by_label.get(label_key)
            if canonical_topic is None:
                canonical_topic = CanonicalTopic(
                    subject_group_id=group.id,
                    label=cluster.new_canonical_label.strip(),
                    description=cluster.new_canonical_description,
                )
                db.add(canonical_topic)
                await db.flush()  # populate canonical_topic.id
                existing_by_id[canonical_topic.id] = canonical_topic
                existing_by_label[label_key] = canonical_topic

        for tid in cluster_topic_ids:
            topic = topics_by_id[tid]
            topic.canonical_topic_id = canonical_topic.id
            topic.match_status = "suggested"  # pending human review in your admin UI
            topic.match_confidence = cluster.confidence


async def process_group(group_id: int, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        async with SessionLocal() as db:
            try:
                group = await db.get(SubjectGroup, group_id)
                topics = await get_group_topics(db, group_id)
                unmatched = [t for t in topics if t.match_status == "unmatched"]
                if not unmatched:
                    logger.info("Group %s (%s): nothing to do, skipping", group_id, group.canonical_name)
                    return

                existing_result = await db.execute(
                    select(CanonicalTopic).where(CanonicalTopic.subject_group_id == group.id)
                )
                existing_canonicals = list(existing_result.scalars().all())
                existing_by_id = {c.id: c for c in existing_canonicals}

                remaining, auto_merged = prefilter_exact_duplicates(unmatched)

                # Auto-merged near-duplicates: resolve without spending an LLM call.
                for dup_group in auto_merged:
                    label = dup_group[0].topic.strip()
                    label_key = label.lower()
                    canonical = next(
                        (c for c in existing_by_id.values() if c.label.strip().lower() == label_key),
                        None,
                    )
                    if canonical is None:
                        canonical = CanonicalTopic(subject_group_id=group.id, label=label)
                        db.add(canonical)
                        await db.flush()
                        existing_by_id[canonical.id] = canonical
                    for t in dup_group:
                        t.canonical_topic_id = canonical.id
                        t.match_status = "suggested"
                        t.match_confidence = 0.99

                if remaining:
                    if len(remaining) > MAX_TOPICS_PER_CALL:
                        logger.warning(
                            "Group %s: %d topics in one batch exceeds MAX_TOPICS_PER_CALL (%d). "
                            "Sending anyway - consider enabling ENABLE_EMBEDDING_PREFILTER if this "
                            "happens often.",
                            group.id, len(remaining), MAX_TOPICS_PER_CALL,
                        )
                    sample_questions = await get_sample_questions(db, [t.id for t in remaining])
                    prompt = await build_prompt(group, remaining, sample_questions, existing_canonicals)
                    result = await call_gemini(prompt)
                    topics_by_id = {t.id: t for t in remaining}
                    await apply_result(db, group, result, topics_by_id, existing_by_id)

                await db.commit()
                logger.info("Group %s (%s): processed %d topics", group.id, group.canonical_name, len(unmatched))
            except Exception:
                await db.rollback()
                logger.exception("Group %s failed, rolled back", group_id)


async def main() -> None:
    async with SessionLocal() as db:
        groups = await get_pending_subject_groups(db)
    logger.info("Found %d subject group(s) with unmatched topics", len(groups))

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_GROUPS)
    await asyncio.gather(*(process_group(g.id, semaphore) for g in groups))


if __name__ == "__main__":
    asyncio.run(main())