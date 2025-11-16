from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import Base, SessionLocal, engine
from llm_client import LLMClient
from models import CourseDoc, FAQEntry, Question, CourseSettings # Import CourseSettings
from schemas import (
    CourseDocCreate,
    CourseDocOut,
    FAQEntryOut,
    QuestionCreate,
    QuestionOut,
    QuestionResolve,
    QuestionStatusUpdate,
    QueueResponse,
    CourseSettingsBase, # Import CourseSettings schemas
    CourseSettingsOut
)

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_similarity

Base.metadata.create_all(bind=engine)

# --- One-time Migrations ---
try:
    from sqlalchemy import inspect
    inspector = inspect(engine)
    
    # Migration for faq_entries table
    faq_columns = [col['name'] for col in inspector.get_columns('faq_entries')]
    
    if 'cluster_name' not in faq_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE faq_entries ADD COLUMN cluster_name VARCHAR(200)'))
            conn.commit()
            print("[Migration] Added cluster_name column to faq_entries table")
    
    if 'ask_count' not in faq_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE faq_entries ADD COLUMN ask_count INTEGER DEFAULT 1 NOT NULL'))
            conn.commit()
            print("[Migration] Added ask_count column to faq_entries table")

    # Migration for settings table
    if 'course_settings' not in inspector.get_table_names():
         # The create_all above should handle this, but we can seed it
         with SessionLocal() as db:
            if db.query(CourseSettings).count() == 0:
                db.add(CourseSettings(key="faq_threshold", value="2"))
                db.commit()
                print("[Migration] Seeded default settings")

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
    """Finds relevant docs and FAQs using semantic search or keyword fallback."""
    docs: List[CourseDoc] = db.query(CourseDoc).all()
    faqs: List[FAQEntry] = db.query(FAQEntry).all()

    q_embedding = llm_client.get_embedding(question_text)

    candidates: List[dict] = []
    for d in docs:
        candidates.append(
            {
                "label": f"Doc: {d.title}",
                "text": d.content[:600],
                "embedding": llm_client.get_embedding(d.content[:600]),
            }
        )
    for f in faqs:
        combined = f"Q: {f.question} \nA: {f.answer}"
        candidates.append(
            {
                "label": "FAQ",
                "text": combined[:600],
                "embedding": llm_client.get_embedding(combined[:600]),
            }
        )

    # If embeddings are working, use them
    if q_embedding and all(c.get("embedding") for c in candidates):
        q_vec = np.array(q_embedding).reshape(1, -1)
        for c in candidates:
            # Ensure embedding exists before trying to reshape
            if c.get("embedding"):
                c_vec = np.array(c["embedding"]).reshape(1, -1)
                c["score"] = cosine_similarity(q_vec, c_vec)[0][0]
            else:
                c["score"] = 0
    else:
        # Fallback to word overlap
        print("[Context] Warning: Embeddings failed, falling back to word overlap.")
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

@app.delete("/api/faq/all")
def delete_all_faqs(db: Session = Depends(get_db)):
    """Deletes all entries from the FAQ table."""
    try:
        num_deleted = db.query(FAQEntry).delete()
        db.commit()
        print(f"[FAQ] Deleted {num_deleted} entries.")
        return {"ok": True, "message": f"Deleted {num_deleted} FAQ entries."}
    except Exception as e:
        db.rollback()
        print(f"[FAQ] Error deleting all FAQs: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete all FAQs.")


@app.post("/api/questions/{question_id}/resolve", response_model=QuestionOut)
def resolve_question(question_id: int, payload: QuestionResolve, db: Session = Depends(get_db)):
    """Mark a question as 'done' and check if it should be added/updated in the FAQ."""
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    q.resolved_answer = payload.resolved_answer
    q.status = "done"
    db.add(q)
    db.commit()
    db.refresh(q)

    if payload.save_to_faq:
        existing_faqs = db.query(FAQEntry).all()
        q_embedding = llm_client.get_embedding(q.question_text)
        
        found_similar = False
        normalized_question_text = q.question_text.strip().lower()

        # 1. Try to find a simple, exact text match first.
        for existing in existing_faqs:
            if existing.question.strip().lower() == normalized_question_text:
                existing.ask_count += 1
                db.add(existing)
                found_similar = True
                print(f"[FAQ] Found exact text match (ID: {existing.id}), incrementing ask_count.")
                break
        
        # 2. If no exact match AND embeddings are working, try semantic search
        if not found_similar and q_embedding and existing_faqs:
            print("[FAQ] No exact match. Trying semantic search...")
            q_vec = np.array(q_embedding).reshape(1, -1)
            for existing in existing_faqs:
                existing_embedding = llm_client.get_embedding(existing.question)
                if not existing_embedding:
                    continue
                
                ex_vec = np.array(existing_embedding).reshape(1, -1)
                similarity = cosine_similarity(q_vec, ex_vec)[0][0]
                
                # If 80% similar, increment count
                if similarity > 0.8:
                    existing.ask_count += 1
                    db.add(existing)
                    found_similar = True
                    print(f"[FAQ] Found semantic match (ID: {existing.id}), incrementing ask_count.")
                    break
        elif not found_similar:
            print("[FAQ] No exact or semantic match found.")
        
        if not q_embedding:
            print("[FAQ] WARNING: Embedding model failed to return a vector. Semantic search was skipped.")

        # 3. If still no match, create a new entry
        if not found_similar:
            entry = FAQEntry(
                question=q.question_text, 
                answer=q.resolved_answer,
                ask_count=1
            )
            db.add(entry)
            print("[FAQ] Creating new FAQ entry.")

        db.commit()
        # Re-cluster FAQs after any change
        _cluster_faqs(db)

    return QuestionOut.model_validate(q)


def _cluster_faqs(db: Session):
    """Cluster FAQs based on semantic similarity using DBSCAN."""
    faqs = db.query(FAQEntry).all()
    if len(faqs) < 3: # Not enough data to cluster
        print("[Clustering] Not enough FAQs to cluster, skipping.")
        return

    embeddings = []
    faqs_with_embeddings = []
    print("[Clustering] Generating embeddings for all FAQs...")
    for faq in faqs:
        vec = llm_client.get_embedding(faq.question)
        if vec:
            embeddings.append(vec)
            faqs_with_embeddings.append(faq)
    
    if len(embeddings) < 2:
        print("[Clustering] Not enough embeddings generated, skipping.")
        return

    X = np.array(embeddings)
        
    # DBSCAN parameters:
    # 'metric' is 'cosine', so 'eps' is a measure of cosine distance (1 - similarity).
    # eps=0.4 means a cosine similarity of 1 - 0.4 = 0.6 is required to be "close".
    # 'min_samples'=2 means a cluster must have at least 2 questions.
    dbscan = DBSCAN(eps=0.4, min_samples=2, metric='cosine')
    dbscan.fit(X) 
    
    labels = dbscan.labels_
    # 'labels' is an array like [0, 0, 1, -1, 1]
    # -1 means "outlier" (unclustered)
    
    clusters = {}
    
    # Reset all cluster info first
    for faq in faqs:
        faq.cluster_id = None
        faq.cluster_name = None

    # Assign new cluster IDs
    for i, faq in enumerate(faqs_with_embeddings):
        cluster_id = int(labels[i])
        
        if cluster_id != -1: # Not an outlier
            faq.cluster_id = cluster_id
            if cluster_id not in clusters:
                clusters[cluster_id] = []
            clusters[cluster_id].append(faq)
            
    print(f"[Clustering] Found {len(clusters)} clusters and {np.sum(labels == -1)} outliers.")

    # 3. Generate meaningful names for clusters using LLM
    for cid, members in clusters.items():
        if len(members) > 1: # Only name if it's a real cluster
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
        # Clean up the response
        topic = topic.split('\n')[0].strip('"\' ').strip()
        if len(topic) > 50:
            topic = topic[:50].rsplit(' ', 1)[0] + '...'
        return topic if topic else "Related Questions"
    except Exception as e:
        print(f"[Clustering] Error generating cluster name: {e}")
        return "Related Questions"


@app.get("/api/faq", response_model=list[FAQEntryOut])
def list_faq(db: Session = Depends(get_db)):
    """Get all FAQs that meet the TA's configured threshold."""
    setting = db.query(CourseSettings).filter(CourseSettings.key == "faq_threshold").first()
    threshold = int(setting.value) if setting else 1 # Default to 1 if not set

    faqs = (
        db.query(FAQEntry)
        .filter(FAQEntry.ask_count >= threshold)
        .order_by(FAQEntry.cluster_id.asc().nullsfirst(), FAQEntry.created_at.desc())
        .all()
    )
    return [FAQEntryOut.model_validate(faq) for faq in faqs]


@app.post("/api/faq/cluster")
def cluster_faqs(db: Session = Depends(get_db)):
    """Manually trigger FAQ clustering."""
    print("[Clustering] Manual clustering trigger received.")
    _cluster_faqs(db)
    return {"ok": True, "message": "FAQs clustered successfully"}


@app.get("/api/settings", response_model=list[CourseSettingsOut])
def get_settings(db: Session = Depends(get_db)):
    settings = db.query(CourseSettings).all()
    return settings

@app.post("/api/settings")
def update_settings(payload: CourseSettingsBase, db: Session = Depends(get_db)):
    setting = db.query(CourseSettings).filter(CourseSettings.key == payload.key).first()
    if setting:
        setting.value = payload.value
        print(f"[Settings] Updated {payload.key} to {payload.value}")
    else:
        setting = CourseSettings(key=payload.key, value=payload.value)
        db.add(setting)
        print(f"[Settings] Created {payload.key} as {payload.value}")
    
    db.commit()
    return {"ok": True, "setting": setting}


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
    """Seed the database with sample data (idempotent)."""
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
            ask_count=1 
        )
        faq2 = FAQEntry(
            question="Can I submit homework late?",
            answer="You can submit homework up to 48 hours late with a small penalty. After that, submissions are not accepted unless you have prior approval.",
            ask_count=2
        )
        db.add_all([faq1, faq2])
        db.commit()

    return {"ok": True}


# Mount the static frontend
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    print("Starting OfficeHourLens server on http://0.0.0.0:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)