# Dynamic Vocabulary Notes Reader — Project Spec

## Overview

A web application for reading Spanish and Italian texts with interactive vocabulary lookup. Click any word while reading to instantly populate a notes panel on the right with the word's form, infinitive (if a verb), and English definition. Notes are ephemeral by design, encouraging users to print when they want a keepsake. Pre-prepared texts can be managed by an admin.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI |
| NLP | spaCy (`es_core_news_sm`, `it_core_news_sm`) |
| Definitions | Wiktionary REST API (pre-fetched at parse time) |
| Frontend | Jinja2 templates, vanilla JS, CSS |
| Database | SQLite (via SQLAlchemy) — for pre-prepared texts only |
| Deployment | Opalstack (shared hosting, WSGI via Gunicorn) |

---

## Project Structure

```
vocab-reader/
├── app/
│   ├── main.py               # FastAPI app entrypoint
│   ├── routers/
│   │   ├── reader.py         # Main reader routes
│   │   └── admin.py          # Admin routes
│   ├── services/
│   │   ├── nlp.py            # spaCy parsing, phrase chunking
│   │   ├── wiktionary.py     # Definition fetching
│   │   └── language.py       # Language auto-detection
│   ├── models.py             # SQLAlchemy models
│   ├── database.py           # DB connection
│   └── templates/
│       ├── base.html
│       ├── reader.html       # Main two-column reading view
│       ├── admin/
│       │   ├── index.html    # List of pre-prepared texts
│       │   ├── edit.html     # Add/edit a text
│       │   └── preview.html  # Preview parsed output
│       └── partials/
│           └── notes_entry.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── reader.js
├── requirements.txt
├── .env
└── README.md
```

---

## Database Schema

### `texts` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `title` | TEXT | Display title |
| `author` | TEXT | Optional |
| `language` | TEXT | `"es"` or `"it"` |
| `raw_text` | TEXT | Original pasted text |
| `parsed_json` | TEXT | JSON blob of parsed tokens/chunks |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

The `parsed_json` field stores the full NLP parse output so the reader view is instant (no re-parsing on load).

---

## NLP Pipeline (`services/nlp.py`)

### Input
Raw text string + language code (`"es"` or `"it"`).

### Processing Steps

1. **Tokenize** using spaCy's tokenizer for the selected language.
2. **POS-tag and lemmatize** — each token gets:
   - `text`: surface form
   - `lemma`: dictionary/infinitive form
   - `pos`: universal POS tag (VERB, NOUN, ADJ, etc.)
   - `is_punct`: boolean
   - `is_space`: boolean
3. **Phrase chunk detection** — use spaCy noun chunks and verb phrases to identify multi-word units. Mark each token with a `chunk_id` (shared among tokens belonging to the same chunk) and a `chunk_role` (`"start"`, `"middle"`, `"end"`, or `"solo"`).
4. **Definition fetch** — for each unique `(lemma, pos)` pair, call the Wiktionary API (see below). Results are stored per token in the parse output.
5. **Serialize** — output a JSON array of token objects.

### Token Object Schema (JSON)

```json
{
  "idx": 0,
  "text": "hablaron",
  "lemma": "hablar",
  "pos": "VERB",
  "is_punct": false,
  "is_space": false,
  "chunk_id": null,
  "chunk_role": "solo",
  "definition_surface": "they spoke (3rd pl. preterite of hablar)",
  "definition_lemma": "to speak, to talk"
}
```

For non-verbs, `definition_surface` is omitted and only `definition_lemma` is populated.

---

## Wiktionary Integration (`services/wiktionary.py`)

### Endpoint

```
GET https://en.wiktionary.org/api/rest_v1/page/definition/{word}
```

### Strategy

- Fetch for both the **surface form** and the **lemma** when they differ.
- Filter results by language (`"Spanish"` or `"Italian"`) and POS.
- Extract the first definition from the first matching entry.
- For verbs: store a short conjugation description as `definition_surface` (e.g., "3rd person plural preterite of hablar") — Wiktionary usually provides this for conjugated forms.
- Cache results in a simple in-memory dict during a parse run to avoid redundant API calls for repeated words.
- Gracefully handle missing entries: fall back to lemma definition only; if neither found, store `"(definition not found)"`.
- **Rate limit:** add a 0.1s sleep between unique word lookups to be polite to Wiktionary's servers.

---

## Language Detection (`services/language.py`)

Use the **`langdetect`** library (wrapper around Google's language-detect) for auto-detection on paste. This is fast, client-side-independent, and handles Spanish/Italian reliably.

```python
from langdetect import detect

def detect_language(text: str) -> str:
    lang = detect(text)
    if lang in ("es", "it"):
        return lang
    return "es"  # default fallback
```

The frontend sends the pasted text to a lightweight `/detect-language` endpoint and updates the language toggle immediately.

---

## API Routes (`routers/reader.py`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Home: paste form + pre-prepared text list |
| `POST` | `/parse` | Accept raw text + lang, validate word count (≤1000; return 400 if exceeded), run NLP pipeline, return reader view |
| `GET` | `/text/{id}` | Load a pre-prepared text into reader view |
| `POST` | `/detect-language` | Quick lang detection for auto-switch on paste |

## Admin Routes (`routers/admin.py`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin` | List all pre-prepared texts |
| `GET` | `/admin/new` | New text form |
| `POST` | `/admin/new` | Submit new text (triggers NLP parse + save) |
| `GET` | `/admin/edit/{id}` | Edit title/author/language/raw text |
| `POST` | `/admin/edit/{id}` | Save edits (re-triggers NLP parse) |
| `POST` | `/admin/delete/{id}` | Delete a text |
| `GET` | `/admin/preview/{id}` | Preview parsed reader view |

> **Note on Admin Auth:** For Opalstack deployment, protect `/admin` routes with HTTP Basic Auth via a middleware check against a value in `.env`. Simple but sufficient for a personal tool.

---

## Frontend — Reader View (`templates/reader.html`)

### Layout

Two equal-width columns, side by side:

```
┌──────────────────────────┬──────────────────────────┐
│  [Text size: S M L XL]   │  VOCABULARY NOTES         │
│  [ES | IT toggle]        │  ─────────────────────── │
│                          │  [notes appear here       │
│  The reading text goes   │   on click]               │
│  here, word by word,     │                           │
│  with phrase underlines. │                           │
│                          │  [Print Notes button]     │
└──────────────────────────┴──────────────────────────┘
```

### Text Rendering

Each token is rendered as a `<span>` with data attributes:

```html
<span
  class="token verb chunk-solo"
  data-idx="0"
  data-lemma="hablar"
  data-pos="VERB"
  data-def-surface="they spoke (3rd pl. preterite of hablar)"
  data-def-lemma="to speak, to talk"
>hablaron</span>
```

- Tokens within a **phrase chunk** are wrapped in a `<span class="chunk">`, which renders a light gray underline across the entire group.
- Punctuation tokens are rendered inline with `pointer-events: none`.
- Whitespace tokens render as literal spaces.

### Text Size Control

Four buttons: S / M / L / XL. Clicking applies a CSS class to the text column that sets `font-size`. Default: M.

### Language Toggle

A two-state switch (ES | IT). On the paste flow, this is auto-set by the `/detect-language` response. User can override at any time. **Changing language after a text is loaded triggers a re-parse** (with a confirmation dialog since this discards current notes).

---

## Frontend — Click Interaction (`static/js/reader.js`)

### On Token Click

1. Remove `.highlighted` from any previously highlighted token.
2. Add `.highlighted` class to the clicked token (CSS: bold, colored background).
3. Read `data-*` attributes from the span.
4. If this exact word is already in the notes list, scroll to it and pulse it (don't duplicate).
5. Otherwise, prepend a new note entry to the notes panel:

```
──────────────────────────
hablaron
  infinitive: hablar
  [conjugated form] they spoke (3rd pl. preterite)
  [infinitive] to speak, to talk
──────────────────────────
```

For non-verbs, only show the word and its definition (no infinitive line).

6. Clicking a word already in notes re-highlights it in the text and scrolls the note into view.

### Notes Panel

- Notes are prepended (newest at top).
- Each note has a small ✕ button to dismiss it individually.
- A "Clear all" button clears the entire panel.
- **No localStorage persistence.** Refresh = clean slate. A small banner at the top of the notes panel reminds the user: *"Notes are lost on refresh — print to save."*

---

## Print Styles

A dedicated `@media print` block in CSS:

- Hide all controls (text size buttons, language toggle, clear button, ✕ dismiss buttons, the reminder banner).
- Remove token highlighting.
- Display both columns side by side at full width.
- Keep phrase chunk underlines visible.
- Use a clean serif font for the text column, sans-serif for notes.
- Page margins: 1.5cm all sides.
- Notes panel gets a left border line for visual separation.

No special print route needed — `window.print()` triggered by the Print button handles this.

---

## Phrase Chunk Styling

```css
.chunk {
  display: inline;
  border-bottom: 2px solid #ccc;
  padding-bottom: 1px;
}

.token.highlighted {
  background-color: #fff3b0;
  border-radius: 3px;
  font-weight: bold;
}

.token:not(.is-punct):hover {
  cursor: pointer;
  background-color: #f0f0f0;
  border-radius: 3px;
}
```

---

## Pre-Prepared Text Admin Flow

1. Admin visits `/admin/new`.
2. Pastes raw text, enters title/author, selects or auto-detects language.
3. On submit: backend runs full NLP + Wiktionary pipeline, stores `parsed_json` in DB.
4. A preview link lets admin verify the parsed output before it appears on the home page.
5. Parse time may be 5–30 seconds depending on text length and Wiktionary lookups — show a loading spinner; this is a one-time cost per text.

---

## Home Page Flow (Paste)

1. User visits `/`.
2. A text area invites pasting. A language toggle (ES | IT) shows, defaulting to ES.
3. On paste event: JS calls `/detect-language` with the pasted text, updates the toggle.
4. JS counts words in the pasted text in real time and displays a live word count below the text area:
   - **0–500 words:** no warning; submit button enabled.
   - **501–1000 words:** amber warning banner — *"Long text detected (~N words). Parsing may take 30+ seconds."* Submit button remains enabled.
   - **>1000 words:** large red error banner — *"Text too long (N words). Please paste 1,000 words or fewer."* Submit button is **disabled** until the user edits the text below the limit.
5. On submit (if allowed): POST to `/parse`. A full-screen loading overlay appears with a spinner and the message *"Parsing your text… this may take up to 30 seconds for longer passages."*
6. Below the paste area: a list of pre-prepared texts as clickable cards.

---

## Dependencies (`requirements.txt`)

```
fastapi
uvicorn[standard]
gunicorn
jinja2
python-multipart
spacy
# run: python -m spacy download es_core_news_sm
# run: python -m spacy download it_core_news_sm
langdetect
sqlalchemy
httpx          # async Wiktionary requests
python-dotenv
```

---

## Opalstack Deployment Notes

| Detail | Value |
|--------|-------|
| Frontend URL | `dvnr.johnpasden.com` |
| Backend path | `~/apps/dvnr/` |
| GitHub repo | `https://github.com/jpasden/dvnr` |
| App type | Python WSGI (Gunicorn + Uvicorn workers) |

- spaCy models are downloaded once to the virtualenv and persist.
- SQLite DB file lives in `instance/` outside the repo root.
- The `.env` file holds `ADMIN_USER`, `ADMIN_PASS`, and `DATABASE_URL`.
- Static files served directly by Opalstack's nginx layer (point `/static` at the `static/` dir).
- On first deploy, run the DB migration script and download both spaCy models.

---

## Out of Scope (for now)

- Chinese language support (planned future phase)
- User accounts / multi-user notes
- Saving notes to a file (print-to-PDF is the intended workflow)
- Mobile layout (desktop-first)
- Flashcard export
