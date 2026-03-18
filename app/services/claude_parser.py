"""
Claude API text parser for DVNR.

Replaces the spaCy + Wiktionary pipeline entirely. A single Claude API call
tokenises the text, assigns POS tags and lemmas, identifies chunks, and
provides English definitions for all content words.
"""

import json
import logging
import time
from typing import Optional
import os
import anthropic

from .known_lemmas_es import KNOWN_LEMMAS_ES

log = logging.getLogger("dvnr.claude_parser")

_client: Optional[anthropic.AsyncAnthropic] = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Add it to your .env file."
            )
        _client = anthropic.AsyncAnthropic(api_key=api_key)
    return _client


_PROMPT_TEMPLATE = """\
You are a linguistic annotation tool. Tokenise the following {language_name} text and return a JSON array of token objects. Follow ALL rules exactly.

## Output format

Return ONLY a JSON array. No markdown fences, no explanation, no preamble. The array must be valid JSON.

Use a COMPACT format: omit any field whose value is the default. Defaults are:
- lemma: "" (empty string)
- pos: "" (empty string)
- is_punct: false
- is_space: false
- is_newline: false
- is_title: false
- chunk_id: null
- chunk_role: "solo"
- definition_lemma: null
- chunk_definition: null
- fixed_expr_canonical: null

Always include: idx, text. Include all other fields only when they differ from the default.

Example compact token for a plain word: {{"idx":5,"text":"casa","pos":"NOUN","lemma":"casa","definition_lemma":"house, home"}}
Example compact token for a space: {{"idx":6,"text":" ","is_space":true}}
Example compact token for punctuation: {{"idx":7,"text":".","is_punct":true}}
Example compact chunk start: {{"idx":8,"text":"se","pos":"PRON","chunk_id":3,"chunk_role":"start","chunk_definition":"she left (perfect)"}}

## Fields reference

- idx: integer, 0-based sequential index
- text: string, exact characters of this token
- lemma: dictionary/infinitive form; lowercase
- pos: universal POS tag (see list below)
- is_punct: true for punctuation marks
- is_space: true for a single space character between words (text must be " ")
- is_newline: true for a line break (text must be "\\n")
- is_title: true if this token is part of the title line
- chunk_id: integer starting at 1 for chunked tokens
- chunk_role: "start", "middle", or "end" for chunked tokens
- definition_lemma: concise English gloss of the lemma
- chunk_definition: English meaning of the whole chunk, on the FIRST token of the chunk only
- fixed_expr_canonical: canonical form of a fixed expression, on the FIRST token only

## POS tags (use exactly these strings)

VERB, NOUN, ADJ, ADV, ADP, CONJ, DET, PRON, PROPN, NUM, INTJ, AUX, PUNCT, SPACE

## Tokenisation rules

1. Preserve every character in the original text exactly. The concatenation of all token text fields must equal the original input string.
2. Each word is one token. Punctuation marks are separate tokens (is_punct: true). A single space between words is a token (is_space: true, text: " "). A line break is a token (is_newline: true, text: "\\n").
3. Contractions that are written as single words (e.g. Spanish "del", "al"; Italian "del", "nella", "agli") are single tokens — do NOT split them. Set their lemma to the contraction itself (e.g. lemma "del") and POS to ADP or DET as appropriate.
4. Apostrophe-elided forms (Italian "l'amico", "dell'uomo") keep the apostrophe attached to whichever part it belongs to and are treated as a single token where the word boundary is clear from the apostrophe. Use your judgement for ambiguous cases.

## Title rule

If the first non-empty line of the text has 15 words or fewer and does not end with a period, treat it as the title. Set is_title: true on every token that belongs to that line (including punctuation on that line, but NOT the newline token at the end of it).

## Chunk rules

A chunk is a multi-token linguistic unit that should be treated as a vocabulary item together. Assign matching chunk_id integers to all tokens in the same chunk, using "start"/"middle"/"end" for their roles. Minimum 2 tokens per chunk.

Identify these chunk types:
- Italian article + possessive + noun: "il mio libro"
- Compound tense (auxiliary + past participle): "ha comido", "è partita", "aveva visto"
- Reflexive clitic + verb (clitic precedes): "se levantó", "si alzò"
- Verb + reflexive clitic (enclitic, clitic follows): "levantarse", "alzarsi" (if appearing as infinitive+clitic)
- Verb + helper preposition (curated pairs): "pensar en", "depender de", "pensare a", "smettere di"
- Fixed expressions and idioms: multi-word units with a unified meaning different from their parts, e.g. "sin embargo", "a pesar de", "darse cuenta de", "tener en cuenta", "por supuesto", "de repente", "hacer falta", "echar de menos", "per esempio", "d'altronde", "a causa di", "tuttavia" (if multi-word)

Do NOT chunk across sentence boundaries (., !, ?, ;) or line breaks.
Do NOT chunk article + noun pairs (e.g. "el libro", "una casa") — articles and nouns are annotated individually.

## Definition rules

- definition_lemma: provide a concise English gloss (up to ~10 words) for the lemma. Focus on the most common meaning(s). Use commas to separate multiple senses. For verbs, give the infinitive meaning: "to speak, to talk". For nouns, just the meaning: "house, home". Omit (use default null) for: punctuation, spaces, newlines, articles (el/la/un/una/il/lo etc.), coordinating conjunctions (y/e/o/ma/e), and prepositions that are part of a chunk.
- chunk_definition: place on the FIRST token of a chunk only. Provide the English meaning of the whole chunk as a unit. For compound tenses, describe the tense: "had eaten (pluperfect)", "has left (perfect)". For fixed expressions: the idiomatic meaning: "nevertheless", "to realise, to become aware of".
- fixed_expr_canonical: for fixed expression chunks, give the canonical infinitive/dictionary form: e.g. "darse cuenta de", "tener en cuenta", "a pesar de".

## Known lemmas — skip definition_lemma for these

The following lemmas already have definitions. If a token's lemma exactly matches one in this list, omit definition_lemma entirely (leave it as default null).

{known_lemmas_list}

## Text to annotate

Language: {language_name} ({language_code})

{text}"""


async def parse_text(text: str, language: str) -> list[dict]:
    """
    Parse text using the Claude API and return a list of token dicts.

    Args:
        text: The raw text to parse.
        language: "es" for Spanish or "it" for Italian.

    Returns:
        List of token dicts conforming to the DVNR token schema.
    """
    language_names = {"es": "Spanish", "it": "Italian"}
    language_name = language_names.get(language, "Spanish")

    known_lemmas = KNOWN_LEMMAS_ES if language == "es" else {}
    known_lemmas_list = ", ".join(sorted(known_lemmas.keys())) if known_lemmas else "(none)"

    prompt = _PROMPT_TEMPLATE.format(
        language_name=language_name,
        language_code=language,
        known_lemmas_list=known_lemmas_list,
        text=text,
    )

    word_count = count_words(text)
    log.info("parse_text called: language=%s words=%d prompt_chars=%d", language, word_count, len(prompt))

    client = get_client()

    t0 = time.monotonic()
    log.debug("Sending request to Claude API (streaming)...")
    chunks: list[str] = []
    input_tokens = output_tokens = 0
    stop_reason = None

    async with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=16000,
        messages=[
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "["},
        ],
    ) as stream:
        chunks.append("[")
        async for text in stream.text_stream:
            chunks.append(text)
        final = await stream.get_final_message()
        input_tokens = final.usage.input_tokens
        output_tokens = final.usage.output_tokens
        stop_reason = final.stop_reason

    elapsed = time.monotonic() - t0
    log.info(
        "Claude API completed in %.1fs: input_tokens=%d output_tokens=%d stop_reason=%s",
        elapsed,
        input_tokens,
        output_tokens,
        stop_reason,
    )

    raw = "".join(chunks).strip()
    log.debug("Raw response length: %d chars", len(raw))

    # Strip any accidental markdown code fences
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    # If Claude still produced a preamble, find the start of the JSON array
    if not raw.startswith("["):
        idx = raw.find("[")
        if idx != -1:
            log.warning("Claude produced preamble (%d chars) before JSON array — stripping", idx)
            raw = raw[idx:]
        else:
            log.error("No JSON array found in response — first 500 chars: %r", raw[:500])

    try:
        tokens: list[dict] = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error("JSON decode failed: %s — first 500 chars of raw: %r", e, raw[:500])
        raise
    log.info("Parsed %d tokens", len(tokens))

    # Expand compact format: fill in all omitted fields with their defaults
    defaults = {
        "idx": 0,
        "text": "",
        "lemma": "",
        "pos": "",
        "is_punct": False,
        "is_space": False,
        "is_newline": False,
        "is_title": False,
        "chunk_id": None,
        "chunk_role": "solo",
        "definition_lemma": None,
        "definition_surface": None,
        "chunk_definition": None,
        "fixed_expr_canonical": None,
    }
    for tok in tokens:
        for field, default in defaults.items():
            if field not in tok:
                tok[field] = default

    # Fill definition_lemma from local known-lemmas list where Claude skipped it
    if known_lemmas:
        filled = 0
        for tok in tokens:
            if tok["definition_lemma"] is None and tok["lemma"] in known_lemmas:
                tok["definition_lemma"] = known_lemmas[tok["lemma"]]
                filled += 1
        log.info("Filled %d definition_lemma values from known-lemmas list", filled)

    return tokens


def count_words(text: str) -> int:
    """Count whitespace-delimited words in text."""
    return len(text.split())
