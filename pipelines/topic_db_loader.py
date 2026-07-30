import json
from database import SessionLocal, engine
from models import Base, Topic
from pathlib import Path
from schemas import TopicCreate

TARGET_FOLDER = 'pipelines/extracted_topics'
target_folder = Path(TARGET_FOLDER)

def topic_loader():
    Base.metadata.create_all(bind=engine)
    for file_path in target_folder.glob("*.json"):
        try:
            with open(f'{file_path}', 'r', encoding='utf-8') as f:
                clean_lines = [
                    line for line in f if not line.lstrip().startswith('//') and not line.lstrip().startswith('#')
                ]
                data = json.loads(''.join(clean_lines))
            with SessionLocal() as db:
                for subject_code, units in data.items():
                    for unit in units:
                        for topic in unit['topics']:
                            db_topic = db.query(Topic).filter(Topic.subject_code == subject_code, Topic.unit == unit['unit'], Topic.topic == topic).first()
                            if db_topic:
                                print(f"{topic} already exists in {subject_code}")
                                continue
                            validated_data = TopicCreate(topic=topic, subject_code=subject_code, unit=unit['unit'])
                            db_topic = Topic(**validated_data.model_dump()) 
                            db.add(db_topic)
                            db.add(db_topic)
                            db.commit()
                            db.refresh(db_topic)
        except Exception as e:
            print("ERROR ", e)
            raise

topic_loader()

                        