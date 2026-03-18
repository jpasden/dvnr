"""Slug generation utility for DVNR."""

import re
import unicodedata


def slugify(title: str) -> str:
    """
    Convert a title string to a URL-safe slug.

    Example: "El Secreto de Mateo" -> "el-secreto-de-mateo"
    """
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode("ascii")
    title = title.lower().strip()
    title = re.sub(r"[^\w\s-]", "", title)
    title = re.sub(r"[\s_-]+", "-", title)
    title = re.sub(r"^-+|-+$", "", title)
    return title
