# Archived: spaCy + Wiktionary NLP Pipeline

These files are the original NLP pipeline for DVNR, archived when the project
was refactored to use the Claude API for text parsing (2026-03-15).

## What was here

- **nlp.py** — spaCy-based tokeniser, chunker, and pipeline entry point.
  Loaded `es_core_news_sm` and `it_core_news_sm` models at startup and used
  rule-based chunk detection (fixed expressions, article+noun, compound tenses,
  reflexive clitics, verb+preposition pairs).

- **wiktionary.py** — Async Wiktionary REST API client. Fetched definitions
  for every unique lemma and surface form in a parsed text, following form-of
  references one level deep.

- **language.py** — `langdetect`-based language detector (Spanish / Italian).

- **fixed_expressions_es.json** — Curated list of Spanish fixed expressions
  and idioms with lemma sequences and English definitions.

- **fixed_expressions_it.json** — Same for Italian.

## Why it was replaced

The spaCy approach required two large model downloads, a complex multi-pass
chunking algorithm, and hundreds of Wiktionary HTTP requests per parse (5–30
seconds). The Claude API approach replaces all of this with a single API call
that handles tokenisation, lemmatisation, POS tagging, chunk detection, and
definitions simultaneously, producing richer and more accurate output.
