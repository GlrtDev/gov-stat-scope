"""Text normalization helpers."""
from __future__ import annotations

import re
import unicodedata


def _normalize_text(text: str) -> str:
    """Lowercase, strip diacritics, keep only letters/digits/spaces."""
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9\s]", " ", text).strip()