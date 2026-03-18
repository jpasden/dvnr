"""
Wiktionary definition fetcher.

Fetches definitions from the English Wiktionary REST API, filtering by
language and POS. Results are cached in-memory per parse run.

Key behaviours:
- Trusts Wiktionary's POS over spaCy's when they differ (e.g. spaCy calls
  a participle a VERB; Wiktionary may list it as Participle or Adjective).
- When a definition is a "form-of" reference ("feminine singular of X",
  "plural of X", "past participle of X"), follows it to the target lemma
  to retrieve the real definition.
- Suppresses definition_surface when it just says "X of [lemma]" with no
  additional gloss.
- Prefers Participle entries over Adjective entries when spaCy tags a token
  as VERB or ADJ.
"""

import asyncio
import re
from typing import Optional
import httpx

WIKTIONARY_URL = "https://en.wiktionary.org/api/rest_v1/page/definition/{word}"

WIKTIONARY_HEADERS = {
    "User-Agent": "DVNR/1.0 (vocabulary reader app) httpx",
}

# Wiktionary REST API returns ISO language codes as top-level keys
LANGUAGE_KEY = {
    "es": "es",
    "it": "it",
}

# Priority-ordered list of Wiktionary partOfSpeech values for each spaCy POS.
# Earlier entries are preferred. "participle" is listed before "adjective" so
# that past-participle forms aren't mistakenly read as plain adjectives.
POS_PRIORITY: dict[str, list[str]] = {
    "VERB": ["verb", "participle"],
    "AUX":  ["verb", "participle"],
    "ADJ":  ["participle", "adjective"],
    "NOUN": ["noun"],
    "ADV":  ["adverb"],
    "ADP":  ["preposition"],
    "CCONJ": ["conjunction"],
    "SCONJ": ["conjunction"],
    "DET":  ["article", "determiner"],
    "PRON": ["pronoun"],
    "PROPN": ["noun", "proper noun"],
    "NUM":  ["numeral", "number"],
    "INTJ": ["interjection"],
}

# Regex to detect "form-of" definitions and extract the target lemma.
# Only matches genuine grammatical form-of patterns (not arbitrary "X of Y" phrases).
# The grammatical prefix must be one of the known patterns Wiktionary uses.
_FORM_OF_RE = re.compile(
    r"^(?:plural|singular|feminine|masculine|neuter|"
    r"(?:first|second|third)(?:[/\-](?:first|second|third))*[\-\s]person|"
    r"past participle|present participle|gerund|"
    r"infinitive|imperative|conditional|subjunctive|"
    r"[\w/\-]+ (?:singular|plural)|"
    r"[\w/\-]+ person)"
    r"[\w\s/\-,]* of ([a-záéíóúüñàèìòùâêîôûäëïöüç]{3,})\s*$",
    re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _is_form_of(definition: str) -> Optional[str]:
    """
    If definition is a form-of reference, return the target lemma.
    Otherwise return None.
    """
    m = _FORM_OF_RE.match(definition.strip())
    return m.group(1).lower() if m else None


def _best_entry(lang_section: list[dict], pos: str) -> Optional[dict]:
    """
    Return the best Wiktionary entry for the given spaCy POS tag,
    respecting priority order. Falls back to any entry if nothing matches.
    """
    priorities = POS_PRIORITY.get(pos, [])

    # First pass: match in priority order
    for fragment in priorities:
        for entry in lang_section:
            if fragment in entry.get("partOfSpeech", "").lower():
                if entry.get("definitions"):
                    return entry

    # Second pass: accept any entry with definitions
    for entry in lang_section:
        if entry.get("definitions"):
            return entry

    return None


def _extract_definition(entry: dict) -> Optional[str]:
    """Return the first non-empty definition from an entry."""
    for d in entry.get("definitions", []):
        raw = d.get("definition", "")
        cleaned = _strip_html(raw)
        if cleaned:
            return cleaned
    return None


async def _fetch_word(client: httpx.AsyncClient, word: str) -> Optional[dict]:
    url = WIKTIONARY_URL.format(word=word)
    try:
        response = await client.get(url, timeout=10.0)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None


def _resolve_definition(
    data: Optional[dict],
    lang_key: str,
    pos: str,
    cache: dict,
    already_followed: bool = False,
) -> Optional[str]:
    """
    Given a Wiktionary response dict, extract the best definition for pos.
    If the definition is a form-of reference, look up the target in cache
    and return its definition instead (one level of follow-through only).
    """
    if not data:
        return None
    lang_section = data.get(lang_key)
    if not lang_section:
        return None

    entry = _best_entry(lang_section, pos)
    if not entry:
        return None

    definition = _extract_definition(entry)
    if not definition:
        return None

    # Follow form-of references one level deep
    if not already_followed:
        target = _is_form_of(definition)
        if target:
            target_data = cache.get(target)
            if target_data:
                followed = _resolve_definition(target_data, lang_key, pos, cache, already_followed=True)
                if followed:
                    return followed
            # If we couldn't follow, return None rather than the unhelpful form-of string
            return None

    return definition


async def fetch_definitions(tokens: list[dict], language: str) -> list[dict]:
    """
    Fetch Wiktionary definitions for all tokens and populate definition_lemma
    and (for verbs) definition_surface fields in place.
    """
    lang_key = LANGUAGE_KEY.get(language, "es")

    # Collect all unique words to fetch (surfaces + lemmas)
    to_fetch: set[str] = set()
    for tok in tokens:
        if tok.get("is_punct") or tok.get("is_space"):
            continue
        surface = tok["text"].lower()
        lemma = tok["lemma"].lower()
        to_fetch.add(surface)
        if lemma != surface:
            to_fetch.add(lemma)

    import logging, time as _time
    log = logging.getLogger("dvnr.wiktionary")
    _tw0 = _time.monotonic()

    # Fetch all words concurrently, with a semaphore to cap parallel requests
    cache: dict[str, Optional[dict]] = {}
    CONCURRENCY = 10

    async def fetch_all(words: set[str], client: httpx.AsyncClient) -> None:
        sem = asyncio.Semaphore(CONCURRENCY)
        async def fetch_one(word: str) -> None:
            async with sem:
                if word not in cache:
                    cache[word] = await _fetch_word(client, word)
        await asyncio.gather(*[fetch_one(w) for w in words])

    async with httpx.AsyncClient(headers=WIKTIONARY_HEADERS) as client:
        await fetch_all(to_fetch, client)
        log.info(f"  First-pass fetches:  {len(to_fetch)} words in {_time.monotonic()-_tw0:.2f}s")

        # Second pass: collect any form-of targets not yet in cache
        form_of_targets: set[str] = set()
        for data in cache.values():
            if not data:
                continue
            for lang_entries in data.values():
                if not isinstance(lang_entries, list):
                    continue
                for entry in lang_entries:
                    for d in entry.get("definitions", []):
                        raw = _strip_html(d.get("definition", ""))
                        target = _is_form_of(raw)
                        if target and target not in cache and len(target) >= 3 and ' ' not in target:
                            form_of_targets.add(target)

        _tw1 = _time.monotonic()
        await fetch_all(form_of_targets, client)
        log.info(f"  Form-of follow-ups:  {len(form_of_targets)} words in {_time.monotonic()-_tw1:.2f}s")

    # Populate token definitions
    for tok in tokens:
        if tok.get("is_punct") or tok.get("is_space"):
            continue

        pos = tok.get("pos", "")
        surface = tok["text"].lower()
        lemma = tok["lemma"].lower()
        is_verb = pos in ("VERB", "AUX")

        # spaCy sometimes produces bad lemmas for common words; use surface as lemma
        # e.g. "un" → lemma "uno", "del" → lemma "de el", articles mangled, etc.
        if surface in cache and lemma not in cache:
            lemma = surface

        # Lemma definition (always attempted)
        lemma_def = _resolve_definition(cache.get(lemma), lang_key, pos, cache)
        if not lemma_def and surface != lemma:
            # Fall back to surface form for lemma def
            lemma_def = _resolve_definition(cache.get(surface), lang_key, pos, cache)

        # Surface definition (verbs only: conjugation note)
        surface_def: Optional[str] = None
        if is_verb and surface != lemma:
            raw_surface_data = cache.get(surface)
            if raw_surface_data:
                lang_section = raw_surface_data.get(lang_key, [])
                entry = _best_entry(lang_section, pos)
                if entry:
                    raw_def = _extract_definition(entry)
                    if raw_def:
                        target = _is_form_of(raw_def)
                        if target:
                            # It's purely a form-of note — suppress it
                            surface_def = None
                        else:
                            # It has its own gloss — keep it
                            surface_def = raw_def

        # Assign to token
        if is_verb:
            if surface_def:
                tok["definition_surface"] = surface_def
            if lemma_def:
                tok["definition_lemma"] = lemma_def
            else:
                tok["definition_lemma"] = "(definition not found)"
        else:
            tok["definition_lemma"] = lemma_def if lemma_def else "(definition not found)"

    return tokens
