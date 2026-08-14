"""
backfill_subject_id.py

One-time backfill for Topic.subject_id after adding that FK column per the
models.py review.

subject_code is NOT globally unique - the same code can appear under
different departments (and possibly different regulations). This script
only auto-backfills Topic rows where subject_code currently maps to
exactly one Subject row. Anything ambiguous is left with subject_id NULL
and printed out for manual resolution - guessing wrong here would silently
attribute a topic to the wrong department's subject.

Run once, after adding the column:
    ALTER TABLE topics ADD COLUMN subject_id INTEGER REFERENCES subjects(id);

Then:
    python backfill_subject_id.py
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from sqlalchemy import select

from database import SessionLocal
from models import Subject, Topic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_subject_id")


async def build_subject_code_index(db) -> dict[str, list[Subject]]:
    result = await db.execute(select(Subject))
    by_code: dict[str, list[Subject]] = defaultdict(list)
    for subject in result.scalars().all():
        by_code[subject.subject_code].append(subject)
    return by_code


async def main() -> None:
    async with SessionLocal() as db:
        subject_code_index = await build_subject_code_index(db)

        result = await db.execute(select(Topic).where(Topic.subject_id.is_(None)))
        topics = list(result.scalars().all())

        updated = 0
        no_match: set[str] = set()
        ambiguous: dict[str, list[Subject]] = {}

        for topic in topics:
            candidates = subject_code_index.get(topic.subject_code, [])
            if len(candidates) == 1:
                topic.subject_id = candidates[0].id
                updated += 1
            elif len(candidates) == 0:
                no_match.add(topic.subject_code)
            else:
                ambiguous[topic.subject_code] = candidates

        await db.commit()

        logger.info("Backfilled %d/%d Topic rows unambiguously", updated, len(topics))

        if no_match:
            logger.warning(
                "%d subject_code value(s) matched no Subject row at all - these Topic rows are "
                "orphaned regardless of the ambiguity issue, worth a separate look: %s",
                len(no_match), sorted(no_match),
            )

        if ambiguous:
            logger.warning(
                "%d subject_code value(s) are ambiguous (match multiple Subject rows). "
                "Topic.subject_id left NULL for these - resolve by hand, e.g. by cross-checking "
                "which department's question paper each topic was actually extracted from:",
                len(ambiguous),
            )
            for code, candidates in ambiguous.items():
                logger.warning("  subject_code=%r -> %d candidates:", code, len(candidates))
                for c in candidates:
                    logger.warning(
                        "      subject.id=%s  dept=%s  regulation=%s  semester=%s  name=%r",
                        c.id, c.department, c.regulation_code, c.semester, c.name,
                    )
        else:
            logger.info("No ambiguous subject_code values found - backfill is complete.")


if __name__ == "__main__":
    asyncio.run(main())