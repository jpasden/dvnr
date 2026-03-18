"""
Definition fetcher for DVNR.

Given a list of unknown content words (with sentence context), calls the
Claude Haiku API to fetch concise English definitions and lemmas.

Yields progress events as (word, lemma, definition) tuples suitable for
streaming via SSE back to the admin UI.

Uses assistant prefill to prevent preamble and ensure pure JSON output.
"""

import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Optional

import anthropic

log = logging.getLogger("dvnr.definition_fetcher")

_client: Optional[anthropic.AsyncAnthropic] = None

# Words per API batch — small enough to stay well under 10K output tokens/min
_BATCH_SIZE = 30

_LANGUAGE_NAMES = {"es": "Spanish", "it": "Italian"}


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


async def fetch_definitions(
    words: list[dict],
    language: str,
) -> AsyncGenerator[dict, None]:
    """
    Fetch definitions for a list of unknown words, yielding one result dict
    per word as each API call completes.

    Each item in `words` is:
      {
        "text": str,       # surface form as it appears in text
        "context": str,    # the sentence it appears in
        "idx": int,        # token index (passed through for caller convenience)
      }

    Yields dicts:
      {
        "idx": int,
        "text": str,
        "lemma": str,
        "definition": str,
        "error": bool,     # True if this word failed
      }
    """
    language_name = _LANGUAGE_NAMES.get(language, "Spanish")
    client = get_client()

    # Process in batches
    for batch_start in range(0, len(words), _BATCH_SIZE):
        batch = words[batch_start: batch_start + _BATCH_SIZE]
        results = await _fetch_batch(client, batch, language_name)

        for word_info in batch:
            text = word_info["text"]
            idx = word_info["idx"]
            result = results.get(text.lower(), results.get(text, None))

            if result and isinstance(result, dict):
                yield {
                    "idx": idx,
                    "text": text,
                    "lemma": result.get("lemma", ""),
                    "definition": result.get("definition", ""),
                    "error": False,
                }
            else:
                log.warning("No definition returned for %r", text)
                yield {
                    "idx": idx,
                    "text": text,
                    "lemma": "",
                    "definition": "",
                    "error": True,
                }


async def _fetch_batch(
    client: anthropic.AsyncAnthropic,
    batch: list[dict],
    language_name: str,
) -> dict:
    """
    Call Claude Haiku for one batch of words.
    Returns a dict mapping surface form (lowercased) → {lemma, definition}.
    """
    lines = []
    for w in batch:
        lines.append(f'- "{w["text"]}" (in: "{w["context"]}")')
    word_list = "\n".join(lines)

    prompt = f"""\
For each {language_name} word below, return a JSON object mapping the word \
(exactly as written, including capitalisation) to an object with two fields:
- "lemma": the dictionary/infinitive form, lowercase
- "definition": a concise English gloss of 1–8 words

Return ONLY a JSON object. No markdown, no explanation, no preamble.

Words:
{word_list}"""

    log.debug("Fetching definitions for batch of %d words", len(batch))

    raw_chunks: list[str] = ["{"]
    try:
        async with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "{"},
            ],
        ) as stream:
            async for chunk in stream.text_stream:
                raw_chunks.append(chunk)
            final = await stream.get_final_message()
            log.debug(
                "Batch complete: input_tokens=%d output_tokens=%d stop_reason=%s",
                final.usage.input_tokens,
                final.usage.output_tokens,
                final.stop_reason,
            )
    except Exception as e:
        log.error("API call failed for batch: %s", e)
        return {}

    raw = "".join(raw_chunks).strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        lines_raw = raw.splitlines()[1:]
        if lines_raw and lines_raw[-1].strip().startswith("```"):
            lines_raw = lines_raw[:-1]
        raw = "\n".join(lines_raw).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.error("JSON decode failed for batch: %s — raw: %r", e, raw[:300])
        return {}
