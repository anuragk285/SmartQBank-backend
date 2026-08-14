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
from models import Subject, SubjectGroup, Base
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


async def manual_terminal_review(s1: Subject, s2: Subject, combined_score: float, reason: str = "") -> bool:
    """Fallback interactive terminal prompt when Gemini API fails or quota is exceeded."""
    print("\n" + "=" * 65)
    print("🖐️ MANUAL TERMINAL REVIEW REQUIRED")
    if reason:
        print(f"   Reason: {reason}")
    print(f"   Similarity Score: {combined_score:.1f}%")
    print(f"   Subject A [ID {s1.id}]: '{s1.name}' | Dept: {s1.department or 'N/A'} | Code: {s1.subject_code or 'N/A'} | Reg: {s1.regulation_code or 'N/A'}")
    print(f"   Subject B [ID {s2.id}]: '{s2.name}' | Dept: {s2.department or 'N/A'} | Code: {s2.subject_code or 'N/A'} | Reg: {s2.regulation_code or 'N/A'}")
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

async def gemini_review_match(s1: Subject, s2: Subject, combined_score: float) -> bool:
    """Uses Gemini API as an expert academic reviewer using full syllabus topic lists."""
    if not gemini_client:
        print("⚠️ GEMINI_API_KEY not set. Delegating to manual review...")
        return await manual_terminal_review(s1, s2, combined_score, reason="Missing GEMINI_API_KEY")

    # 👇 CHANGED: Extract using `t.topic` based on your Topic model 👇
    s1_topics = [t.topic for t in getattr(s1, 'topics', [])]
    s2_topics = [t.topic for t in getattr(s2, 'topics', [])]

    s1_topic_str = "\n".join([f"  - {t}" for t in s1_topics]) if s1_topics else "  (No topics listed)"
    s2_topic_str = "\n".join([f"  - {t}" for t in s2_topics]) if s2_topics else "  (No topics listed)"

    prompt = f"""
You are an expert university curriculum committee member evaluating engineering subjects for academic equivalence.

Compare the following two subjects using both their metadata AND their covered syllabus topics across units:

SUBJECT A:
- Title: "{s1.name}"
- Department: {s1.department} | Code: {s1.subject_code} | Regulation: {s1.regulation_code}
- Topics Covered ({len(s1_topics)} total):
{s1_topic_str}

SUBJECT B:
- Title: "{s2.name}"
- Department: {s2.department} | Code: {s2.subject_code} | Regulation: {s2.regulation_code}
- Topics Covered ({len(s2_topics)} total):
{s2_topic_str}

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
        return await manual_terminal_review(s1, s2, combined_score, reason=f"Gemini Error ({e})")

async def merge_subjects(s1: Subject, s2: Subject, session: AsyncSession):
    """Links two subjects into the same SubjectGroup."""
    if not s1.subject_group_id and not s2.subject_group_id:
        new_group = SubjectGroup(
            canonical_name=s1.name,
            department=s1.department
        )
        session.add(new_group)
        await session.flush() 
        s1.subject_group_id = new_group.id
        s2.subject_group_id = new_group.id

    elif s1.subject_group_id and not s2.subject_group_id:
        s2.subject_group_id = s1.subject_group_id

    elif not s1.subject_group_id and s2.subject_group_id:
        s1.subject_group_id = s2.subject_group_id

    elif s1.subject_group_id != s2.subject_group_id:
        old_group_id = s2.subject_group_id
        s2.subject_group_id = s1.subject_group_id
        
        stmt = select(Subject).where(Subject.subject_group_id == old_group_id)
        result = await session.execute(stmt)
        for subject in result.scalars().all():
            subject.subject_group_id = s1.subject_group_id


async def evaluate_and_merge_subjects(s1: Subject, s2: Subject, session: AsyncSession):
    """Evaluates a pair using Lexical (Fuzzy) + Semantic (ML Embeddings) + Gemini API / Manual Fallback."""
    fuzzy_score = float(fuzz.token_sort_ratio(s1.name, s2.name))
    semantic_score = get_semantic_similarity(s1.name, s2.name)
    combined_score = max(fuzzy_score, semantic_score)

    print(f"🔍 Comparing: '{s1.name}' ↔ '{s2.name}'")
    print(f"   Lexical: {fuzzy_score:.1f}% | Semantic ML: {semantic_score:.1f}% | Best: {combined_score:.1f}%")

    if combined_score >= 88.0:
        print("  ✅ HIGH CONFIDENCE MATCH: Auto-merging...\n")
        await merge_subjects(s1, s2, session)

    elif combined_score >= 60.0:
        print("  ❓ AMBIGUOUS MATCH: Delegating to Gemini API Reviewer...")
        should_merge = await gemini_review_match(s1, s2, combined_score)
        if should_merge:
            await merge_subjects(s1, s2, session)
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
                text("ALTER TABLE subjects ADD COLUMN subject_group_id INTEGER REFERENCES subject_groups(id);")
            )
            print("  ➕ Added missing subject_group_id column to subjects table.")
        except Exception:
            pass  # Column already exists

    async with AsyncSessionLocal() as session:
        stmt = select(Subject).options(selectinload(Subject.topics))
        result = await session.execute(stmt)
        subjects = result.scalars().all()
        print(f"📚 Loaded {len(subjects)} subjects from database (with syllabus topics!).\n")

        # Evaluate unique subject pairs
        for s1, s2 in itertools.combinations(subjects, 2):
            # KEY CHANGE FOR NEW SUBJECTS: 
            # Skip if BOTH subjects are already assigned to settled groups
            if s1.subject_group_id is not None and s2.subject_group_id is not None:
                continue

            await evaluate_and_merge_subjects(s1, s2, session)

        await session.commit()

        # FALLBACK: Assign standalone groups to any remaining unassigned subjects
        unassigned_stmt = select(Subject).where(Subject.subject_group_id.is_(None))
        unassigned_result = await session.execute(unassigned_stmt)
        unassigned_subjects = unassigned_result.scalars().all()

        if unassigned_subjects:
            print(f"📦 Creating standalone groups for {len(unassigned_subjects)} unassigned subjects...")
            for s in unassigned_subjects:
                standalone_group = SubjectGroup(
                    canonical_name=s.name,
                    department=s.department
                )
                session.add(standalone_group)
                await session.flush()
                s.subject_group_id = standalone_group.id

            await session.commit()

        print("\n✨ Subject grouping pipeline completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())