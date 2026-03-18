"""
Local tokenizer for DVNR.

Splits raw text into a token array (words, spaces, punctuation, newlines)
without any API calls. Also detects the title line and computes word count.

Token schema (fields always present after tokenization):
  idx          int   — 0-based sequential index
  text         str   — exact characters of this token
  is_space     bool  — single space between words
  is_newline   bool  — line break
  is_punct     bool  — punctuation mark
  is_title     bool  — part of the title line
  lemma        str   — empty string until filled by definition fetcher
  pos          str   — empty string until filled by definition fetcher
  definition_lemma  str|None — None until filled
  chunk_id     None  — always None (chunking not implemented)
  chunk_role   str   — always "solo"
  chunk_definition  None
  fixed_expr_canonical  None
  definition_surface    None
"""

import re
import unicodedata

# Characters treated as punctuation tokens
_PUNCT_RE = re.compile(r'[^\w\s]', re.UNICODE)

# Split pattern: capture spaces, newlines, and punctuation as separate tokens
# Order matters: newline first, then space, then punctuation, then word
_SPLIT_RE = re.compile(r'(\r\n|\r|\n| |[^\w\s])', re.UNICODE)

# Title detection: first non-empty line with ≤15 words and no trailing period
_MAX_TITLE_WORDS = 15


def tokenize(text: str) -> list[dict]:
    """
    Tokenise raw text into a list of token dicts.
    No API calls — purely local.
    """
    parts = _SPLIT_RE.split(text)
    # _SPLIT_RE.split returns interleaved [non-match, match, non-match, ...]
    # Flatten and filter empty strings
    raw_tokens: list[str] = [p for p in parts if p != ""]

    tokens: list[dict] = []
    idx = 0
    for part in raw_tokens:
        if part in ("\r\n", "\r", "\n"):
            tokens.append(_make_token(idx, "\n", is_newline=True))
        elif part == " ":
            tokens.append(_make_token(idx, " ", is_space=True))
        elif _PUNCT_RE.fullmatch(part):
            tokens.append(_make_token(idx, part, is_punct=True))
        else:
            tokens.append(_make_token(idx, part))
        idx += 1

    _mark_title(tokens)
    return tokens


def count_words(text: str) -> int:
    """Count whitespace-delimited words in text."""
    return len(text.split())


def get_content_words(tokens: list[dict]) -> list[dict]:
    """
    Return only tokens that are content words:
    not space, not newline, not punctuation.
    """
    return [
        t for t in tokens
        if not t["is_space"] and not t["is_newline"] and not t["is_punct"]
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(idx: int, text: str, *, is_space=False, is_newline=False, is_punct=False) -> dict:
    return {
        "idx": idx,
        "text": text,
        "is_space": is_space,
        "is_newline": is_newline,
        "is_punct": is_punct,
        "is_title": False,
        "lemma": "",
        "pos": "",
        "definition_lemma": None,
        "definition_surface": None,
        "chunk_id": None,
        "chunk_role": "solo",
        "chunk_definition": None,
        "fixed_expr_canonical": None,
    }


def _mark_title(tokens: list[dict]) -> None:
    """
    Detect the title line and set is_title=True on its tokens.

    Rule: the first non-empty line has ≤15 words and does not end with a period.
    """
    # Collect tokens up to (not including) the first newline
    first_line: list[dict] = []
    for tok in tokens:
        if tok["is_newline"]:
            break
        first_line.append(tok)

    if not first_line:
        return

    # Count words in first line
    words = [t for t in first_line if not t["is_space"] and not t["is_newline"] and not t["is_punct"]]
    if len(words) > _MAX_TITLE_WORDS:
        return

    # Check last non-space, non-newline token is not a period
    visible = [t for t in first_line if not t["is_space"]]
    if not visible:
        return
    if visible[-1]["text"] == ".":
        return

    # Mark all tokens in the first line as title (but NOT the newline itself)
    for tok in first_line:
        tok["is_title"] = True
