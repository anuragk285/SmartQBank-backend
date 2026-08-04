from pydantic import BaseModel, Field
from typing import List

class SubjectCreate(BaseModel):
    name: str
    subject_code: str
    department: str
    semester: int
    regulation_code: str

class SubjectResponse(BaseModel):
    id: int
    name: str
    subject_code: str
    department: str 
    semester: int
    regulation_code: str
    question_count: int
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

class TopicResponse(BaseModel):
    id: int
    topic: str
    subject_code: str
    unit: int

class PaginatedQuestions(BaseModel):
    questions: List[QuestionResponse]
    total: int
    page: int
    page_size: int
    has_more: bool

    class Config:
        from_attributes = True