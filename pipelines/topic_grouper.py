import os
import sys
import json
import random
import asyncio
import argparse
import numpy as np

from sqlalchemy import select, text, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer
from google import genai
from google.genai import types
from dotenv import load_dotenv

from models import SubjectContent, SubjectGroup, Topic, CanonicalTopic, Base

load_dotenv()

AUTO_MERGE_SEMANTIC = 92.0
AUTO_MERGE_FUZZY_EXACT = 97.0
GEMINI_REVIEW_FLOOR = 75.0

CANDIDATE_TOP_K = 5
CANDIDATE_MIN_SEMANTIC = 35.0

GEMINI_BATCH_SIZE = 25
GEMINI_RPM = int(os.getenv("GEMINI_RPM", "10"))     
GEMINI_MAX_RETRIES = 5
GEMINI_BASE_BACKOFF = 5.0                            # seconds, doubles each retry
FALLBACK_MERGE_FLOOR = 85.0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "smart-question-bank-database.db")
DATABASE_URL = f"sqlite+aiosqlite:///{os.path.abspath(DB_PATH)}"

print(f"🔗 Connecting to database at: {DATABASE_URL}")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

print("⏳ Loading local ML embedding model (all-MiniLM-L6-v2)...")
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

gemini_api_key = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None


class GeminiRateLimiter:
    """Enforces a minimum spacing between successive Gemini calls (a simple
    1-slot token bucket). All calls funnel through here, so even across
    different groups/batches there's never a burst that trips the RPM wall."""

    def __init__(self, requests_per_minute: int):
        self.min_interval = 60.0 / requests_per_minute
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait_for_slot(self):
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_call = asyncio.get_event_loop().time()


rate_limiter = GeminiRateLimiter(GEMINI_RPM)

def _is_rate_limit_error(e: Exception) -> bool:
    status = getattr(e, "status_code", None) or getattr(e, "code", None)
    msg = str(e).lower()
    return status == 429 or "429" in msg or "resource_exhausted" in msg or "rate limit" in msg or "quota" in msg


async def call_gemini_with_backoff(prompt: str):
    """Rate-limited Gemini call with exponential backoff on 429s specifically.
    Non-rate-limit errors (bad prompt, network blip) are NOT retried here --
    they bubble up so the caller can fall back immediately rather than
    burning retries on something backoff can't fix."""
    delay = GEMINI_BASE_BACKOFF
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        await rate_limiter.wait_for_slot()
        try:
            return await gemini_client.aio.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
        except Exception as e:
            if _is_rate_limit_error(e) and attempt < GEMINI_MAX_RETRIES:
                wait_s = delay + random.uniform(0, 1.5)
                print(f"  ⏳ Rate limited (attempt {attempt}/{GEMINI_MAX_RETRIES}) — backing off {wait_s:.1f}s...")
                await asyncio.sleep(wait_s)
                delay *= 2
                continue
            raise
    raise RuntimeError("Exhausted Gemini retries")


def precompute_group_embeddings(topics: list[Topic]) -> np.ndarray:
    missing = [t for t in topics if not t.embedding]
    if missing:
        texts = [f"{t.subject_code} unit {t.unit}: {t.topic}" for t in missing]
        vectors = embedding_model.encode(texts, convert_to_tensor=False, batch_size=64)
        for t, v in zip(missing, vectors):
            t.embedding = v.tolist() if isinstance(v, np.ndarray) else list(v)

    matrix_data = []
    for t in topics:
        emb = t.embedding
        if isinstance(emb, str):
            emb = json.loads(emb)
        matrix_data.append(emb)

    matrix = np.array(matrix_data, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-10, norms)
    norm_matrix = matrix / norms
    return np.dot(norm_matrix, norm_matrix.T)


def generate_candidate_pairs(topics: list[Topic], sim_matrix: np.ndarray) -> list[tuple[int, int, float]]:
    n = len(topics)
    codes = np.array([t.subject_code for t in topics])
    masked = sim_matrix.copy()
    masked[codes[:, None] == codes[None, :]] = -1.0
    np.fill_diagonal(masked, -1.0)

    floor = CANDIDATE_MIN_SEMANTIC / 100.0
    candidates: dict[tuple[int, int], float] = {}
    for i in range(n):
        row = masked[i]
        top_idx = np.argsort(row)[::-1][:CANDIDATE_TOP_K]
        for j in top_idx:
            score = float(row[j])
            if score < floor:
                continue
            pair = (i, j) if i < j else (j, i)
            if pair not in candidates or score > candidates[pair]:
                candidates[pair] = score

    return sorted(((i, j, s) for (i, j), s in candidates.items()), key=lambda x: -x[2])


def is_extraction_artifact(topic_text: str, group_name: str) -> bool:
    t = topic_text.lower().strip()
    g = group_name.lower().strip()
    if not t:
        return True
    return len(t.split()) <= 2 and fuzz.token_sort_ratio(t, g) >= 85


async def merge_topics(
    t1: Topic, t2: Topic, group_id: int, session: AsyncSession,
    status: str = "auto_high", confidence: float = None, canonical_label: str = None,
):
    chosen_label = canonical_label.strip() if canonical_label and canonical_label.strip() else t1.topic

    if not t1.canonical_topic_id and not t2.canonical_topic_id:
        new_canonical = CanonicalTopic(label=chosen_label, subject_group_id=group_id)
        session.add(new_canonical)
        await session.flush()
        t1.canonical_topic_id = new_canonical.id
        t2.canonical_topic_id = new_canonical.id
    elif t1.canonical_topic_id and not t2.canonical_topic_id:
        t2.canonical_topic_id = t1.canonical_topic_id
    elif not t1.canonical_topic_id and t2.canonical_topic_id:
        t1.canonical_topic_id = t2.canonical_topic_id
    elif t1.canonical_topic_id != t2.canonical_topic_id:
        old_id = t2.canonical_topic_id
        t2.canonical_topic_id = t1.canonical_topic_id
        result = await session.execute(select(Topic).where(Topic.canonical_topic_id == old_id))
        for topic in result.scalars().all():
            topic.canonical_topic_id = t1.canonical_topic_id
            topic.match_status = status
            topic.match_confidence = confidence
        await session.execute(delete(CanonicalTopic).where(CanonicalTopic.id == old_id))

    t1.match_status = status
    t1.match_confidence = max(t1.match_confidence or 0.0, confidence or 0.0)
    t2.match_status = status
    t2.match_confidence = max(t2.match_confidence or 0.0, confidence or 0.0)


async def local_pass_for_group(group: SubjectGroup, group_topics: list[Topic], session: AsyncSession):
    """Returns the group's leftover ambiguous pairs. Nothing here touches
    the network, so this runs at full CPU speed for every group before a
    single Gemini call is made anywhere in the pipeline."""
    sim_matrix = precompute_group_embeddings(group_topics)
    candidates = generate_candidate_pairs(group_topics, sim_matrix)

    queue = []
    for i, j, semantic_frac in candidates:
        t1, t2 = group_topics[i], group_topics[j]
        if t1.canonical_topic_id is not None and t1.canonical_topic_id == t2.canonical_topic_id:
            continue

        semantic_score = semantic_frac * 100.0
        fuzzy_score = float(fuzz.token_sort_ratio(t1.topic, t2.topic))
        combined_score = max(fuzzy_score, semantic_score)

        if semantic_score >= AUTO_MERGE_SEMANTIC or fuzzy_score >= AUTO_MERGE_FUZZY_EXACT:
            await merge_topics(t1, t2, group.id, session, status="auto_high", confidence=combined_score)
            continue

        if combined_score < GEMINI_REVIEW_FLOOR:
            continue
        if t1.topic.strip().lower() != t2.topic.strip().lower():
            if is_extraction_artifact(t1.topic, group.canonical_name) or is_extraction_artifact(t2.topic, group.canonical_name):
                continue

        queue.append((group, t1, t2, combined_score))

    await session.commit()
    return queue


async def gemini_batch_review(chunk: list[tuple[SubjectGroup, Topic, Topic, float]]):
    numbered = []
    for idx, (group, t1, t2, score) in enumerate(chunk, start=1):
        numbered.append(
            f'{idx}. [{group.canonical_name}] A: "{t1.topic}" (Unit {t1.unit}, {t1.subject_code}) '
            f'| B: "{t2.topic}" (Unit {t2.unit}, {t2.subject_code}) | similarity hint: {score:.1f}%'
        )
    pairs_block = "\n".join(numbered)

    prompt = f"""
You are an expert curriculum reviewer at an engineering college. Below are {len(chunk)} pairs of topics, each pair bracketed with its subject/course context. Topics in a pair may come from different regulations, semesters, or even differently-named subjects/departments that were later found to overlap.

For EACH pair, independently decide whether A and B refer to the SAME examinable concept: a question written to test A would also correctly and fully test B.

Rules:
1. MERGE minor wording or notation variants of the same concept.
2. MERGE an acronym and its expansion when they mean the same thing.
3. DO NOT merge topics that share words but test different content (e.g. "Stack using arrays" vs "Stack using linked lists").
4. DO NOT merge sequential/hierarchical sub-parts of a larger topic (e.g. "1NF and 2NF" vs "3NF and BCNF").
5. DO NOT merge distinct algorithms/protocols/architectures from the same family (e.g. "DFA" vs "NFA").
6. Unit number mismatches are NOT a reason to reject a merge.
7. A topic from a differently-named subject/department CAN still merge if the underlying concept is genuinely identical (e.g. a "Data Communications" course and a "Computer Networks" course may both cover the same OSI layers topic).
8. The similarity hint is a weak automated signal only.

Pairs:
{pairs_block}

Respond ONLY with a strict JSON array, exactly {len(chunk)} objects, IN THE SAME ORDER, no markdown fences:
[
  {{"pair": 1, "should_merge": true, "confidence": 0-100, "canonical_label": "...", "reason": "..."}}
]
"""
    if not gemini_client:
        return None

    try:
        response = await call_gemini_with_backoff(prompt)
        data = json.loads(response.text)
        if not isinstance(data, list) or len(data) != len(chunk):
            print(f"  ⚠️ Batch size mismatch (got {len(data) if isinstance(data, list) else 'invalid'}, expected {len(chunk)}).")
            return None
        return data
    except Exception as e:
        print(f"  ⚠️ Gemini batch permanently failed after retries ({type(e).__name__}): {e}")
        return None


def algorithmic_fallback_decision(score: float) -> dict:
    """Used ONLY when Gemini is fully unavailable after retries. Deliberately
    conservative -- requires a high combined score before merging -- so an
    outage degrades to 'slightly under-merged, flagged for optional audit'
    rather than blocking on a human or guessing wildly."""
    return {
        "should_merge": score >= FALLBACK_MERGE_FLOOR,
        "confidence": score,
        "canonical_label": "",
        "reason": "algorithmic fallback -- gemini unavailable after retries",
    }


async def process_global_queue(all_pairs: list[tuple[SubjectGroup, Topic, Topic, float]], session: AsyncSession):
    stats = {"gemini_confirmed": 0, "auto_fallback": 0, "gemini_calls": 0}

    for start in range(0, len(all_pairs), GEMINI_BATCH_SIZE):
        chunk = [
            p for p in all_pairs[start:start + GEMINI_BATCH_SIZE]
            if not (p[1].canonical_topic_id is not None and p[1].canonical_topic_id == p[2].canonical_topic_id)
        ]
        if not chunk:
            continue

        print(f"  ❓ Gemini batch: {len(chunk)} pair(s)...")
        decisions = await gemini_batch_review(chunk)
        stats["gemini_calls"] += 1 if gemini_client else 0

        for idx, (group, t1, t2, score) in enumerate(chunk):
            if t1.canonical_topic_id is not None and t1.canonical_topic_id == t2.canonical_topic_id:
                continue

            if decisions is not None:
                decision = decisions[idx]
                status = "gemini_confirmed"
            else:
                decision = algorithmic_fallback_decision(score)
                status = "auto_fallback"

            if bool(decision.get("should_merge", False)):
                conf = float(decision.get("confidence") or score)
                label = str(decision.get("canonical_label") or "").strip()
                await merge_topics(t1, t2, group.id, session, status=status, confidence=conf, canonical_label=label)
                stats["gemini_confirmed" if status == "gemini_confirmed" else "auto_fallback"] += 1

        await session.commit()

    return stats

async def verify_and_update_schema(conn):
    columns_to_add = [
        ("topics", "canonical_topic_id", "INTEGER REFERENCES canonical_topics(id)"),
        ("topics", "embedding", "JSON"),
        ("topics", "match_status", "VARCHAR DEFAULT 'unmatched'"),
        ("topics", "match_confidence", "FLOAT"),
    ]
    for table, col_name, col_type in columns_to_add:
        res = await conn.execute(text(f"PRAGMA table_info({table});"))
        existing = [row[1] for row in res.fetchall()]
        if col_name not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type};"))
            print(f"  ➕ Added missing column '{col_name}' to '{table}'.")

async def print_coverage_report(session: AsyncSession):
    orphan_count = (await session.execute(text("""
        SELECT COUNT(*) FROM topics t
        LEFT JOIN subjects s ON s.subject_code = t.subject_code
        WHERE s.id IS NULL
    """))).scalar()
    ungrouped_count = (await session.execute(text("""
        SELECT COUNT(DISTINCT t.id) FROM topics t
        JOIN subjects s ON s.subject_code = t.subject_code
        WHERE s.subject_group_id IS NULL
    """))).scalar()
    if orphan_count or ungrouped_count:
        print(f"⚠️  Coverage gaps: {orphan_count} orphaned topic(s), "
              f"{ungrouped_count} topic(s) under ungrouped subjects — NOT processed this run.\n")

async def print_coverage_report(session: AsyncSession):
    orphan_count = (await session.execute(text("""
        SELECT COUNT(*) FROM topics t
        LEFT JOIN subject_contents sc ON sc.id = t.subject_content_id
        WHERE sc.id IS NULL
    """))).scalar()
    ungrouped_count = (await session.execute(text("""
        SELECT COUNT(DISTINCT t.id) FROM topics t
        JOIN subject_contents sc ON sc.id = t.subject_content_id
        WHERE sc.subject_group_id IS NULL
    """))).scalar()
    if orphan_count or ungrouped_count:
        print(f"⚠️  Coverage gaps: {orphan_count} orphaned topic(s), "
              f"{ungrouped_count} topic(s) under ungrouped subject contents — NOT processed this run.\n")

async def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()  # kept for future flags

    print("🚀 Initializing Database Schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await verify_and_update_schema(conn)

    async with AsyncSessionLocal() as session:
        await print_coverage_report(session=session)
        
        # 1. Fetch all Subject Groups
        result = await session.execute(select(SubjectGroup))
        subject_groups = result.scalars().all()
        print(f"📚 Loaded {len(subject_groups)} Subject Groups.\n")

        all_queue = []
        group_topics_map = {}
        for group in subject_groups:
            # 2. Query topics through SubjectContent instead of group.subjects
            topics_res = await session.execute(
                select(Topic)
                .join(SubjectContent, Topic.subject_content_id == SubjectContent.id)
                .where(SubjectContent.subject_group_id == group.id)
            )
            group_topics = topics_res.scalars().all()
            group_topics_map[group.id] = group_topics

            if len(group_topics) == 1:
                precompute_group_embeddings(group_topics)
                continue
            elif len(group_topics) == 0:
                continue

            print(f"📂 [Local] Group [ID {group.id}]: '{group.canonical_name}' ({len(group_topics)} topics)")
            queue = await local_pass_for_group(group, group_topics, session)
            all_queue.extend(queue)

        print(f"\n🧮 Local pass complete. {len(all_queue)} ambiguous pair(s) queued for Gemini across all groups.\n")

        stats = await process_global_queue(all_queue, session)

        for group in subject_groups:
            group_topics = group_topics_map.get(group.id)
            if not group_topics:
                continue

            # Fetch all existing CanonicalTopics for this group to avoid duplicate constraint errors
            ct_res = await session.execute(
                select(CanonicalTopic).where(CanonicalTopic.subject_group_id == group.id)
            )
            existing_canonicals = ct_res.scalars().all()
            
            # Case-insensitive map of label -> CanonicalTopic object
            canonical_map = {ct.label.strip().lower(): ct for ct in existing_canonicals}

            unassigned = [t for t in group_topics if t.canonical_topic_id is None]
            for t in unassigned:
                clean_label = t.topic.strip()
                lookup_key = clean_label.lower()

                if lookup_key in canonical_map:
                    # Reuse existing canonical topic
                    canonical_item = canonical_map[lookup_key]
                else:
                    # Create new canonical topic and cache it
                    canonical_item = CanonicalTopic(label=clean_label, subject_group_id=group.id)
                    session.add(canonical_item)
                    await session.flush()  # Generates canonical_item.id
                    canonical_map[lookup_key] = canonical_item

                t.canonical_topic_id = canonical_item.id
                t.match_status = "standalone"
                t.match_confidence = 100.0

            if unassigned:
                await session.commit()

        print("\n🧹 Sweeping up orphaned and ungrouped topics...")
        ungrouped_res = await session.execute(select(Topic).where(Topic.match_status == 'unmatched'))
        leftover_topics = ungrouped_res.scalars().all()
        
        if leftover_topics:
            print(f"  Generating embeddings for {len(leftover_topics)} leftover topic(s)...")
            texts = [f"{t.subject_code or 'Unknown'} unit {t.unit}: {t.topic}" for t in leftover_topics]
            vectors = embedding_model.encode(texts, convert_to_tensor=False, batch_size=64)
            
            for t, v in zip(leftover_topics, vectors):
                t.embedding = v.tolist() if isinstance(v, np.ndarray) else list(v)
                t.match_status = "orphaned_or_ungrouped"
                t.match_confidence = 0.0
                
            await session.commit()
            print("  ✅ Leftovers successfully processed.")
        else:
            print("  ✅ No leftovers found.")

        print("\n✨ Done.")
        print(f"   Gemini-confirmed: {stats['gemini_confirmed']} | Algorithmic fallback: {stats['auto_fallback']}")
        print(f"   Total Gemini API calls: {stats['gemini_calls']} (~{stats['gemini_calls'] * 60 / max(GEMINI_RPM,1):.0f}s min. wall time at {GEMINI_RPM} RPM)")


if __name__ == "__main__":
    asyncio.run(main())