# OfficeHourLens – Backend

FastAPI backend for the OfficeHourLens project.

## Setup

1. Create a virtual environment (or conda env) and install dependencies:

```bash
pip install -r backend/requirements.txt
```

2. (Optional but recommended) Start an open-source LLM via [Ollama](https://ollama.com/).

For example:

```bash
ollama pull llama3.2
ollama serve
```

You can override the defaults:

```bash
export OLLAMA_MODEL=llama3.2
export OLLAMA_BASE_URL=http://localhost:11434
```

If Ollama is not running, the backend will still work, but AI answers will fall back to a generic message.

3. Initialize the database and seed some sample course docs and FAQs:

```bash
cd backend
python -m uvicorn main:app --reload
```

Then hit:

```bash
curl -X POST http://127.0.0.1:8000/api/seed_sample
```

(or call this from your browser using a REST client).

4. The app also serves the frontend from the `../frontend` folder, so once uvicorn is running, open:

http://127.0.0.1:8000
