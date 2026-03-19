from langdetect import detect, LangDetectException


def detect_language(text: str) -> str:
    """Detect whether text is Spanish or Italian. Defaults to 'es' on failure."""
    if not text or len(text.strip()) < 10:
        return "es"
    try:
        lang = detect(text)
        if lang in ("es", "it"):
            return lang
        return "es"
    except LangDetectException:
        return "es"
