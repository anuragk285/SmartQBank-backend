import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select

from database import SessionLocal, engine
from models import Base, Topic
from schemas import TopicCreate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("topic_loader")

TARGET_FOLDER = "pipelines/extracted_topics"
target_folder = Path(TARGET_FOLDER)

async def topic_loader() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        for file_path in target_folder.glob("*.json"):
            try:
                # with open(file_path, "r", encoding="utf-8") as f:
                #     clean_lines = [
                #         line for line in f
                #         if not line.lstrip().startswith("//") and not line.lstrip().startswith("#")
                #     ]
                #     data = json.loads("".join(clean_lines))
                with open(file_path, 'r') as f:
                    data = json.load(f)

                regulation_code = file_path.stem 
                added_count = 0
                for subject_code, units in data.items():
                    for unit in units:
                        unit_num = unit["unit"]
                        for topic_text in unit["topics"]:
                            stmt = select(Topic).where(
                                Topic.subject_code == subject_code,
                                Topic.regulation_code == regulation_code,
                                Topic.unit == unit_num,
                                Topic.topic == topic_text,
                            )
                            result = await db.execute(stmt)
                            db_topic = result.scalar_one_or_none()

                            if db_topic:
                                # logger.debug(
                                #     "[%s] %s (Unit %s) already exists in %s",
                                #     regulation_code, topic_text, unit_num, subject_code
                                # )
                                continue

                            validated_data = TopicCreate(
                                subject_content_id=None,  
                                subject_code=subject_code,
                                unit=unit_num,
                                topic=topic_text,
                                regulation_code=regulation_code,
                                canonical_topic_id=None,
                                embedding=None,
                                match_status="unmatched",
                                match_confidence=None,
                            )

                            new_topic = Topic(**validated_data.model_dump())
                            db.add(new_topic)
                            added_count += 1
                await db.commit()
                logger.info("Successfully loaded %d new topic(s) from %s", added_count, file_path.name)

            except Exception as e:
                await db.rollback()
                #logger.error("Error processing file %s: %s", file_path.name, e)
                raise


if __name__ == "__main__":
    asyncio.run(topic_loader())