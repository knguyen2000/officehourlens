# OfficeHourLens – Hackathon Project

Student Success track – **Claude for Good Hackathon**

> AI-augmented office hours: students get instant suggestions while still keeping humans in the loop.

---

## What this does

- Students:
  - Enter their name, course, and question to join the office hours queue.
  - Immediately get an AI-generated **suggested answer** based on course docs and FAQ.
- TAs:
  - See a live queue of students and their questions.
  - Mark questions as **in progress** or **done**.
  - Save final answers into a growing **FAQ**, which improves suggestions over time.

Everything runs on open-source components. The LLM is accessed via **Ollama** (e.g., `llama3.2`). No OpenAI / Claude APIs are used.

---

## Folder layout

- `backend/` – FastAPI app, database models, and LLM wrapper.
- `frontend/` – Static HTML/CSS/JS single-page interface.

---

## Quickstart

1. **Install Python dependencies**

```bash
pip install -r backend/requirements.txt
```

2. **Run an open-source LLM with Ollama** (optional but recommended)

Install Ollama, then:

```bash
ollama pull llama3.2
ollama serve
```

You can configure the model and base URL:

```bash
export OLLAMA_MODEL=llama3.2
export OLLAMA_BASE_URL=http://localhost:11434
```

If no LLM is available, the app will still run; AI answers will fall back to a generic message.

3. **Start the backend**

```bash
cd backend
uvicorn main:app --reload
```

4. **Seed some sample data (course docs + FAQ)**

In a new terminal:

```bash
curl -X POST http://127.0.0.1:8000/api/seed_sample
```

5. **Open the UI**

Visit:

http://127.0.0.1:8000

Use the **Student** tab to submit a question, and the **TA** tab to see and manage the queue.

---

## Demo storyline

1. As a **student**, paste a question like:

> I'm confused about how to interpret the weights in linear regression for HW1.

2. Show the immediate **AI suggestion** and explain that this may help some students leave the queue early.

3. As a **TA**, switch to the TA tab, refresh the queue, and:
   - Click **Start** when you begin helping a student.
   - Click **Done** and save a short summary answer, which gets added to the FAQ.

4. Explain how future questions benefit from a richer FAQ and course docs.

You can tune prompts, styling, and data models to match your specific course or university setting.
# officehourlens
