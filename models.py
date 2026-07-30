from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import DeclarativeBase
from database import Base
from sqlalchemy.dialects.postgresql import JSON

class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    subject_code = Column(String, nullable=False, index=True)
    department = Column(String, nullable=False, index=True)
    semester = Column(Integer, nullable=False, index=True)
    regulation_code = Column(String, nullable=False, index=True)

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True, index=True)
    text = Column(String, nullable=False)
    subject_code = Column(String, nullable=False, index=True)
    unit = Column(Integer, nullable=False, index=True)
    difficulty = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    marks = Column(Integer, nullable=False)
    image_urls = Column(JSON, nullable=True)
    topic = Column(String, nullable=False, index=True)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True)
    hash_password = Column(String, nullable=False)
    is_admin = Column(Boolean, nullable=False)

class PaperAppearances(Base):
    __tablename__ = "paper_appearances"
    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    year = Column(Integer, nullable=False)
    paper_name = Column(String, nullable=False, index=True)
   
class Topic(Base):
    __tablename__ = "topics"
    id = Column(Integer, primary_key=True, index=True)
    subject_code = Column(String, nullable=False)
    unit = Column(Integer, nullable=False)
    topic = Column(String, nullable=False)
