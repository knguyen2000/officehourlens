from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class QuestionCreate(BaseModel):
    student_name: str
    course: Optional[str] = None
    question_text: str


class QuestionOut(BaseModel):
    id: int
    student_name: str
    course: Optional[str]
    question_text: str
    created_at: datetime
    status: str
    ai_answer: Optional[str]
    ai_sources: Optional[str]
    resolved_answer: Optional[str]

    class Config:
        from_attributes = True


class QuestionStatusUpdate(BaseModel):
    status: str  # waiting, in_progress, done


class QuestionResolve(BaseModel):
    resolved_answer: str
    save_to_faq: bool = True


class QueueResponse(BaseModel):
    questions: List[QuestionOut]


class FAQEntryOut(BaseModel):
    id: int
    question: str
    answer: str
    created_at: datetime
    cluster_id: Optional[int] = None
    cluster_name: Optional[str] = None

    class Config:
        from_attributes = True


class CourseDocCreate(BaseModel):
    title: str
    content: str
    source_type: str  # syllabus, hw, slide, lecture_notes, other


class CourseDocOut(BaseModel):
    id: int
    title: str
    content: str
    source_type: str

    class Config:
        from_attributes = True
