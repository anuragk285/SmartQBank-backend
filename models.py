from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, UniqueConstraint, Float
from database import Base
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import relationship

class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    subject_code = Column(String, nullable=False, index=True)  # NOT globally unique — same code can repeat across departments
    department = Column(String, nullable=False, index=True)
    semester = Column(Integer, nullable=False, index=True)
    regulation_code = Column(String, nullable=False, index=True)
    subject_content_id = Column(Integer, ForeignKey("subject_contents.id"), nullable=False, index=True)  # replaces subject_group_id
    content = relationship("SubjectContent", back_populates="subjects")
    __table_args__ = (
        UniqueConstraint("subject_code", "department", "regulation_code", name="uq_subject_code_dept_regulation"),
    )
       
class Topic(Base):
    __tablename__ = "topics"
    id = Column(Integer, primary_key=True, index=True)
    subject_content_id = Column(Integer, ForeignKey("subject_contents.id"), nullable=True, index=True)
    subject_code = Column(String, nullable=False, index=True) 
    unit = Column(Integer, nullable=False)
    topic = Column(String, nullable=False, index=True)
    canonical_topic_id = Column(Integer, ForeignKey("canonical_topics.id"), nullable=True, index=True)
    embedding = Column(JSON, nullable=True)
    match_status = Column(String, nullable=False, default="unmatched", index=True)
    match_confidence = Column(Float, nullable=True)
    regulation_code = Column(String, nullable=False)

    subject_content = relationship("SubjectContent", back_populates="topics")
    canonical_topic = relationship("CanonicalTopic", back_populates="topics")
    questions = relationship("Question", back_populates="topic")

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
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    topic = relationship("Topic", back_populates="questions")

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

class SubjectGroup(Base):
    __tablename__ = "subject_groups"
    id = Column(Integer, primary_key=True)
    canonical_name = Column(String, nullable=False)
    department = Column(String, nullable=True)

    subject_contents = relationship("SubjectContent", back_populates="group")  # was `subjects`
    canonical_topics = relationship("CanonicalTopic", back_populates="subject_group")

class CanonicalTopic(Base):
    __tablename__ = "canonical_topics"
    id = Column(Integer, primary_key=True)
    subject_group_id = Column(Integer, ForeignKey("subject_groups.id"), nullable=False, index=True)
    label = Column(String, nullable=False)
    description = Column(String, nullable=True)  # NEW — grounds future incremental Gemini calls + nice for the admin UI

    subject_group = relationship("SubjectGroup", back_populates="canonical_topics")
    topics = relationship("Topic", back_populates="canonical_topic")

    __table_args__ = (
        UniqueConstraint("subject_group_id", "label", name="uq_canonical_topic_group_label"),
    )

class SubjectContent(Base):
    """
    The actual syllabus/content identity of a subject within one regulation -
    shared by every department that teaches it under the same subject_code.
    Topics live here, not on individual department Subject rows, so they
    aren't duplicated per department.
    """
    __tablename__ = "subject_contents"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    subject_code = Column(String, nullable=False, index=True)
    regulation_code = Column(String, nullable=False, index=True)
    subject_group_id = Column(Integer, ForeignKey("subject_groups.id"), nullable=True)  # moved here, see note below

    group = relationship("SubjectGroup", back_populates="subject_contents")
    subjects = relationship("Subject", back_populates="content")
    topics = relationship("Topic", back_populates="subject_content")

    __table_args__ = (
        UniqueConstraint("subject_code", "regulation_code", name="uq_subject_content_code_regulation"),
    )