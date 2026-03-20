import json
import re
from typing import Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TextEntry
from app.services.tokenizer import tokenize, count_words, get_content_words
from app.services.definition_fetcher import fetch_definitions
from app.services.known_lemmas_es import KNOWN_LEMMAS_ES
from app.services.freq_dict_es import FREQ_DICT_ES
from app.services.freq_dict_it import FREQ_DICT_IT
from app.services.publisher import publish_text, publish_index
from app.services.slugify import slugify

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["add_hours"] = lambda dt, h: dt + timedelta(hours=h)

# Merge hand-curated lemmas on top of freq dicts (hand-curated takes precedence)
_KNOWN: dict[str, dict[str, str]] = {
    "es": {**FREQ_DICT_ES, **KNOWN_LEMMAS_ES},
    "it": FREQ_DICT_IT,
}


def _require_local(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    allowed = {"127.0.0.1", "::1", "localhost"}
    if client_host not in allowed:
        forwarded_hosts = [h.strip() for h in forwarded_for.split(",")]
        if not any(h in allowed for h in forwarded_hosts):
            raise HTTPException(status_code=403, detail="Admin routes are accessible from localhost only.")


def _make_unique_slug(base_slug: str, db: Session, exclude_id: Optional[int] = None) -> str:
    candidate = base_slug
    counter = 2
    while True:
        q = db.query(TextEntry).filter(TextEntry.slug == candidate)
        if exclude_id is not None:
            q = q.filter(TextEntry.id != exclude_id)
        if not q.first():
            return candidate
        candidate = f"{base_slug}-{counter}"
        counter += 1


def _sentence_for_token(tokens: list[dict], target_idx: int) -> str:
    """
    Return the sentence containing the token at target_idx.
    Sentence boundaries are ., !, ?, newlines.
    """
    # Find sentence start (scan backwards)
    start = 0
    for i in range(target_idx - 1, -1, -1):
        tok = tokens[i]
        if tok["is_newline"] or tok["text"] in (".", "!", "?"):
            start = i + 1
            break

    # Find sentence end (scan forwards)
    end = len(tokens)
    for i in range(target_idx + 1, len(tokens)):
        tok = tokens[i]
        if tok["is_newline"] or tok["text"] in (".", "!", "?"):
            end = i + 1
            break

    return "".join(t["text"] for t in tokens[start:end]).strip()


def _classify_tokens(tokens: list[dict], language: str) -> tuple[list[dict], list[dict]]:
    """
    Split content words into known (in local dict) and unknown (need API).
    Returns (known_tokens, unknown_tokens).
    Each token gets a 'known' bool added in-place.
    """
    known_dict = _KNOWN.get(language, {})
    content = get_content_words(tokens)
    known, unknown = [], []
    seen_unknown: set[str] = set()  # deduplicate by lowercase surface form

    for tok in content:
        surface = tok["text"].lower().rstrip(".,;:!?\"'")
        # Treat pure numbers as known (no definition needed)
        if surface.replace(",", "").replace(".", "").isdigit():
            tok["known"] = True
            known.append(tok)
            continue
        if surface in known_dict:
            tok["known"] = True
            known.append(tok)
        else:
            tok["known"] = False
            # Only request definition once per unique surface form
            if surface not in seen_unknown:
                seen_unknown.add(surface)
                unknown.append(tok)

    return known, unknown


# ---------------------------------------------------------------------------
# GET /admin — list all texts
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def admin_index(request: Request, db: Session = Depends(get_db)):
    _require_local(request)
    texts = db.query(TextEntry).order_by(TextEntry.created_at.desc()).all()
    return templates.TemplateResponse("admin/index.html", {
        "request": request,
        "texts": texts,
    })


# ---------------------------------------------------------------------------
# GET /admin/new — new text form
# ---------------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def admin_new_form(request: Request):
    _require_local(request)
    return templates.TemplateResponse("admin/new.html", {"request": request})


# ---------------------------------------------------------------------------
# POST /admin/new — tokenize locally, save, redirect to confirm
# ---------------------------------------------------------------------------

TEXT_TYPES = ["story", "song", "dialogue", "email", "poem"]


@router.post("/new")
async def admin_new_submit(
    request: Request,
    title: str = Form(...),
    author: str = Form(""),
    source: str = Form(""),
    text_type: str = Form("story"),
    language: str = Form("es"),
    raw_text: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_local(request)

    if language not in ("es", "it"):
        language = "es"
    if text_type not in TEXT_TYPES:
        text_type = "story"

    tokens = tokenize(raw_text)
    wc = count_words(raw_text)
    base_slug = slugify(title)
    unique_slug = _make_unique_slug(base_slug, db)

    entry = TextEntry(
        title=title,
        author=author.strip() if author.strip() else None,
        source=source.strip() if source.strip() else None,
        text_type=text_type,
        language=language,
        raw_text=raw_text,
        parsed_json=json.dumps(tokens, ensure_ascii=False),
        slug=unique_slug,
        word_count=wc,
        published_at=None,
        edited_tokens=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return RedirectResponse(url=f"/admin/confirm/{entry.id}", status_code=303)


# ---------------------------------------------------------------------------
# GET /admin/confirm/{id} — show text with unknown words highlighted
# ---------------------------------------------------------------------------

@router.get("/confirm/{text_id}", response_class=HTMLResponse)
async def admin_confirm(request: Request, text_id: int, db: Session = Depends(get_db)):
    _require_local(request)
    entry = db.query(TextEntry).filter(TextEntry.id == text_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Text not found")

    tokens = json.loads(entry.parsed_json)
    known, unknown = _classify_tokens(tokens, entry.language)

    return templates.TemplateResponse("admin/confirm.html", {
        "request": request,
        "entry": entry,
        "tokens": tokens,
        "known_count": len(known),
        "unknown_count": len(unknown),
        "text_types": TEXT_TYPES,
    })


# ---------------------------------------------------------------------------
# GET /admin/fetch/{id} — SSE stream: fetch definitions for unknown words
# ---------------------------------------------------------------------------

@router.get("/fetch/{text_id}")
async def admin_fetch(request: Request, text_id: int, db: Session = Depends(get_db)):
    _require_local(request)
    entry = db.query(TextEntry).filter(TextEntry.id == text_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Text not found")

    tokens = json.loads(entry.parsed_json)
    known_dict = _KNOWN.get(entry.language, {})

    # Fill known-lemma definitions directly
    for tok in tokens:
        if tok["is_space"] or tok["is_newline"] or tok["is_punct"]:
            continue
        surface = tok["text"].lower().rstrip(".,;:!?\"'")
        if surface in known_dict:
            tok["definition_lemma"] = known_dict[surface]

    # Collect unknown words with context
    seen: set[str] = set()
    unknown_words: list[dict] = []
    for tok in tokens:
        if tok["is_space"] or tok["is_newline"] or tok["is_punct"]:
            continue
        surface = tok["text"].lower().rstrip(".,;:!?\"'")
        if surface not in known_dict and surface not in seen:
            seen.add(surface)
            unknown_words.append({
                "idx": tok["idx"],
                "text": tok["text"],
                "context": _sentence_for_token(tokens, tok["idx"]),
            })

    async def event_stream():
        # Build a lookup from surface→list of token idxs for bulk-filling
        surface_to_idxs: dict[str, list[int]] = {}
        for tok in tokens:
            if tok["is_space"] or tok["is_newline"] or tok["is_punct"]:
                continue
            surface = tok["text"].lower().rstrip(".,;:!?\"'")
            surface_to_idxs.setdefault(surface, []).append(tok["idx"])

        yield _sse({"type": "start", "total": len(unknown_words)})

        async for result in fetch_definitions(unknown_words, entry.language):
            surface = result["text"].lower().rstrip(".,;:!?\"'")

            if not result["error"]:
                # Fill definition into all tokens with this surface form
                for i, tok in enumerate(tokens):
                    if tok["idx"] in surface_to_idxs.get(surface, []):
                        tokens[i]["lemma"] = result["lemma"]
                        tokens[i]["definition_lemma"] = result["definition"]

                yield _sse({
                    "type": "word",
                    "text": result["text"],
                    "lemma": result["lemma"],
                    "definition": result["definition"],
                    "error": False,
                })
            else:
                yield _sse({
                    "type": "word",
                    "text": result["text"],
                    "lemma": "",
                    "definition": "",
                    "error": True,
                })

        # Save updated tokens to DB
        entry.parsed_json = json.dumps(tokens, ensure_ascii=False)
        entry.updated_at = datetime.utcnow()
        db.commit()

        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# POST /admin/update-meta/{id} — update title/author without re-parsing
# ---------------------------------------------------------------------------

@router.post("/update-meta/{text_id}")
async def admin_update_meta(
    request: Request,
    text_id: int,
    title: str = Form(...),
    author: str = Form(""),
    source: str = Form(""),
    text_type: str = Form("story"),
    slug: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_local(request)
    entry = db.query(TextEntry).filter(TextEntry.id == text_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Text not found")

    entry.title = title.strip() or entry.title
    entry.author = author.strip() if author.strip() else None
    entry.source = source.strip() if source.strip() else None
    entry.text_type = text_type if text_type in TEXT_TYPES else entry.text_type
    # Use provided slug if valid, otherwise derive from title
    cleaned_slug = re.sub(r"[^a-z0-9-]", "", slug.strip().lower())
    base_slug = cleaned_slug if cleaned_slug else slugify(entry.title)
    entry.slug = _make_unique_slug(base_slug, db, exclude_id=entry.id)
    entry.updated_at = datetime.utcnow()
    db.commit()

    return RedirectResponse(url=f"/admin/confirm/{text_id}", status_code=303)


# ---------------------------------------------------------------------------
# POST /admin/publish/{id} — bake JSON + regenerate index
# ---------------------------------------------------------------------------

@router.post("/publish/{text_id}")
async def admin_publish(
    request: Request,
    text_id: int,
    db: Session = Depends(get_db),
):
    _require_local(request)
    entry = db.query(TextEntry).filter(TextEntry.id == text_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Text not found")

    entry.published_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)

    publish_text(entry)
    publish_index()

    return RedirectResponse(url="/admin", status_code=303)


# ---------------------------------------------------------------------------
# POST /admin/publish-all
# ---------------------------------------------------------------------------

@router.post("/publish-all")
async def admin_publish_all(
    request: Request,
    db: Session = Depends(get_db),
):
    _require_local(request)
    from app.services.publisher import publish_all
    publish_all(db)
    return RedirectResponse(url="/admin", status_code=303)


# ---------------------------------------------------------------------------
# POST /admin/delete/{id}
# ---------------------------------------------------------------------------

@router.post("/delete/{text_id}")
async def admin_delete(
    request: Request,
    text_id: int,
    db: Session = Depends(get_db),
):
    _require_local(request)
    entry = db.query(TextEntry).filter(TextEntry.id == text_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Text not found")

    if entry.slug:
        from pathlib import Path
        json_path = Path("dist") / "texts" / f"{entry.slug}.json"
        if json_path.exists():
            json_path.unlink()

    db.delete(entry)
    db.commit()
    publish_index()

    return RedirectResponse(url="/admin", status_code=303)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
