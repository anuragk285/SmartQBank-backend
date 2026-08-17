import os
import json
import asyncio
import itertools
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer, util
from google import genai
from google.genai import types
from models import SubjectContent, SubjectGroup, Base
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "smart-question-bank-database.db")

DATABASE_URL = f"sqlite+aiosqlite:///{os.path.abspath(DB_PATH)}"

print(f"🔗 Connecting to database at: {DATABASE_URL}")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

print("⏳ Loading local ML embedding model (all-MiniLM-L6-v2)...")
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

gemini_api_key = os.getenv('GEMINI_API_KEY')
gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None


def get_semantic_similarity(name1: str, name2: str) -> float:
    """Calculates semantic similarity (0-100%) using sentence embeddings."""
    emb1 = embedding_model.encode(name1, convert_to_tensor=True)
    emb2 = embedding_model.encode(name2, convert_to_tensor=True)
    cosine_sim = util.cos_sim(emb1, emb2).item()
    return round(float(cosine_sim) * 100, 2)


def departments_of(content: SubjectContent) -> list[str]:
    """SubjectContent has no single department (it's shared across whichever
    departments teach it under this code) - derive the set from the linked
    Subject rows instead."""
    depts = sorted({s.department for s in content.subjects if s.department})
    return depts or ["N/A"]


async def manual_terminal_review(c1: SubjectContent, c2: SubjectContent, combined_score: float, reason: str = "") -> bool:
    """Fallback interactive terminal prompt when Gemini API fails or quota is exceeded."""
    print("\n" + "=" * 65)
    print("🖐️ MANUAL TERMINAL REVIEW REQUIRED")
    if reason:
        print(f"   Reason: {reason}")
    print(f"   Similarity Score: {combined_score:.1f}%")
    print(f"   Subject A [ID {c1.id}]: '{c1.name}' | Depts: {', '.join(departments_of(c1))} | Code: {c1.subject_code or 'N/A'} | Reg: {c1.regulation_code or 'N/A'}")
    print(f"   Subject B [ID {c2.id}]: '{c2.name}' | Depts: {', '.join(departments_of(c2))} | Code: {c2.subject_code or 'N/A'} | Reg: {c2.regulation_code or 'N/A'}")
    print("=" * 65)

    def ask_user():
        while True:
            choice = input("👉 Merge these subjects into the same group? (y/n): ").strip().lower()
            if choice in ['y', 'yes']:
                return True
            elif choice in ['n', 'no']:
                return False
            print("Invalid input. Please enter 'y' or 'n'.")

    # Use asyncio.to_thread to keep input non-blocking for async loop
    decision = await asyncio.to_thread(ask_user)
    print("=" * 65 + "\n")
    return decision


async def gemini_review_match(c1: SubjectContent, c2: SubjectContent, combined_score: float) -> bool:
    """Uses Gemini API as an expert academic reviewer using full syllabus topic lists."""
    if not gemini_client:
        print("⚠️ GEMINI_API_KEY not set. Delegating to manual review...")
        return await manual_terminal_review(c1, c2, combined_score, reason="Missing GEMINI_API_KEY")

    # SubjectContent.topics is a real FK relationship now (Topic.subject_content_id),
    # not the old subject_code string-matching join.
    c1_topics = [t.topic for t in c1.topics]
    c2_topics = [t.topic for t in c2.topics]

    c1_topic_str = "\n".join([f"  - {t}" for t in c1_topics]) if c1_topics else "  (No topics listed)"
    c2_topic_str = "\n".join([f"  - {t}" for t in c2_topics]) if c2_topics else "  (No topics listed)"

    prompt = f"""
You are an expert university curriculum committee member evaluating engineering subjects for academic equivalence.

Compare the following two subjects using both their metadata AND their covered syllabus topics across units:

SUBJECT A:
- Title: "{c1.name}"
- Departments: {', '.join(departments_of(c1))} | Code: {c1.subject_code} | Regulation: {c1.regulation_code}
- Topics Covered ({len(c1_topics)} total):
{c1_topic_str}

SUBJECT B:
- Title: "{c2.name}"
- Departments: {', '.join(departments_of(c2))} | Code: {c2.subject_code} | Regulation: {c2.regulation_code}
- Topics Covered ({len(c2_topics)} total):
{c2_topic_str}

Similarity Match Hint: {combined_score:.1f}%

EVALUATION CRITERIA:
1. Check if both subjects cover essentially the same core concepts and syllabus content (>70% topic overlap).
2. Ignore minor naming variations in topic titles.
3. Treat foundational vs advanced subjects as DIFFERENT (e.g. "Web Tech" vs "Full Stack Dev").
4. Treat subjects with different terminology but identical concepts as EQUIVALENT (e.g. "FLAT" vs "Theory of Computation").

Respond ONLY in strict JSON format:
{{
  "should_merge": true or false,
  "reason": "1-2 sentence justification based on syllabus topic overlap"
}}
"""

    try:
        response = await gemini_client.aio.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        data = json.loads(response.text)
        print(f"  🤖 Gemini Evaluation (Syllabus-Aware):")
        print(f"     Merge : {data.get('should_merge')}")
        print(f"     Reason: {data.get('reason')}\n")
        return data.get("should_merge", False)

    except Exception as e:
        print(f"  ⚠️ Gemini API call failed ({type(e).__name__}). Falling back to manual review...")
        return await manual_terminal_review(c1, c2, combined_score, reason=f"Gemini Error ({e})")


async def merge_subjects(c1: SubjectContent, c2: SubjectContent, session: AsyncSession):
    """Links two SubjectContents into the same SubjectGroup."""
    if not c1.subject_group_id and not c2.subject_group_id:
        new_group = SubjectGroup(
            canonical_name=c1.name,
            department=None,  # SubjectContent already spans whichever departments share it
        )
        session.add(new_group)
        await session.flush()
        c1.subject_group_id = new_group.id
        c2.subject_group_id = new_group.id

    elif c1.subject_group_id and not c2.subject_group_id:
        c2.subject_group_id = c1.subject_group_id

    elif not c1.subject_group_id and c2.subject_group_id:
        c1.subject_group_id = c2.subject_group_id

    elif c1.subject_group_id != c2.subject_group_id:
        old_group_id = c2.subject_group_id
        c2.subject_group_id = c1.subject_group_id

        stmt = select(SubjectContent).where(SubjectContent.subject_group_id == old_group_id)
        result = await session.execute(stmt)
        for content in result.scalars().all():
            content.subject_group_id = c1.subject_group_id


async def evaluate_and_merge_subjects(c1: SubjectContent, c2: SubjectContent, session: AsyncSession):
    """Evaluates a pair using Lexical (Fuzzy) + Semantic (ML Embeddings) + Gemini API / Manual Fallback."""
    fuzzy_score = float(fuzz.token_sort_ratio(c1.name, c2.name))
    semantic_score = get_semantic_similarity(c1.name, c2.name)
    combined_score = max(fuzzy_score, semantic_score)

    print(f"🔍 Comparing: '{c1.name}' ↔ '{c2.name}'")
    print(f"   Lexical: {fuzzy_score:.1f}% | Semantic ML: {semantic_score:.1f}% | Best: {combined_score:.1f}%")

    if combined_score >= 88.0:
        print("  ✅ HIGH CONFIDENCE MATCH: Auto-merging...\n")
        await merge_subjects(c1, c2, session)

    elif combined_score >= 60.0:
        print("  ❓ AMBIGUOUS MATCH: Delegating to Gemini API Reviewer...")
        should_merge = await gemini_review_match(c1, c2, combined_score)
        if should_merge:
            await merge_subjects(c1, c2, session)
        else:
            print("  ❌ Rejected merge.\n")
    else:
        print("  ⏭️ LOW MATCH: Skipping.\n")


async def main():
    print("🚀 Initializing Database Schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(
                text("ALTER TABLE subject_contents ADD COLUMN subject_group_id INTEGER REFERENCES subject_groups(id);")
            )
            print("  ➕ Added missing subject_group_id column to subject_contents table.")
        except Exception:
            pass  # Column already exists

    async with AsyncSessionLocal() as session:
        stmt = select(SubjectContent).options(
            selectinload(SubjectContent.topics),
            selectinload(SubjectContent.subjects),
        )
        result = await session.execute(stmt)
        contents = result.scalars().all()
        print(f"📚 Loaded {len(contents)} subject contents from database (with syllabus topics!).\n")

        # Evaluate unique SubjectContent pairs - departments sharing a code
        # already collapsed into one row upstream, so there's no redundant
        # same-content comparison here anymore.
        for c1, c2 in itertools.combinations(contents, 2):
            if c1.subject_group_id is not None and c2.subject_group_id is not None:
                continue

            await evaluate_and_merge_subjects(c1, c2, session)

        await session.commit()

        # FALLBACK: Assign standalone groups to any remaining unassigned contents
        unassigned_stmt = select(SubjectContent).where(SubjectContent.subject_group_id.is_(None))
        unassigned_result = await session.execute(unassigned_stmt)
        unassigned_contents = unassigned_result.scalars().all()

        if unassigned_contents:
            print(f"📦 Creating standalone groups for {len(unassigned_contents)} unassigned subject contents...")
            for c in unassigned_contents:
                standalone_group = SubjectGroup(
                    canonical_name=c.name,
                    department=None,
                )
                session.add(standalone_group)
                await session.flush()
                c.subject_group_id = standalone_group.id

            await session.commit()

        print("\n✨ Subject grouping pipeline completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())