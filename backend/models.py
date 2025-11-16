from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from database import Base


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    student_name = Column(String(100), nullable=False)
    course = Column(String(100), nullable=True)
    question_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String(20), default="waiting", nullable=False)  # waiting, in_progress, done
    ai_answer = Column(Text, nullable=True)
    ai_sources = Column(Text, nullable=True)
    resolved_answer = Column(Text, nullable=True)


class FAQEntry(Base):
    __tablename__ = "faq_entries"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    cluster_id = Column(Integer, nullable=True)  # For grouping similar questions
    cluster_name = Column(String(200), nullable=True)  # AI-generated cluster topic name
    ask_count = Column(Integer, default=1, nullable=False) # Count of how many times this was asked


class CourseDoc(Base):
    __tablename__ = "course_docs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    source_type = Column(String(50), nullable=False)  # syllabus, hw, slide, other

class CourseSettings(Base):
    __tablename__ = "course_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False) # e.g., "faq_threshold"
    value = Column(String(200), nullable=False) # e.g., "2"