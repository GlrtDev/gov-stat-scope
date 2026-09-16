"""Pure helper utilities for the GUS adapter.

No HTTP or client state lives here so these functions can be reused by
both ``gus_api`` and ``gus``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_YEAR = 2023

GUS_TERM_ALIASES = {
    "population": "ludność",
    "ludnosc": "ludność",
    "wheat": "pszenica",
    "rye": "żyto",
    "price": "cena",
    "prices": "ceny",
    "salary": "wynagrodzenie",
    "employment": "pracujący",
    "unemployment": "bezrobocie",
}


class TTLCache:
    """Small TTL cache used to avoid repeated GUS calls."""

    def __init__(self, ttl_seconds: float = 3600) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic() + self._ttl, value)


def normalize_text(value: str) -> str:
    """Normalize text for searching/matching."""
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def api_search_text(query: str) -> str:
    """Prepare query for the GUS API, preserving Polish diacritics."""
    q = query.strip().lower()
    q = GUS_TERM_ALIASES.get(q, q)
    # Restore a few common Polish diacritics if typed without them.
    q = q.replace("ludnosc", "ludność")
    q = q.replace("pszenzyto", "pszenżyto")
    q = q.replace("zboze", "zboże")
    q = q.replace("zywienie", "żywienie")
    return " ".join(q.split())


def parse_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_present(data: dict, *keys: str, default: Any = None) -> Any:
    """Return the first key found in a dict."""
    if not isinstance(data, dict):
        return default
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def extract_results(payload: Any) -> List[Dict[str, Any]]:
    """Extract a list of dicts from a GUS API response."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "value", "values"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        # Some endpoints return a single object.
        if payload.get("id") is not None or payload.get("name") is not None:
            return [payload]
    return []


def subject_id(subject: Dict[str, Any]) -> str:
    return str(
        first_present(
            subject,
            "id",
            "subject-id",
            "subjectId",
            "code",
            default="",
        )
    )


def score_variable(variable: Dict[str, Any], query: str) -> int:
    name = str(
        first_present(
            variable,
            "name",
            "title",
            "description",
            default="",
        )
    )
    norm_name = normalize_text(name)
    query_norm = normalize_text(query)
    if not query_norm:
        return 0

    score = 0
    if norm_name == query_norm:
        score += 1000
    elif query_norm in norm_name:
        score += 500
    elif norm_name in query_norm:
        score += 300

    query_tokens = set(query_norm.split())
    name_tokens = set(norm_name.split())
    for qt in query_tokens:
        for nt in name_tokens:
            if qt in nt or nt in qt:
                score += 50
    return score


def score_unit(query: str, unit_name: str) -> int:
    query_norm = normalize_text(query)
    unit_norm = normalize_text(unit_name)
    if not query_norm or not unit_norm:
        return 0
    if query_norm == unit_norm:
        return 1000
    if query_norm in unit_norm or unit_norm in query_norm:
        return 500
    return 0


def normalize_years(years: Any, default_year: int = DEFAULT_YEAR) -> List[int]:
    if years is None:
        return [default_year]
    if isinstance(years, int):
        return [years]
    if isinstance(years, str):
        years = years.split(",")

    result: List[int] = []
    for year in years:
        parsed = parse_int(year)
        if parsed is not None:
            result.append(parsed)

    if not result:
        result = [default_year]
    return result


def select_result(
    results: List[Dict[str, Any]],
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    if not results:
        return {}

    unit_id = kwargs.get("unit_id") or kwargs.get("unitId")
    region = kwargs.get("region")

    for result in results:
        result_unit_id = str(
            first_present(
                result,
                "id",
                "unit-id",
                "unitId",
                default="",
            )
        )
        if unit_id is not None and str(unit_id) == result_unit_id:
            return result

    if region:
        for result in results:
            unit_name = str(
                first_present(
                    result,
                    "unit-name",
                    "unitName",
                    "name",
                    default="",
                )
            )
            region_norm = normalize_text(str(region))
            unit_norm = normalize_text(unit_name)
            if region_norm and (
                region_norm in unit_norm or unit_norm in region_norm
            ):
                return result

    return results[0]


# Backwards-compatible private aliases.
_normalize_text = normalize_text
_api_search_text = api_search_text
_parse_int = parse_int
_parse_float = parse_float
_first_present = first_present
_extract_results = extract_results
_subject_id = subject_id
_score_variable = score_variable
_unit_score = score_unit
_normalize_years = normalize_years
_select_result = select_result