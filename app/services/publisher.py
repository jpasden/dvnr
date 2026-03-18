"""
Static site generator for DVNR.

Generates:
  dist/texts/{slug}.json  — token data for a single published text
  dist/es/index.html      — Spanish text listing
  dist/it/index.html      — Italian text listing
"""

import json
from datetime import datetime
from pathlib import Path

DIST_DIR = Path("dist")
TEXTS_DIR = DIST_DIR / "texts"


def _ensure_dirs() -> None:
    TEXTS_DIR.mkdir(parents=True, exist_ok=True)
    (DIST_DIR / "es").mkdir(parents=True, exist_ok=True)
    (DIST_DIR / "it").mkdir(parents=True, exist_ok=True)


def publish_text(entry) -> None:
    """Generate dist/texts/{slug}.json for one text entry."""
    _ensure_dirs()

    slug = entry.slug or str(entry.id)
    tokens = json.loads(entry.parsed_json) if entry.parsed_json else []

    # Apply edited_tokens overrides
    if entry.edited_tokens:
        try:
            overrides: dict = json.loads(entry.edited_tokens)
            for tok in tokens:
                idx_str = str(tok.get("idx", ""))
                if idx_str in overrides:
                    tok.update(overrides[idx_str])
        except (json.JSONDecodeError, TypeError):
            pass

    published_at = entry.published_at.isoformat() if entry.published_at else datetime.utcnow().isoformat()

    payload = {
        "slug": slug,
        "title": entry.title,
        "author": entry.author,
        "language": entry.language,
        "word_count": entry.word_count or 0,
        "published_at": published_at,
        "tokens": tokens,
    }

    out_path = TEXTS_DIR / f"{slug}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def publish_index() -> None:
    """Regenerate dist/es/index.html and dist/it/index.html from available JSON files."""
    _ensure_dirs()

    # Collect all published text metadata from JSON files
    texts_by_lang: dict[str, list[dict]] = {"es": [], "it": []}

    for json_path in sorted(TEXTS_DIR.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            lang = data.get("language", "es")
            if lang in texts_by_lang:
                texts_by_lang[lang].append({
                    "slug": data.get("slug", ""),
                    "title": data.get("title", ""),
                    "author": data.get("author"),
                    "word_count": data.get("word_count", 0),
                    "published_at": data.get("published_at", ""),
                })
        except (json.JSONDecodeError, OSError):
            continue

    for lang, texts in texts_by_lang.items():
        # Sort by published_at descending
        texts.sort(key=lambda t: t.get("published_at", ""), reverse=True)
        html = _build_index_html(lang, texts)
        index_path = DIST_DIR / lang / "index.html"
        index_path.write_text(html, encoding="utf-8")


def publish_all(db_session) -> None:
    """Publish all texts that have a slug set, then regenerate index pages."""
    from app.models import TextEntry
    entries = db_session.query(TextEntry).filter(TextEntry.slug.isnot(None)).all()
    for entry in entries:
        publish_text(entry)
    publish_index()


def _build_index_html(lang: str, texts: list[dict]) -> str:
    lang_label = "Spanish" if lang == "es" else "Italian"
    lang_flag = "ES" if lang == "es" else "IT"
    other_lang = "it" if lang == "es" else "es"
    other_label = "Italian" if lang == "es" else "Spanish"

    badge_color = "#856404" if lang == "es" else "#155724"
    badge_bg = "#ffeeba" if lang == "es" else "#d4edda"

    cards_html = ""
    if texts:
        for t in texts:
            slug = t["slug"]
            title = _h(t["title"])
            author_html = f'<p class="card-author">{_h(t["author"])}</p>' if t.get("author") else ""
            word_count = t.get("word_count") or 0
            wc_html = f'<span class="card-meta">{word_count:,} words</span>' if word_count else ""
            pub_date = ""
            if t.get("published_at"):
                try:
                    dt = datetime.fromisoformat(t["published_at"])
                    pub_date = f'<span class="card-meta">{dt.strftime("%b %d, %Y")}</span>'
                except ValueError:
                    pass
            cards_html += f"""
    <a class="card" href="../reader.html#{slug}">
      <div class="card-header">
        <span class="lang-badge" style="background:{badge_bg};color:{badge_color}">{lang_flag}</span>
      </div>
      <div class="card-body">
        <h2 class="card-title">{title}</h2>
        {author_html}
        <div class="card-footer">
          {wc_html}
          {pub_date}
        </div>
      </div>
    </a>"""
    else:
        cards_html = '<p class="empty">No texts published yet.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{lang_label} Texts — DVNR</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ font-size: 16px; }}
    body {{
      font-family: Arial, sans-serif;
      background: #fafaf8;
      color: #222;
      line-height: 1.6;
    }}
    header {{
      background: #1a1a2e;
      color: #e8e8f0;
      padding: 0.75rem 2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 2px solid #2a2a4e;
    }}
    header h1 {{
      font-size: 1.25rem;
      font-family: 'Courier New', monospace;
      font-weight: bold;
      letter-spacing: 0.05em;
      color: #e8e8f0;
    }}
    header nav a {{
      color: #c8c8e0;
      text-decoration: none;
      font-size: 0.9rem;
      margin-left: 1.5rem;
    }}
    header nav a:hover {{ color: #fff; }}
    main {{
      max-width: 960px;
      margin: 0 auto;
      padding: 2rem 1.5rem;
    }}
    .page-title {{
      font-size: 1.5rem;
      font-family: Arial, sans-serif;
      margin-bottom: 0.25rem;
    }}
    .page-subtitle {{
      color: #666;
      font-size: 0.9rem;
      margin-bottom: 2rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 1rem;
    }}
    .card {{
      display: flex;
      flex-direction: column;
      background: #fff;
      border: 1px solid #ddd;
      border-radius: 8px;
      text-decoration: none;
      color: #222;
      transition: border-color 0.15s, box-shadow 0.15s;
      overflow: hidden;
    }}
    .card:hover {{
      border-color: #2a6496;
      box-shadow: 0 2px 10px rgba(42,100,150,0.12);
      text-decoration: none;
    }}
    .card-header {{
      padding: 0.6rem 0.9rem 0;
    }}
    .lang-badge {{
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 3px;
      font-size: 0.75rem;
      font-weight: 700;
      letter-spacing: 0.05em;
    }}
    .card-body {{
      padding: 0.5rem 0.9rem 0.8rem;
      flex: 1;
      display: flex;
      flex-direction: column;
    }}
    .card-title {{
      font-size: 0.95rem;
      font-weight: 600;
      margin-bottom: 0.2rem;
      line-height: 1.35;
    }}
    .card-author {{
      font-size: 0.82rem;
      color: #666;
      font-style: italic;
      margin-bottom: 0.4rem;
    }}
    .card-footer {{
      margin-top: auto;
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
    }}
    .card-meta {{
      font-size: 0.78rem;
      color: #999;
    }}
    .empty {{
      color: #777;
      font-style: italic;
    }}
    footer {{
      margin-top: 3rem;
      padding: 1rem 2rem;
      text-align: center;
      font-size: 0.8rem;
      color: #aaa;
      border-top: 1px solid #eee;
    }}
  </style>
</head>
<body>
  <header>
    <h1>DVNR</h1>
    <nav>
      <a href="../{other_lang}/index.html">{other_label}</a>
    </nav>
  </header>
  <main>
    <h1 class="page-title">{lang_label} Texts</h1>
    <p class="page-subtitle">Click a text to open it in the reader.</p>
    <div class="grid">
      {cards_html}
    </div>
  </main>
  <footer>DVNR — Dynamic Vocabulary Notes Reader</footer>
</body>
</html>"""


def _h(text: str) -> str:
    """HTML-escape a string."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
