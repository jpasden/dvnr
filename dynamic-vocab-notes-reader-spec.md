# Dynamic Vocabulary Notes Reader — Project Spec (v2)

> v1 of this spec is archived at `dynamic-vocab-notes-reader-spec-v1.md`.
> v2 reflects the architecture as implemented after the January 2026 redesign.

## Overview

A web application for reading Spanish and Italian texts with interactive vocabulary lookup. Click any word while reading to see the word's lemma and English definition in the notes panel. Notes are ephemeral by design, encouraging users to print when they want a keepsake. Texts are managed exclusively by an admin and deployed as a static site.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend (admin only) | Python, FastAPI |
| Tokenization | Pure Python regex tokenizer (no spaCy) |
| Definitions | Claude API (claude-haiku-4-5-20251001), batched, on-demand |
| Local dictionary | Hand-curated ~316-entry known_lemmas_es.py (to be replaced by 10K Hermit Dave list) |
| Frontend | Jinja2 templates, vanilla JS, CSS |
| Database | SQLite (via SQLAlchemy) |
| Static site | dist/ committed to repo, served by nginx on Opalstack |
| Deployment | Opalstack — nginx serves dist/ as pure static files; no Python on server |

---

## Project Structure

```
dvnr/
├── app/
│   ├── main.py                   # FastAPI app entrypoint (admin + reader routes)
│   ├── models.py                 # SQLAlchemy TextEntry model
│   ├── database.py               # SQLite connection
│   ├── routers/
│   │   ├── admin.py              # Admin routes (localhost-only)
│   │   └── reader.py             # Static reader routes (if any)
│   ├── services/
│   │   ├── tokenizer.py          # Local regex tokenizer — instant, no API
│   │   ├── definition_fetcher.py # Claude Haiku API calls — batched, SSE streaming
│   │   ├── known_lemmas_es.py    # ~316 core Spanish lemmas + definitions
│   │   ├── publisher.py          # Bakes dist/ JSON files and index pages
│   │   └── slugify.py            # URL slug generation
│   └── templates/
│       ├── base.html
│       ├── admin/
│       │   ├── index.html        # Text list with published/created timestamps
│       │   ├── new.html          # Add text form (instant submit, no API)
│       │   └── confirm.html      # Review unknowns, fetch definitions, publish
│       └── ...
├── static/
│   ├── css/style.css
│   └── js/reader.js
├── dist/                         # Generated static site — committed to repo
│   ├── reader.html               # Single-page reader SPA
│   ├── es/index.html             # Spanish texts index
│   ├── it/index.html             # Italian texts index
│   ├── texts/{slug}.json         # One JSON file per published text
│   └── static/                  # Copied CSS/JS
├── instance/                     # SQLite DB (gitignored)
├── requirements.txt
├── .env                          # ANTHROPIC_API_KEY (gitignored)
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
| `parsed_json` | TEXT | JSON array of token objects (updated after definition fetch) |
| `slug` | TEXT UNIQUE | URL-safe slug, auto-generated from title, editable |
| `word_count` | INTEGER | Content word count |
| `published_at` | DATETIME | Null = draft; set on publish |
| `edited_tokens` | TEXT | Reserved for future inline token editing |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

---

## Token Schema

Tokens are stored as a JSON array in `parsed_json`. Each token:

```json
{
  "idx": 0,
  "text": "hablaron",
  "lemma": "hablar",
  "is_punct": false,
  "is_space": false,
  "is_newline": false,
  "is_title": false,
  "definition_lemma": "to speak, to talk"
}
```

- `is_title`: true for tokens on the first line if it is ≤15 words with no trailing period
- `definition_lemma`: populated either from `known_lemmas_es.py` (locally) or from the Claude API
- Space, newline, and punctuation tokens have no `lemma` or `definition_lemma`
- `lemma` is absent on tokens that had no definition returned

---

## Tokenizer (`services/tokenizer.py`)

Pure Python, no external dependencies.

- Splits on spaces, newlines, and punctuation using a regex: `(\r\n|\r|\n| |[^\w\s])`
- Each part becomes one token with appropriate boolean flags
- Title detection: first line of text, ≤15 words, no trailing period → `is_title=True` on those tokens
- `count_words(text)`: counts whitespace-separated words
- `get_content_words(tokens)`: returns non-space, non-newline, non-punct tokens

---

## Definition Fetcher (`services/definition_fetcher.py`)

- Model: `claude-haiku-4-5-20251001`
- Batches unknown words 30 at a time
- Each word in the batch includes its sentence context (scanned from surrounding tokens)
- Assistant prefill (`{`) used to force JSON output with no preamble
- Returns: `{surface_form: {lemma: str, definition: str}}`
- Async generator: yields one result dict per word for SSE streaming
- Errors: if Claude returns no definition for a word, the token is left without `definition_lemma`

---

## Local Dictionary (`services/known_lemmas_es.py`)

Currently ~316 hand-curated entries covering core Spanish vocabulary:
- Core verbs (ser, estar, tener, hacer, ir, etc.)
- Pronouns, articles, prepositions, conjunctions
- Common adverbs and adjectives

**Planned replacement:** 10,000-word frequency dictionary built from the [Hermit Dave FrequencyWords list](https://github.com/hermitdave/FrequencyWords/blob/master/content/2018/es/es_50k.txt) (OpenSubtitles corpus). One-time build via Claude Haiku API (~$0.20). Script to be written at `scripts/build_freq_dict/`.

Italian equivalent: same approach, using the [Italian Hermit Dave list](https://github.com/hermitdave/FrequencyWords/blob/master/content/2018/it/it_50k.txt). Flagged for later.

---

## Admin Routes (`routers/admin.py`)

All routes require `localhost` or `127.0.0.1` — no auth needed beyond that.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin` | List all texts with published/created timestamps |
| `GET` | `/admin/new` | New text form |
| `POST` | `/admin/new` | Tokenize locally, save draft, redirect to confirm |
| `GET` | `/admin/confirm/{id}` | Review page: highlights unknown words, shows counts |
| `GET` | `/admin/fetch/{id}` | SSE stream: fetch definitions for unknown words via Claude |
| `POST` | `/admin/update-meta/{id}` | Save title, author, slug edits |
| `POST` | `/admin/publish/{id}` | Bake JSON, regenerate index, set `published_at` |
| `POST` | `/admin/publish-all` | Re-bake all published texts and index |
| `POST` | `/admin/delete/{id}` | Delete text + its dist/ JSON file, regenerate index |

---

## Admin Workflow

1. **Add text** — paste raw text, enter title/author, select language (ES/IT toggle). Submit is instant (local tokenizer only, no API call).

2. **Confirm page** — shows:
   - Editable metadata: title, author, slug
   - Pills: language, word count, N known, N need API lookup
   - Full text preview with unknown words highlighted in red; known words shown normally
   - "Fetch definitions" button (hidden if all words already known)

3. **Fetch definitions** — clicking the button opens an SSE terminal:
   ```
   Fetching definitions for 47 word(s)…

   [1 / 47] ✓ secreto [secreto] — secret, mystery
   [2 / 47] ✓ alcanzar [alcanzar] — to reach, to achieve
   ...
   Done.
   ```
   - Unknown words turn green in the text preview as definitions arrive
   - Pure numbers are excluded from API lookup
   - Tokens are saved to DB on completion

4. **Publish** — button appears after fetch completes (or immediately if no unknowns). Sets `published_at`, bakes `dist/texts/{slug}.json`, regenerates index pages.

---

## Publisher (`services/publisher.py`)

- `publish_text(entry)` — writes `dist/texts/{slug}.json` with token array + metadata
- `publish_index()` — regenerates `dist/es/index.html` and `dist/it/index.html`
- `publish_all(db)` — re-bakes all published entries + index (used by "Publish All" button)

---

## Reader (`dist/reader.html`)

Single-page SPA loaded by nginx. Reads the slug from the URL hash (`#el-secreto-de-mateo`), fetches `dist/texts/{slug}.json`, renders the two-column reader view.

### Layout

```
┌──────────────────────────┬──────────────────────────┐
│  Title line              │  VOCABULARY NOTES         │
│                          │  ─────────────────────── │
│  The reading text goes   │  [notes appear here       │
│  here, word by word.     │   on click]               │
│                          │                           │
│                          │  [Print Notes button]     │
└──────────────────────────┴──────────────────────────┘
```

### Click Interaction

- Click a word → note entry appears in notes panel showing: surface form, lemma, definition
- Clicking the same word again scrolls to its note (no duplicate)
- Notes are prepended (newest at top)
- Each note has a ✕ dismiss button; "Clear all" clears panel
- No localStorage persistence — refresh = clean slate

### Print

`@media print` hides controls, shows both columns. `window.print()` triggered by Print button.

---

## Deployment

| Detail | Value |
|--------|-------|
| Live URL | `dvnr.johnpasden.com` |
| GitHub repo | `https://github.com/jpasden/dvnr` |
| Server | Opalstack, nginx serving `dist/` as static files |
| Admin | Run locally only — `uvicorn app.main:app` on localhost |
| Deploy | `git push` locally → `git pull` on Opalstack server |

- No Python running on the server — dist/ is pure static
- SQLite DB lives in `instance/` (gitignored)
- `.env` holds `ANTHROPIC_API_KEY` (gitignored, never on server)
- Admin is localhost-only by IP check — no HTTP auth needed

---

## Pending Work

- [ ] **10K Spanish frequency dictionary** — build script using Hermit Dave data, replace `known_lemmas_es.py`
- [ ] Italian language support (same architecture — needs `known_lemmas_it.py` from Hermit Dave)
- [ ] Delete dead files: `app/services/claude_parser.py`, `app/services/nlp.py`, `app/services/wiktionary.py`, `app/services/language.py`, `app/templates/admin/edit.html`, `app/templates/admin/preview.html`, `app/templates/partials/notes_entry.html`

---

## Out of Scope (for now)

- User-submitted texts (admin-only is intentional)
- User accounts or multi-user notes
- Mobile layout (desktop-first)
- Flashcard export
- Chinese language support
