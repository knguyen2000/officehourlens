from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine
from llm_client import LLMClient
from models import CourseDoc, FAQEntry, Question
from schemas import (
    CourseDocCreate,
    CourseDocOut,
    FAQEntryOut,
    QuestionCreate,
    QuestionOut,
    QuestionResolve,
    QuestionStatusUpdate,
    QueueResponse,
)

import math

Base.metadata.create_all(bind=engine)

# Add cluster_name column if it doesn't exist (migration)
try:
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    faq_columns = [col['name'] for col in inspector.get_columns('faq_entries')]
    
    if 'cluster_name' not in faq_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE faq_entries ADD COLUMN cluster_name VARCHAR(200)'))
            conn.commit()
            print("[Migration] Added cluster_name column to faq_entries table")
except Exception as e:
    print(f"[Migration] Note: {e}")
    pass

app = FastAPI(title="OfficeHourLens", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


llm_client = LLMClient()


def _get_queue_position(db: Session, question: Question) -> int:
    count = (
        db.query(Question)
        .filter(Question.status.in_(["waiting", "in_progress"]))
        .filter(Question.created_at <= question.created_at)
        .count()
    )
    return count


def _find_relevant_contexts(db: Session, question_text: str, top_k: int = 5):
    docs: List[CourseDoc] = db.query(CourseDoc).all()
    faqs: List[FAQEntry] = db.query(FAQEntry).all()

    candidates: List[dict] = []
    for d in docs:
        candidates.append(
            {
                "label": f"Doc: {d.title}",
                "text": d.content[:600],
                "score": 0,
            }
        )
    for f in faqs:
        combined = f"Q: {f.question} \nA: {f.answer}"
        candidates.append(
            {
                "label": "FAQ",
                "text": combined[:600],
                "score": 0,
            }
        )

    q_words = set(question_text.lower().split())
    for c in candidates:
        text_words = set(c["text"].lower().split())
        overlap = q_words.intersection(text_words)
        c["score"] = len(overlap)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    if not candidates:
        return []
    non_zero = [c for c in candidates if c["score"] > 0]
    chosen = non_zero[:top_k] if non_zero else candidates[:2]
    return chosen


@app.post("/api/questions", response_model=QuestionOut)
def create_question(payload: QuestionCreate, db: Session = Depends(get_db)):
    q = Question(
        student_name=payload.student_name,
        course=payload.course,
        question_text=payload.question_text,
        status="waiting",
        created_at=datetime.utcnow(),
    )
    db.add(q)
    db.commit()
    db.refresh(q)

    contexts = _find_relevant_contexts(db, q.question_text, top_k=5)
    ai_answer = llm_client.answer_with_context(q.question_text, contexts)
    q.ai_answer = ai_answer
    if contexts:
        labels = [c["label"] for c in contexts]
        q.ai_sources = ", ".join(labels)

    db.add(q)
    db.commit()
    db.refresh(q)

    return q


@app.get("/api/questions/{question_id}", response_model=QuestionOut)
def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    return q


@app.delete("/api/questions/{question_id}")
def delete_question(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    db.delete(q)
    db.commit()
    return {"ok": True}


@app.get("/api/queue", response_model=QueueResponse)
def get_queue(db: Session = Depends(get_db)):
    qs = (
        db.query(Question)
        .filter(Question.status.in_(["waiting", "in_progress"]))
        .order_by(Question.created_at.asc())
        .all()
    )
    # Convert ORM objects to Pydantic models
    question_outs = [QuestionOut.model_validate(q) for q in qs]
    return QueueResponse(questions=question_outs)


@app.post("/api/questions/{question_id}/status", response_model=QuestionOut)
def update_status(question_id: int, payload: QuestionStatusUpdate, db: Session = Depends(get_db)):
    if payload.status not in {"waiting", "in_progress", "done"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    q.status = payload.status
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


@app.post("/api/questions/{question_id}/resolve", response_model=QuestionOut)
def resolve_question(question_id: int, payload: QuestionResolve, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    q.resolved_answer = payload.resolved_answer
    q.status = "done"
    db.add(q)
    db.commit()
    db.refresh(q)

    if payload.save_to_faq:
        # Check for similar existing FAQs to avoid duplicates
        existing_faqs = db.query(FAQEntry).all()
        is_duplicate = False
        
        q_words = set(q.question_text.lower().split())
        for existing in existing_faqs:
            existing_words = set(existing.question.lower().split())
            overlap = len(q_words.intersection(existing_words))
            similarity = overlap / max(len(q_words), len(existing_words)) if max(len(q_words), len(existing_words)) > 0 else 0
            
            # If 70% similar, consider it a duplicate
            if similarity > 0.7:
                is_duplicate = True
                break
        
        if not is_duplicate:
            entry = FAQEntry(question=q.question_text, answer=q.resolved_answer)
            db.add(entry)
            db.commit()
            # Cluster FAQs after adding new one
            _cluster_faqs(db)

    return QuestionOut.model_validate(q)


def _cluster_faqs(db: Session):
    """Cluster FAQs based on word overlap and semantic similarity."""
    faqs = db.query(FAQEntry).all()
    if len(faqs) < 2:
        return
    
    # Reset clusters
    for faq in faqs:
        faq.cluster_id = None
        faq.cluster_name = None
    
    cluster_id = 0
    clustered = set()
    clusters_to_name = []  # Store clusters that need names
    
    # Remove common stop words for better matching
    stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 
                  'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
                  'can', 'could', 'may', 'might', 'must', 'i', 'you', 'he', 'she', 'it',
                  'we', 'they', 'what', 'which', 'who', 'when', 'where', 'why', 'how',
                  'to', 'from', 'in', 'on', 'at', 'by', 'for', 'with', 'about', 'as'}
    
    for i, faq1 in enumerate(faqs):
        if faq1.id in clustered:
            continue
        
        # Start potential cluster
        cluster_members = [faq1]
        clustered.add(faq1.id)
        
        # Filter out stop words and get meaningful words
        words1 = set(w for w in faq1.question.lower().split() if w not in stop_words and len(w) > 2)
        
        # Find similar FAQs
        for faq2 in faqs[i+1:]:
            if faq2.id in clustered:
                continue
            
            words2 = set(w for w in faq2.question.lower().split() if w not in stop_words and len(w) > 2)
            
            if not words1 or not words2:
                continue
                
            overlap = len(words1.intersection(words2))
            union = len(words1.union(words2))
            
            # Jaccard similarity: intersection / union
            similarity = overlap / union if union > 0 else 0
            
            # If 30% similar (more lenient), add to same cluster
            if similarity > 0.3 and overlap >= 2:  # At least 2 words in common
                cluster_members.append(faq2)
                clustered.add(faq2.id)
        
        # Only assign cluster ID if there are multiple members
        if len(cluster_members) > 1:
            for faq in cluster_members:
                faq.cluster_id = cluster_id
            clusters_to_name.append((cluster_id, cluster_members))
            cluster_id += 1
        # Otherwise leave cluster_id as None (unclustered)
    
    # Generate meaningful names for clusters using LLM
    for cid, members in clusters_to_name:
        questions = [m.question for m in members]
        cluster_name = _generate_cluster_name(questions)
        for faq in members:
            faq.cluster_name = cluster_name
    
    db.commit()


def _generate_cluster_name(questions: list[str]) -> str:
    """Use LLM to generate a meaningful name for a cluster of questions."""
    questions_text = "\n".join([f"- {q}" for q in questions[:5]])  # Limit to 5 questions
    
    prompt = (
        "You are analyzing student questions from a course. Below are related questions that have been grouped together. "
        "Generate a short, descriptive topic name (2-5 words) that captures the main theme of these questions.\n\n"
        "Questions:\n"
        f"{questions_text}\n\n"
        "Topic name (2-5 words only):"
    )
    
    try:
        topic = llm_client._generate(prompt, max_tokens=50).strip()
        # Clean up the response - take first line, remove quotes, limit length
        topic = topic.split('\n')[0].strip('"\' ').strip()
        if len(topic) > 50:
            topic = topic[:50].rsplit(' ', 1)[0] + '...'
        return topic if topic else "Related Questions"
    except Exception as e:
        print(f"[Clustering] Error generating cluster name: {e}")
        return "Related Questions"


@app.get("/api/faq", response_model=list[FAQEntryOut])
def list_faq(db: Session = Depends(get_db)):
    faqs = db.query(FAQEntry).order_by(FAQEntry.cluster_id.asc().nullsfirst(), FAQEntry.created_at.desc()).all()
    return [FAQEntryOut.model_validate(faq) for faq in faqs]


@app.post("/api/faq/cluster")
def cluster_faqs(db: Session = Depends(get_db)):
    """Manually trigger FAQ clustering."""
    _cluster_faqs(db)
    return {"ok": True, "message": "FAQs clustered successfully"}


@app.get("/api/course_docs", response_model=list[CourseDocOut])
def list_course_docs(db: Session = Depends(get_db)):
    docs = db.query(CourseDoc).all()
    return [CourseDocOut.model_validate(doc) for doc in docs]


@app.post("/api/course_docs", response_model=CourseDocOut)
def create_course_doc(payload: CourseDocCreate, db: Session = Depends(get_db)):
    doc = CourseDoc(
        title=payload.title,
        content=payload.content,
        source_type=payload.source_type,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return CourseDocOut.model_validate(doc)


@app.delete("/api/course_docs/{doc_id}")
def delete_course_doc(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(CourseDoc).filter(CourseDoc.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()
    return {"ok": True}


@app.post("/api/seed_sample")
def seed_sample_data(db: Session = Depends(get_db)):
    existing_docs = db.query(CourseDoc).count()
    if existing_docs == 0:
        doc1 = CourseDoc(
            title="HW1: Linear Regression",
            content="Homework 1 covers linear regression, mean squared error, gradient descent, and basic data preprocessing.",
            source_type="hw",
        )
        doc2 = CourseDoc(
            title="Syllabus: Intro to ML",
            content="Course covers supervised learning, regression, classification, neural networks. Homework is due on Fridays at 11:59 PM.",
            source_type="syllabus",
        )
        db.add_all([doc1, doc2])
        db.commit()

    existing_faqs = db.query(FAQEntry).count()
    if existing_faqs == 0:
        faq1 = FAQEntry(
            question="What should I focus on for the midterm?",
            answer="Focus on understanding linear regression, logistic regression, and how to interpret model coefficients. Practice past homework problems and review lecture slides.",
        )
        faq2 = FAQEntry(
            question="Can I submit homework late?",
            answer="You can submit homework up to 48 hours late with a small penalty. After that, submissions are not accepted unless you have prior approval.",
        )
        db.add_all([faq1, faq2])
        db.commit()

    return {"ok": True}


app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
