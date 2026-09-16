"""Temporal helpers for API Engineer node."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Tuple

from app.workflow.nodes.api_engineer.constants import _FOLLOWUP_STOPWORDS


def _extract_year_range(raw_text: str) -> Tuple[int, int]:
    """Extract `(year_start, year_end)` from the query; fallback to last 5 years."""
    current_year = date.today().year
    years = sorted({
        int(m.group(0))
        for m in re.finditer(r"\b(?:19|20)\d{2}\b", raw_text)
        if 1900 <= int(m.group(0)) <= current_year
    })
    if not years:
        return current_year - 4, current_year
    if len(years) == 1:
        return years[0], years[0]
    return years[0], years[-1]


def _is_temporal_followup(raw_text: str, previous_context: Dict[str, Any]) -> bool:
    if not previous_context or previous_context.get("source") != "GUS":
        return False
    if not re.search(r"\b(?:19|20)\d{2}\b", raw_text):
        return False
    tokens = [
        t for t in re.findall(r"\w+", raw_text.lower())
        if t not in _FOLLOWUP_STOPWORDS and not t.isdigit()
    ]
    return len(tokens) == 0