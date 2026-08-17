from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from sqlalchemy import select

from database import SessionLocal
from models import Subject, Topic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("diagnose_unresolved_topics")


async def main() -> None:
    async with SessionLocal() as db:
        topics_result = await db.execute(select(Topic).where(Topic.subject_content_id.is_(None)))
        topics = list(topics_result.scalars().all())

        subjects_result = await db.execute(select(Subject))
        subjects = list(subjects_result.scalars().all())

    by_code: dict[str, set[str]] = defaultdict(set)
    for s in subjects:
        by_code[s.subject_code].add(s.regulation_code)

    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for t in topics:
        pairs[(t.subject_code, t.regulation_code)] += 1

    likely_mismatch: list[tuple[str, str, set[str], int]] = []
    truly_orphaned: list[tuple[str, str, int]] = []

    for (code, reg), count in sorted(pairs.items()):
        existing_regs = by_code.get(code)
        if existing_regs:
            likely_mismatch.append((code, reg, existing_regs, count))
        else:
            truly_orphaned.append((code, reg, count))

    logger.info("Total unresolved (subject_code, regulation_code) pairs: %d", len(pairs))

    if likely_mismatch:
        logger.warning(
            "\n%d pair(s): subject_code EXISTS on Subject, but under a DIFFERENT regulation_code - "
            "check whether this is a real regulation variant or a naming inconsistency:",
            len(likely_mismatch),
        )
        for code, reg, existing_regs, count in likely_mismatch:
            logger.warning(
                "  subject_code=%r  Topic says regulation=%r (%d topics)  |  Subject has regulation(s)=%s",
                code, reg, count, sorted(existing_regs),
            )

    if truly_orphaned:
        logger.warning(
            "\n%d pair(s): NO Subject row exists for this subject_code under ANY regulation - "
            "genuinely orphaned topics, no matching subject was ever created:",
            len(truly_orphaned),
        )
        for code, reg, count in truly_orphaned:
            logger.warning("  subject_code=%r regulation=%r (%d topics)", code, reg, count)


if __name__ == "__main__":
    asyncio.run(main())