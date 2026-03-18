# Dynamic Vocabulary Notes Reader (DVNR)

A web application for reading Spanish and Italian texts with interactive vocabulary lookup. Click any word while reading to instantly populate a notes panel with the word's form, infinitive (for verbs), and English definition. Notes are ephemeral by design — print to save.

## Setup

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

You must run `source .venv/bin/activate` every time you open a new terminal session.

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Download spaCy language models

```bash
python -m spacy download es_core_news_sm
python -m spacy download it_core_news_sm
```

### 4. Configure environment

```bash
cp .env.example .env
```

The default `.env` uses SQLite at `instance/dvnr.db`. No changes needed for local development.

### 5. Run the development server

```bash
python -m uvicorn app.main:app --reload
```

Note: use `python -m uvicorn` (not just `uvicorn`) to ensure the venv's uvicorn is used.

The app will be available at `http://127.0.0.1:8000`.

## Admin

Admin routes (`/admin`) are **localhost-only** by design. They are accessible only when running locally (requests from `127.0.0.1` or `::1`). Pre-parsed texts are added via the admin panel and committed to the repository; the live version serves them directly from the database.

To add a pre-prepared text:

1. Visit `http://127.0.0.1:8000/admin`
2. Click **Add New Text**
3. Paste the text, enter title/author, select language
4. Submit — the backend runs the full NLP + Wiktionary pipeline (5–30 seconds)
5. Preview the result, then commit the SQLite database or export as needed

## Production (Opalstack / Gunicorn)

```bash
gunicorn app.main:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

- Static files should be served by nginx pointing at the `static/` directory.
- The SQLite database lives at `instance/dvnr.db` (not tracked in git).
- Both spaCy models must be downloaded into the production virtualenv.

## Project structure

```
app/
  main.py              FastAPI app entry point
  database.py          SQLAlchemy setup
  models.py            Text model
  routers/
    reader.py          Public routes (/, /parse, /text/{id}, /detect-language)
    admin.py           Admin routes (/admin/*)
  services/
    nlp.py             spaCy pipeline + custom chunker
    wiktionary.py      Wiktionary definition fetcher
    language.py        Language auto-detection
  templates/           Jinja2 HTML templates
static/
  css/style.css        All styles (including print)
  js/reader.js         All reader interactivity
config/
  fixed_expressions_es.json
  fixed_expressions_it.json
instance/              SQLite DB lives here (not in git)
```
