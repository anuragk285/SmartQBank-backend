from pydantic import BaseModel, Field
from typing import List
from typing import Optional

class SubjectCreate(BaseModel):
    name: str
    subject_code: str
    department: str
    semester: int
    regulation_code: str
    subject_content_id: Optional[int] = None

class SubjectResponse(BaseModel):
    id: int
    name: str
    subject_code: str
    department: str 
    semester: int
    regulation_code: str
    question_count: int
    subject_content_id: int
    class Config:
        from_attributes = True 

class QuestionCreate(BaseModel):
    text: str
    subject_code: str
    unit: int
    difficulty: str
    year: int 
    marks: int
    image_urls: List[str] = Field(default_factory=list)
    topic_id: int

class QuestionResponse(BaseModel):
    id: int
    text: str
    subject_code: str
    unit: int
    difficulty: str
    year: int
    marks: int
    image_urls: List[str] = []
    topic: str
    topic_id: int

    class Config:
        from_attributes = True


class PaperAppearancesCreate(BaseModel):
    year: int
    paper_name: str
    question_id: int

class PaperAppearancesResponse(BaseModel):
    id: int
    question_id: int
    year: int
    paper_name: str
    
    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    email: str
    password: str
    is_admin: bool

class UserResponse(BaseModel):
    id: int
    email: str
    is_admin: bool

    class Config:
        from_attributes = True
    
class DeleteSubjectRequest(BaseModel):
    admin_password: str

class TopicCreate(BaseModel):
    topic: str
    subject_code: str
    unit: int
    regulation_code: str
    embedding: Optional[list[float]] = None
    match_status: str = "unmatched"
    match_confidence: Optional[float] = None
    canonical_topic_id: Optional[int] = None

class TopicResponse(BaseModel):
    id: int
    topic: str
    subject_code: str
    unit: int
    regulation_code: str
    # embedding: List[float]
    # match_status: str
    # match_confidence: float
    # canonical_topic_id: int

class PaginatedQuestions(BaseModel):
    questions: List[QuestionResponse]
    total: int
    page: int
    page_size: int
    has_more: bool

    class Config:
        from_attributes = True

class ImportantTopicOut(BaseModel):
    topic: str
    question_count: int
    total_marks: int
    years_appeared: int
    weightage_percent: float