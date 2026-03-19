"""
NLP pipeline for DVNR.

Tokenises text, detects phrase chunks (fixed expressions, article+noun,
compound tenses, etc.) and fetches Wiktionary definitions.
"""

import json
import re
from pathlib import Path
from typing import Optional
import spacy
from spacy.tokens import Doc

from app.services.wiktionary import fetch_definitions

# ---------------------------------------------------------------------------
# Model loading (called once at startup from main.py)
# ---------------------------------------------------------------------------

_nlp_es: Optional[spacy.Language] = None
_nlp_it: Optional[spacy.Language] = None


def load_models() -> None:
    global _nlp_es, _nlp_it
    _nlp_es = spacy.load("es_core_news_sm")
    _nlp_it = spacy.load("it_core_news_sm")


def get_nlp(language: str) -> spacy.Language:
    if language == "it":
        if _nlp_it is None:
            raise RuntimeError("Italian spaCy model not loaded")
        return _nlp_it
    if _nlp_es is None:
        raise RuntimeError("Spanish spaCy model not loaded")
    return _nlp_es


# ---------------------------------------------------------------------------
# Fixed expressions config
# ---------------------------------------------------------------------------

_FIXED_ES: list[dict] = []
_FIXED_IT: list[dict] = []

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def _load_fixed_expressions() -> None:
    global _FIXED_ES, _FIXED_IT
    es_path = CONFIG_DIR / "fixed_expressions_es.json"
    it_path = CONFIG_DIR / "fixed_expressions_it.json"
    if es_path.exists():
        _FIXED_ES = json.loads(es_path.read_text(encoding="utf-8"))
    if it_path.exists():
        _FIXED_IT = json.loads(it_path.read_text(encoding="utf-8"))


_load_fixed_expressions()


def _get_fixed_expressions(language: str) -> list[dict]:
    return _FIXED_ES if language == "es" else _FIXED_IT


# ---------------------------------------------------------------------------
# Spanish / Italian contraction surface-form tokens
# Contracted forms must be matched on raw surface text (not lemma).
# ---------------------------------------------------------------------------

CONTRACTIONS_ES = {"del", "al"}
CONTRACTIONS_IT = {"del", "dello", "della", "dei", "degli", "delle",
                   "nel", "nello", "nella", "nei", "negli", "nelle",
                   "al", "allo", "alla", "ai", "agli", "alle",
                   "col", "coi", "sul", "sullo", "sulla", "sui", "sugli", "sulle"}


def _is_contraction(text: str, language: str) -> bool:
    t = text.lower()
    if language == "es":
        return t in CONTRACTIONS_ES
    return t in CONTRACTIONS_IT


# ---------------------------------------------------------------------------
# Curated verb + preposition pairs (for the "verb + helper prep" chunk rule)
# ---------------------------------------------------------------------------

VERB_PREP_PAIRS_ES: dict[str, set[str]] = {
    "pensar": {"en"},
    "creer": {"en"},
    "soñar": {"con"},
    "insistir": {"en"},
    "consistir": {"en"},
    "depender": {"de"},
    "acordarse": {"de"},
    "olvidarse": {"de"},
    "encargarse": {"de"},
    "preocuparse": {"por", "de"},
    "quedar": {"en", "con"},
    "quedar": {"en", "con"},
    "contar": {"con"},
    "confiar": {"en"},
    "fijarse": {"en"},
    "interesarse": {"por", "en"},
    "dedicarse": {"a"},
    "atreverse": {"a"},
    "negarse": {"a"},
    "acostumbrarse": {"a"},
    "comprometerse": {"a", "con"},
    "disponer": {"de"},
    "tratar": {"de"},
    "acabar": {"de", "con"},
    "empezar": {"a"},
    "comenzar": {"a"},
    "volver": {"a"},
    "dejar": {"de"},
    "cesar": {"de"},
    "hablar": {"de", "con"},
    "referirse": {"a"},
    "centrarse": {"en"},
    "basarse": {"en"},
}

VERB_PREP_PAIRS_IT: dict[str, set[str]] = {
    "pensare": {"a", "di"},
    "credere": {"in", "a"},
    "sognare": {"di"},
    "insistere": {"su"},
    "consistere": {"in"},
    "dipendere": {"da"},
    "ricordarsi": {"di"},
    "dimenticarsi": {"di"},
    "occuparsi": {"di"},
    "preoccuparsi": {"di", "per"},
    "fidarsi": {"di"},
    "interessarsi": {"di", "a"},
    "dedicarsi": {"a"},
    "osare": {"di"},
    "rifiutarsi": {"di"},
    "abituarsi": {"a"},
    "impegnarsi": {"a", "con"},
    "contare": {"su"},
    "parlare": {"di", "con"},
    "riferirsi": {"a"},
    "concentrarsi": {"su"},
    "basarsi": {"su"},
    "smettere": {"di"},
    "cominciare": {"a", "con"},
    "iniziare": {"a"},
    "continuare": {"a"},
    "cercare": {"di"},
    "provare": {"a"},
    "riuscire": {"a"},
    "finire": {"di", "con"},
    "andare": {"a", "da", "in"},
    "venire": {"da", "a"},
}


def _get_verb_prep_pairs(language: str) -> dict[str, set[str]]:
    return VERB_PREP_PAIRS_ES if language == "es" else VERB_PREP_PAIRS_IT


# ---------------------------------------------------------------------------
# Spanish / Italian auxiliaries and modals
# ---------------------------------------------------------------------------

ES_AUXILIARIES = {"haber"}
ES_MODALS = {"poder", "querer", "deber", "soler", "saber", "tener"}

IT_AUXILIARIES = {"avere", "essere"}
IT_MODALS = {"potere", "volere", "dovere", "sapere", "solere"}


def _is_auxiliary(lemma: str, language: str) -> bool:
    if language == "es":
        return lemma in ES_AUXILIARIES
    return lemma in IT_AUXILIARIES


def _is_modal(lemma: str, language: str) -> bool:
    if language == "es":
        return lemma in ES_MODALS
    return lemma in IT_MODALS


# ---------------------------------------------------------------------------
# Article detection helpers
# ---------------------------------------------------------------------------

ES_DEF_ARTICLES = {"el", "la", "los", "las", "lo"}
ES_INDEF_ARTICLES = {"un", "una", "unos", "unas"}
ES_ARTICLES = ES_DEF_ARTICLES | ES_INDEF_ARTICLES

IT_DEF_ARTICLES = {"il", "lo", "la", "i", "gli", "le", "l'", "l"}
IT_INDEF_ARTICLES = {"un", "uno", "una", "un'"}
IT_ARTICLES = IT_DEF_ARTICLES | IT_INDEF_ARTICLES

IT_POSSESSIVES = {"mio", "mia", "miei", "mie",
                  "tuo", "tua", "tuoi", "tue",
                  "suo", "sua", "suoi", "sue",
                  "nostro", "nostra", "nostri", "nostre",
                  "vostro", "vostra", "vostri", "vostre",
                  "loro"}


def _is_article(text: str, language: str) -> bool:
    t = text.lower()
    # Also accept contraction tokens as article-like for article+noun rule
    if language == "es":
        return t in ES_ARTICLES or t in CONTRACTIONS_ES
    return t in IT_ARTICLES or t in CONTRACTIONS_IT


def _is_it_possessive(text: str) -> bool:
    return text.lower() in IT_POSSESSIVES


# ---------------------------------------------------------------------------
# Reflexive clitics
# ---------------------------------------------------------------------------

ES_CLITICS = {"se", "me", "te", "nos", "os"}
IT_CLITICS = {"si", "mi", "ti", "ci", "vi"}

ES_REFLEXIVE_VERBS = set()  # se matches by position
IT_REFLEXIVE_VERBS = set()


def _is_clitic(text: str, language: str) -> bool:
    t = text.lower()
    return t in (ES_CLITICS if language == "es" else IT_CLITICS)


# ---------------------------------------------------------------------------
# Sentence boundary detection: tokens that break chunk spans
# ---------------------------------------------------------------------------

SENTENCE_BREAK_PUNCTS = {".", "!", "?", ";", ":", "…", "...", "—", "–"}


def _is_sentence_break(text: str) -> bool:
    return text in SENTENCE_BREAK_PUNCTS or text == "\n"


# ---------------------------------------------------------------------------
# Main chunker
# ---------------------------------------------------------------------------

def _build_token_list(doc: Doc) -> list[dict]:
    """Convert a spaCy Doc to a list of plain dicts."""
    tokens = []
    for i, tok in enumerate(doc):
        tokens.append({
            "idx": i,
            "text": tok.text,
            "lemma": tok.lemma_.lower(),
            "pos": tok.pos_,
            "is_punct": tok.is_punct,
            "is_space": tok.is_space,
            "is_newline": False,
            "chunk_id": None,
            "chunk_role": "solo",
        })
    return tokens


def _assign_chunk(tokens: list[dict], indices: list[int], chunk_id: int) -> None:
    """Mark a list of token indices as belonging to a chunk."""
    if len(indices) < 2:
        return
    for pos_in_chunk, i in enumerate(indices):
        tokens[i]["chunk_id"] = chunk_id
        if len(indices) == 1:
            tokens[i]["chunk_role"] = "solo"
        elif pos_in_chunk == 0:
            tokens[i]["chunk_role"] = "start"
        elif pos_in_chunk == len(indices) - 1:
            tokens[i]["chunk_role"] = "end"
        else:
            tokens[i]["chunk_role"] = "middle"


def _already_chunked(tokens: list[dict], indices: list[int]) -> bool:
    return any(tokens[i]["chunk_id"] is not None for i in indices)


def detect_chunks(tokens: list[dict], language: str) -> list[dict]:
    """
    Apply all chunking rules in priority order to the token list.
    Modifies tokens in place and returns them.
    """
    n = len(tokens)
    next_chunk_id = 1

    # Helper: skip whitespace tokens when looking for the next real token
    def next_non_space(start: int, limit: int = 3) -> Optional[int]:
        for k in range(start, min(start + limit, n)):
            if not tokens[k]["is_space"]:
                return k
        return None

    # -----------------------------------------------------------------------
    # Rule 1: Fixed expressions (HIGHEST PRIORITY, lemma-based matching,
    #         but contraction tokens matched on surface text)
    # -----------------------------------------------------------------------
    fixed_exprs = _get_fixed_expressions(language)

    for expr in fixed_exprs:
        lemma_seq = expr["lemmas"]  # list of lemma strings
        expr_len = len(lemma_seq)

        i = 0
        while i < n:
            # Try to match starting at token i
            match_indices = []
            j = i
            seq_pos = 0

            while seq_pos < expr_len and j < n:
                tok = tokens[j]
                if tok["is_space"]:
                    j += 1
                    continue
                if tok["is_punct"] and not _is_sentence_break(tok["text"]):
                    j += 1
                    continue
                if tok["is_punct"] and _is_sentence_break(tok["text"]):
                    break  # sentence boundary breaks match

                expected_lemma = lemma_seq[seq_pos]
                # For contractions, match on surface text
                if _is_contraction(tok["text"], language):
                    actual = tok["text"].lower()
                else:
                    actual = tok["lemma"]

                if actual == expected_lemma:
                    match_indices.append(j)
                    seq_pos += 1
                    j += 1
                else:
                    break

            if seq_pos == expr_len and len(match_indices) >= 2:
                if not _already_chunked(tokens, match_indices):
                    # Store definition on the first token of the chunk
                    tokens[match_indices[0]]["fixed_expr_def"] = expr.get("definition", "")
                    tokens[match_indices[0]]["fixed_expr_canonical"] = expr.get("canonical", "")
                    _assign_chunk(tokens, match_indices, next_chunk_id)
                    next_chunk_id += 1
                    i = match_indices[-1] + 1
                    continue
            i += 1

    # -----------------------------------------------------------------------
    # Rules 2–6 applied token by token
    # -----------------------------------------------------------------------
    i = 0
    while i < n:
        tok = tokens[i]

        if tok["is_space"] or tok["is_punct"]:
            i += 1
            continue

        # -------------------------------------------------------------------
        # Rule 4: Verb + reflexive clitic (clitic PRECEDES verb)
        # Pattern: clitic → verb (ignoring spaces)
        # -------------------------------------------------------------------
        if _is_clitic(tok["text"], language):
            j = next_non_space(i + 1)
            if j is not None and tokens[j]["pos"] in ("VERB", "AUX"):
                indices = [i, j]
                if not _already_chunked(tokens, indices):
                    _assign_chunk(tokens, indices, next_chunk_id)
                    next_chunk_id += 1
                    i = j + 1
                    continue

        # -------------------------------------------------------------------
        # Rule 2: Article + noun  (both languages)
        # Rule 3: Italian: article + possessive + noun
        # -------------------------------------------------------------------
        if _is_article(tok["text"], language) and tok["chunk_id"] is None:
            j = next_non_space(i + 1)
            if j is not None and not _is_sentence_break(tokens[j]["text"]):
                next_tok = tokens[j]
                # Italian: article + possessive + noun
                if language == "it" and _is_it_possessive(next_tok["text"]):
                    k = next_non_space(j + 1)
                    if k is not None and tokens[k]["pos"] in ("NOUN", "PROPN") \
                            and not _is_sentence_break(tokens[k]["text"]):
                        indices = [i, j, k]
                        if not _already_chunked(tokens, indices):
                            _assign_chunk(tokens, indices, next_chunk_id)
                            next_chunk_id += 1
                            i = k + 1
                            continue
                # Article + noun
                if next_tok["pos"] in ("NOUN", "PROPN") and next_tok["chunk_id"] is None:
                    indices = [i, j]
                    if not _already_chunked(tokens, indices):
                        _assign_chunk(tokens, indices, next_chunk_id)
                        next_chunk_id += 1
                        i = j + 1
                        continue

        # -------------------------------------------------------------------
        # Rule 5: Compound tense — auxiliary + past participle
        # NOT modals
        # -------------------------------------------------------------------
        if tok["pos"] in ("VERB", "AUX") and _is_auxiliary(tok["lemma"], language) \
                and not _is_modal(tok["lemma"], language) and tok["chunk_id"] is None:
            j = next_non_space(i + 1)
            if j is not None:
                next_tok = tokens[j]
                # Past participle: spaCy tags these as VERB with morph Aspect=Perf
                # or we can check pos == VERB and tense indicator; spaCy small models
                # may tag pp as ADJ too, so accept both
                if next_tok["pos"] in ("VERB", "ADJ") and not _is_sentence_break(tokens[j]["text"]) \
                        and next_tok["chunk_id"] is None:
                    indices = [i, j]
                    if not _already_chunked(tokens, indices):
                        _assign_chunk(tokens, indices, next_chunk_id)
                        next_chunk_id += 1
                        i = j + 1
                        continue

        # -------------------------------------------------------------------
        # Rule 4 (continued): Verb + reflexive clitic (clitic FOLLOWS verb)
        # -------------------------------------------------------------------
        if tok["pos"] in ("VERB", "AUX") and tok["chunk_id"] is None:
            j = next_non_space(i + 1)
            if j is not None and _is_clitic(tokens[j]["text"], language) \
                    and not _is_sentence_break(tokens[j]["text"]) \
                    and tokens[j]["chunk_id"] is None:
                indices = [i, j]
                if not _already_chunked(tokens, indices):
                    _assign_chunk(tokens, indices, next_chunk_id)
                    next_chunk_id += 1
                    i = j + 1
                    continue

            # ---------------------------------------------------------------
            # Rule 6: Verb + helper preposition
            # ---------------------------------------------------------------
            verb_prep_pairs = _get_verb_prep_pairs(language)
            verb_lemma = tok["lemma"]
            if verb_lemma in verb_prep_pairs and tok["chunk_id"] is None:
                j = next_non_space(i + 1)
                if j is not None and tokens[j]["pos"] == "ADP" \
                        and tokens[j]["text"].lower() in verb_prep_pairs[verb_lemma] \
                        and not _is_sentence_break(tokens[j]["text"]) \
                        and tokens[j]["chunk_id"] is None:
                    indices = [i, j]
                    if not _already_chunked(tokens, indices):
                        _assign_chunk(tokens, indices, next_chunk_id)
                        next_chunk_id += 1
                        i = j + 1
                        continue

        i += 1

    return tokens


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------

async def parse_text(text: str, language: str, title_line: Optional[str] = None) -> list[dict]:
    import logging, time as _time
    log = logging.getLogger("dvnr.parse")
    logging.basicConfig(level=logging.INFO)
    _t0 = _time.monotonic()
    """
    Full NLP pipeline:
    1. Split text on line breaks, preserving paragraph/line boundaries
    2. spaCy tokenise + POS + lemma each line
    3. Chunk detection
    4. Wiktionary definitions
    5. Return token list as JSON-serialisable dicts
    """
    nlp = get_nlp(language)

    # Split on newlines, preserving blank lines as paragraph breaks
    lines = text.splitlines()

    all_tokens: list[dict] = []
    global_idx = 0

    # Determine which line index is the title (if any)
    title_line_index: Optional[int] = None
    if title_line:
        for li, line in enumerate(lines):
            if line.strip() == title_line.strip():
                title_line_index = li
                break

    for line_num, line in enumerate(lines):
        stripped = line.strip()
        is_title_line = (title_line_index is not None and line_num == title_line_index)

        # Blank line → paragraph break token
        if not stripped:
            all_tokens.append({
                "idx": global_idx,
                "text": "\n",
                "lemma": "\n",
                "pos": "",
                "is_punct": False,
                "is_space": True,
                "is_newline": True,
                "is_title": False,
                "chunk_id": None,
                "chunk_role": "solo",
            })
            global_idx += 1
            continue

        # Non-empty line → parse with spaCy
        doc = nlp(stripped)
        line_tokens = _build_token_list(doc)

        # Renumber idx to be globally unique; mark title tokens
        for tok in line_tokens:
            tok["idx"] = global_idx
            tok["is_newline"] = False
            tok["is_title"] = is_title_line
            global_idx += 1

        all_tokens.extend(line_tokens)

        # After every line (except the last), insert a line-break token
        if line_num < len(lines) - 1:
            all_tokens.append({
                "idx": global_idx,
                "text": "\n",
                "lemma": "\n",
                "pos": "",
                "is_punct": False,
                "is_space": True,
                "is_newline": True,
                "is_title": False,
                "chunk_id": None,
                "chunk_role": "solo",
            })
            global_idx += 1

    _t_spacy = _time.monotonic()
    log.info(f"spaCy tokenisation: {_t_spacy - _t0:.2f}s  ({len(all_tokens)} tokens)")

    all_tokens = detect_chunks(all_tokens, language)
    _t_chunks = _time.monotonic()
    log.info(f"Chunk detection:    {_t_chunks - _t_spacy:.2f}s")

    # Count unique words before Wiktionary fetch
    unique_words = len({
        w for tok in all_tokens
        if not tok.get("is_punct") and not tok.get("is_space")
        for w in [tok["text"].lower(), tok["lemma"].lower()]
    })
    log.info(f"Unique words to look up: {unique_words} (est. {unique_words * 0.1:.0f}s at 0.1s/word)")

    all_tokens = await fetch_definitions(all_tokens, language)
    _t_wiki = _time.monotonic()
    log.info(f"Wiktionary fetch:   {_t_wiki - _t_chunks:.2f}s")
    log.info(f"TOTAL:              {_t_wiki - _t0:.2f}s")

    return all_tokens


def count_words(text: str) -> int:
    """Count whitespace-delimited words in text."""
    return len(text.split())
