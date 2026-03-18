"""
Simple language detection heuristic for DVNR.

Used only as a fallback/utility; the primary language selection is explicit
in the admin form. The full langdetect-based version has been archived to
archive/wiktionary_spacy/language.py.
"""


def detect_language(text: str) -> str:
    """
    Detect whether text is Spanish or Italian using a simple word-frequency
    heuristic. Defaults to 'es' (Spanish) when uncertain.
    """
    italian_markers = {
        "il", "la", "le", "gli", "una", "sono", "è", "che", "non",
        "con", "per", "nel", "della", "dello", "degli", "nelle",
        "questo", "questa", "questi", "queste", "anche", "però",
    }
    words = set(text.lower().split()[:50])
    italian_count = len(words & italian_markers)
    return "it" if italian_count >= 3 else "es"
