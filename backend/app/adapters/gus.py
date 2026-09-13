# backend/app/adapters/gus.py
"""GUS BDL adapter for the GovStatScope AI Orchestrator."""

from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import json
import logging
import os
import random
import re
import time
import unicodedata
from collections import Counter
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.adapters.base import DataSourceClient
from app.adapters.schemas import DataPoint, NormalizedSeries
from app.models import DataSource


logger = logging.getLogger(__name__)


class GUSAOError(Exception):
    """Base exception for GUS Adapter errors."""


class GUSNotFoundError(GUSAOError):
    """Raised when a requested resource is not found in the GUS API."""


class GUSAuthenticationError(GUSAOError):
    """Raised when GUS rejects the supplied client identifier."""


class GUSRateLimitError(GUSAOError):
    """Raised when GUS rate limiting is exceeded and no retry remains."""


class GUSServerError(GUSAOError):
    """Raised when GUS returns a temporary server-side error."""


_POLISH_REPLACEMENTS = {
    "ł": "l",
    "ć": "c",
    "ę": "e",
    "ń": "n",
    "ś": "s",
    "ź": "z",
    "ż": "z",
}


def _normalize_gus_api_key(raw_value: Optional[str]) -> Optional[str]:
    """Return a real GUS API key, or None if the value is missing/placeholder."""
    if raw_value is None:
        return None

    key = raw_value.strip()
    if not key:
        return None

    normalized = key.lower().replace(" ", "_")
    placeholder_values = {
        "none",
        "null",
        "undefined",
        "placeholder",
        "changeme",
        "dummy",
        "your_gus_key_here",
        "your-gus-key-here",
        "your_gus_client_id_here",
        "dummy_gus_key_for_local",
    }

    if normalized in placeholder_values:
        return None

    if normalized.startswith("dummy"):
        return None

    if "your_" in normalized or "your-" in normalized:
        return None

    return key


def _normalize_text(value: str) -> str:
    """Normalize Polish/English text for deterministic matching."""
    text = str(value).lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))

    for polish_char, ascii_char in _POLISH_REPLACEMENTS.items():
        text = text.replace(polish_char, ascii_char)

    return re.sub(r"\s+", " ", text).strip()

def _normalize_polish_search(text: str) -> str:
    """Lowercase and trim, but preserve Polish diacritics for GUS search."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())

def _first_present(mapping: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first non-None value from a mapping using possible field aliases."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _parse_int(value: Any) -> Optional[int]:
    """Parse an integer safely from API payloads or user kwargs."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _extract_results(payload: Any) -> List[Dict[str, Any]]:
    """Extract a list of result objects from common GUS response envelopes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("results", "content", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def _variable_matches(name: str, query: str) -> bool:
    if not name or not query:
        return False
    if query in name or name in query:
        return True

    query_tokens = {t for t in query.split() if len(t) > 2}
    name_tokens = {t for t in name.split() if len(t) > 2}

    if query_tokens & name_tokens:
        return True

    return any(
        SequenceMatcher(None, qt, nt).ratio() >= 0.8
        for qt in query_tokens
        for nt in name_tokens
    )

class _TTLCache:
    """Small bounded TTL cache for metadata lookups."""

    def __init__(self, ttl_seconds: int, max_items: int = 1024) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._items: Dict[str, Tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        item = self._items.get(key)

        if item is None:
            return None

        value, expires_at = item
        if expires_at <= now:
            del self._items[key]
            return None

        return value

    def set(self, key: str, value: Any) -> None:
        if key not in self._items and len(self._items) >= self._max_items:
            oldest_key = next(iter(self._items))
            del self._items[oldest_key]

        self._items[key] = (value, time.monotonic() + self._ttl_seconds)

    def clear(self) -> None:
        self._items.clear()


class GUSClient(DataSourceClient):
    """
    Adapter for the Polish Central Statistical Office (GUS) Local Data Bank (BDL) API.

    Supports optional API key authentication via X-ClientId header, falling back to
    unauthenticated rate limits when no valid key is configured.
    """

    BASE_URL = "https://bdl.stat.gov.pl/api/v1/"

    def __init__(
        self,
        api_key: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        raw_api_key = api_key or os.getenv("GUS_API_KEY") or os.getenv("GUS_CLIENT_ID")
        resolved_api_key = _normalize_gus_api_key(raw_api_key)

        if raw_api_key is not None and resolved_api_key is None:
            logger.warning(
                "GUS API key looks like a placeholder or dummy value. "
                "Using unauthenticated low-rate access. Set a real GUS_API_KEY/GUS_CLIENT_ID."
            )

        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "GovStatScope-GUS-Adapter/1.0",
        }
        if resolved_api_key:
            headers["X-ClientId"] = resolved_api_key

        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

        self.page_size = 100
        self.max_pages = 200
        self.max_retries = 4

        self._unit_cache = _TTLCache(ttl_seconds=86_400)
        self._variable_search_cache = _TTLCache(ttl_seconds=3_600)
        self._resolved_variable_cache = _TTLCache(ttl_seconds=3_600)

    async def close(self) -> None:
        """Closes the underlying HTTP client and clears metadata caches."""
        await self.client.aclose()
        self._unit_cache.clear()
        self._variable_search_cache.clear()
        self._resolved_variable_cache.clear()

    @staticmethod
    def _backoff_delay(attempt: int, retry_after_seconds: Optional[float] = None) -> float:
        if retry_after_seconds is not None:
            return min(max(float(retry_after_seconds), 1.0), 60.0)

        base_delay = 2 ** attempt
        jitter = random.uniform(0.0, 1.0)
        return min(base_delay + jitter, 30.0)

    @staticmethod
    def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
        if not header_value:
            return None

        try:
            return float(header_value)
        except ValueError:
            return None

    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a GUS request with retry for transient failures only."""
        path = endpoint.lstrip("/")
        last_error: Optional[GUSAOError] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self.client.request(method, path, params=params)
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                last_error = GUSAOError(f"GUS request failed for {path}: {exc}")

                if attempt == self.max_retries:
                    raise last_error from exc

                await asyncio.sleep(self._backoff_delay(attempt))
                continue

            status_code = response.status_code

            if status_code == 404:
                raise GUSNotFoundError(f"Resource not found at {path} with params {params}")

            if status_code in (401, 403):
                raise GUSAuthenticationError(
                    f"GUS authentication failed for {path}. Status: {status_code}. "
                    "Check GUS_API_KEY / X-ClientId configuration."
                )

            if status_code == 429:
                retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
                last_error = GUSRateLimitError(f"GUS rate limit exceeded for {path}.")

                if attempt == self.max_retries:
                    raise last_error

                await asyncio.sleep(self._backoff_delay(attempt, retry_after))
                continue

            if 500 <= status_code < 600:
                last_error = GUSServerError(f"GUS server error for {path}. Status: {status_code}")

                if attempt == self.max_retries:
                    raise last_error

                await asyncio.sleep(self._backoff_delay(attempt))
                continue

            if status_code >= 400:
                raise GUSAOError(f"GUS API Error: {status_code} - {response.text}")

            try:
                return response.json()
            except ValueError as exc:
                raise GUSAOError(f"GUS API returned invalid JSON from {path}.") from exc

        if last_error is not None:
            raise last_error

        raise GUSAOError(f"Unable to complete GUS request for {path}.")

    async def _get_page(
        self,
        endpoint: str,
        params: Dict[str, Any],
        page: int,
        page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Request one page from a paginated GUS endpoint."""
        size = page_size or self.page_size
        request_params: Dict[str, Any] = dict(params)
        request_params["page"] = page
        request_params["size"] = size
        request_params["page-size"] = size

        return await self._request("GET", endpoint, params=request_params)

    async def _get_all_pages(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Fetch all pages from a paginated GUS endpoint and merge them."""
        base_params: Dict[str, Any] = dict(params or {})
        all_results: List[Dict[str, Any]] = []
        seen_keys = set()

        page = 0
        while page < self.max_pages:
            data = await self._get_page(endpoint, base_params, page)
            results = _extract_results(data)

            if not results:
                break

            new_items = 0
            for item in results:
                id_value = item.get("id")
                key = (
                    f"id:{id_value}"
                    if id_value is not None
                    else json.dumps(item, sort_keys=True, default=str)
                )

                if key not in seen_keys:
                    seen_keys.add(key)
                    all_results.append(item)
                    new_items += 1

            if new_items == 0 or len(results) < self.page_size:
                break

            page += 1

        return {"results": all_results}

    async def _fetch_paginated_items(
        self,
        endpoint: str,
        params: Dict[str, Any],
        max_items: int,
    ) -> List[Dict[str, Any]]:
        """Fetch a bounded number of items from a paginated search/list endpoint."""
        collected: List[Dict[str, Any]] = []
        page = 0

        while len(collected) < max_items and page < self.max_pages:
            data = await self._get_page(endpoint, params, page)
            results = _extract_results(data)

            if not results:
                break

            collected.extend(results)

            if len(results) < self.page_size:
                break

            page += 1

        return collected[:max_items]

    @staticmethod
    def _dedupe_by_id(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unique: List[Dict[str, Any]] = []
        seen = set()

        for item in items:
            id_value = item.get("id")
            key = (
                f"id:{id_value}"
                if id_value is not None
                else json.dumps(item, sort_keys=True, default=str)
            )

            if key not in seen:
                seen.add(key)
                unique.append(item)

        return unique

    @staticmethod
    def _score_candidate(query_norm: str, candidate: Dict[str, Any]) -> int:
        """Score a variable candidate against a normalized user query."""
        english_name = _normalize_text(
            str(_first_present(candidate, "name-en", "nameEn", "englishName", default=""))
        )
        polish_name = _normalize_text(str(_first_present(candidate, "name", default="")))

        if query_norm and (query_norm == english_name or query_norm == polish_name):
            return 1_000

        score = 0
        query_tokens = set(re.findall(r"[a-z0-9]+", query_norm))

        for name in (english_name, polish_name):
            if not name:
                continue

            name_tokens = set(re.findall(r"[a-z0-9]+", name))
            overlap = len(query_tokens & name_tokens)

            score += overlap * 25

            if query_tokens:
                coverage = overlap / len(query_tokens)
                score += int(coverage * 100)

            if query_norm and (name.startswith(query_norm) or query_norm.startswith(name)):
                score += 80

        return score

    @staticmethod
    def _unit_score(target_norm: str, unit: Dict[str, Any]) -> int:
        """Score a unit candidate against a normalized location query."""
        name = _normalize_text(str(_first_present(unit, "name", default="")))
        if not name:
            return -1

        if target_norm == name:
            return 1_000

        score = 0
        target_tokens = set(re.findall(r"[a-z0-9]+", target_norm))
        name_tokens = set(re.findall(r"[a-z0-9]+", name))

        overlap = len(target_tokens & name_tokens)
        score += overlap * 30

        if target_norm and (name.startswith(target_norm) or target_norm.startswith(name)):
            score += 120

        return score

    async def _search_variable_candidates(
    self,
    query: str,
    subject_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # GUS requires native Polish spelling here, not ASCII-folded text.
        search_query = _normalize_polish_search(query)

        if not search_query:
            raise GUSNotFoundError("GUS variable query is empty.")

        cache_key = f"variable_search:{subject_id or 'all'}:{search_query}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

        params = {
            "search": search_query,
            "lang": "pl",
            "format": "json",
        }

        if subject_id is not None:
            params["subjectId"] = str(subject_id)

        payload = await self._get_all_pages("variables/search", params)
        results = _extract_results(payload)
        results = self._dedupe_by_id(results)

        self._cache.set(cache_key, results)
        return list(results)

    async def resolve_variable_id(self, query: str, **kwargs: Any) -> str:
        """Resolve a natural-language metric phrase to the best GUS variable ID."""
        normalized_query = _normalize_text(query)

        if not normalized_query:
            raise GUSNotFoundError("GUS variable query is empty.")

        cache_key = f"variable_id:{normalized_query}"
        cached_id = self._cache.get(cache_key)

        if cached_id is not None:
            return str(cached_id)

        def pick_best(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            best = None
            best_score = 0

            for candidate in candidates:
                score = self._score_candidate(normalized_query, candidate)

                if score > best_score:
                    best_score = score
                    best = candidate

            return best if best_score > 0 else None

        # 1. Subject-aware search:
        #    /subjects -> /subjects/K15 -> /subjects/G186
        subject_id, remaining_query = await self._find_subject_and_remaining(query)

        if subject_id:
            candidates = await self._search_variable_candidates(
                remaining_query or query,
                subject_id=subject_id,
            )

            best = pick_best(candidates)

            if best is not None:
                variable_id = str(_first_present(best, "id", default=""))

                if variable_id:
                    self._cache.set(cache_key, variable_id)
                    return variable_id

        # 2. Fallback: direct full-query search.
        candidates = await self._search_variable_candidates(query)
        best = pick_best(candidates)

        if best is not None:
            variable_id = str(_first_present(best, "id", default=""))

            if variable_id:
                self._cache.set(cache_key, variable_id)
                return variable_id

        raise GUSNotFoundError(f"No GUS variable found matching query: {query}")

    async def _find_unit_in_level(self, name: str, level: int) -> Optional[Dict[str, Any]]:
        """Fallback unit resolver that scans a single administrative level."""
        target_norm = _normalize_text(name)
        page = 0

        while page < self.max_pages:
            data = await self._get_page("units", {"level": level}, page)
            results = _extract_results(data)

            if not results:
                break

            best_unit: Optional[Dict[str, Any]] = None
            best_score = -1

            for unit in results:
                score = self._unit_score(target_norm, unit)
                if score > best_score:
                    best_score = score
                    best_unit = unit

            if best_unit is not None and best_score > 0:
                return best_unit

            if len(results) < self.page_size:
                break

            page += 1

        return None

    async def resolve_unit(self, name: str) -> Dict[str, Any]:
        """Resolve a location phrase to a GUS unit reference."""
        target_norm = _normalize_text(name)
        if not target_norm:
            raise GUSNotFoundError("Unit name is empty.")

        cache_key = f"unit:{target_norm}"
        cached_unit = self._unit_cache.get(cache_key)
        if cached_unit is not None:
            return dict(cached_unit)

        if target_norm in {"poland", "polska", "pl"}:
            unit = {"id": 0, "name": "Poland", "level": 0}
            self._unit_cache.set(cache_key, unit)
            return dict(unit)

        try:
            search_results = await self._fetch_paginated_items(
                endpoint="units/search",
                params={"name": name},
                max_items=100,
            )
        except GUSNotFoundError:
            search_results = []

        best_unit: Optional[Dict[str, Any]] = None
        best_score = -1

        for unit in search_results:
            score = self._unit_score(target_norm, unit)
            if score > best_score:
                best_score = score
                best_unit = unit

        if best_unit is not None and best_score > 0:
            resolved = {
                "id": _parse_int(_first_present(best_unit, "id")) or 0,
                "name": str(_first_present(best_unit, "name", default=name)),
                "level": _parse_int(_first_present(best_unit, "level", "unitLevel", default=1)) or 1,
            }
            self._unit_cache.set(cache_key, resolved)
            return dict(resolved)

        for level in (1, 2):
            found = await self._find_unit_in_level(name, level)
            if found is not None:
                resolved = {
                    "id": _parse_int(_first_present(found, "id")) or 0,
                    "name": str(_first_present(found, "name", default=name)),
                    "level": level,
                }
                self._unit_cache.set(cache_key, resolved)
                return dict(resolved)

        raise GUSNotFoundError(f"No GUS unit found matching '{name}'.")

    @staticmethod
    def _normalize_years(years: Any) -> List[int]:
        """Normalize user/API year input into a sorted list of unique ints."""
        if not years:
            return []

        if isinstance(years, (list, tuple, set)):
            raw_items = list(years)
        elif isinstance(years, str):
            raw_items = re.split(r"[,\s]+", years.strip())
        else:
            raw_items = [years]

        unique_years: List[int] = []
        seen = set()

        for item in raw_items:
            year = _parse_int(item)
            if year is not None and year not in seen:
                seen.add(year)
                unique_years.append(year)

        return sorted(unique_years)

    @staticmethod
    def _date_from_value(value_obj: Dict[str, Any]) -> str:
        """Extract an ISO date from a GUS value object."""
        raw = _first_present(value_obj, "year", "date", "period", default="")
        text = str(raw).strip()

        if not text:
            return "unknown"

        year_match = re.match(r"^(\d{4})$", text)
        if year_match:
            try:
                return date(int(year_match.group(1)), 1, 1).isoformat()
            except ValueError:
                return text

        date_part = text[:10]
        try:
            return datetime.fromisoformat(date_part).date().isoformat()
        except ValueError:
            return text

    @staticmethod
    def _parse_numeric(raw_value: Any) -> Optional[float]:
        """Parse GUS numeric values, preserving missing values as None."""
        if raw_value is None:
            return None

        if isinstance(raw_value, bool):
            return float(raw_value)

        if isinstance(raw_value, (int, float)):
            return float(raw_value)

        text = str(raw_value).strip().replace("\u00a0", " ")
        if not text or text in {".", "-", "--", "…"}:
            return None

        if "," in text and "." not in text:
            cleaned = text.replace(",", ".")
        elif "," in text and "." in text:
            cleaned = text.replace(",", "")
        else:
            cleaned = text

        try:
            return float(cleaned)
        except ValueError:
            return None

    def _select_unit_result(
        self,
        results: List[Dict[str, Any]],
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Select the correct unit result from a GUS data response."""
        unit_id_raw = kwargs.get("unit_id") or kwargs.get("id")
        if unit_id_raw is not None:
            target_id = _parse_int(unit_id_raw)
            if target_id is not None:
                for item in results:
                    if _parse_int(item.get("id")) == target_id:
                        return item

        region = str(kwargs.get("region") or kwargs.get("unit_name") or "")
        if region:
            target_region = _normalize_text(region)
            for item in results:
                name = _normalize_text(str(_first_present(item, "name", default="")))
                if name == target_region or target_region in re.findall(r"[a-z0-9]+", name):
                    return item

        if len(results) == 1:
            return results[0]

        for item in results:
            unit_id = _parse_int(item.get("id"))
            name = _normalize_text(str(_first_present(item, "name", default="")))
            if unit_id == 0 or name in {"poland", "polska"}:
                return item

        return results[0]

    def _select_attribute(
        self,
        values_raw: List[Dict[str, Any]],
        kwargs: Dict[str, Any],
    ) -> Optional[int]:
        """Select one attribute ID so different attributes are never mixed."""
        requested_attr = _parse_int(kwargs.get("attribute_id"))
        if requested_attr is not None:
            return requested_attr

        attr_ids: List[int] = []
        seen_attrs = set()

        for value_obj in values_raw:
            attr_id = _parse_int(_first_present(value_obj, "attrId", "attributeId", default=0))
            if attr_id is None:
                attr_id = 0

            if attr_id not in seen_attrs:
                seen_attrs.add(attr_id)
                attr_ids.append(attr_id)

        if len(attr_ids) <= 1:
            return attr_ids[0] if attr_ids else 0

        counts: Dict[int, int] = {}
        for value_obj in values_raw:
            attr_id = _parse_int(_first_present(value_obj, "attrId", "attributeId", default=0))
            if attr_id is None:
                attr_id = 0

            numeric_value = self._parse_numeric(_first_present(value_obj, "val", "value"))
            if numeric_value is not None:
                counts[attr_id] = counts.get(attr_id, 0) + 1

        return max(counts, key=lambda aid: (counts.get(aid, 0), -aid))

    # --- Metadata Discovery Methods ---

    async def list_regions(self, level: int = 0, page: int = 0, page_size: int = 100) -> Dict[str, Any]:
        """Lists geographical units at a specific administrative level."""
        return await self._get_page("units", {"level": level}, page, page_size)

    async def list_variables(self, subject_id: str, page: int = 0, page_size: int = 100) -> Dict[str, Any]:
        """Lists variables belonging to a specific subject category."""
        return await self._get_page("variables", {"subject-id": subject_id}, page, page_size)

    async def resolve_variable_id(self, name: str) -> str:
        """Searches for a variable by name and returns the best matching ID."""
        return await self._resolve_variable_id(name)

    # --- Interface Implementation Methods ---

    async def resolve_query(self, query: str) -> str:
        """Implements DataSourceClient.resolve_query."""
        return await self._resolve_variable_id(query)

    async def fetch_series(
        self,
        variable_id: str,
        unit_level: int = 0,
        year: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Fetches data for a variable at the requested administrative level."""
        return await self.fetch_data(variable_id, unit_level=unit_level, years=year)

    async def fetch_comparison(self, variable_id: str, unit_parent_id: str) -> Dict[str, Any]:
        """Fetches comparative data across multiple sub-units for a parent unit."""
        parent_id = _parse_int(unit_parent_id)
        if parent_id is None:
            raise GUSAOError(f"Invalid GUS parent unit id: {unit_parent_id}")

        return await self._get_all_pages(
            f"data/by-variable/{variable_id}",
            {"unit-parent-id": parent_id},
        )

    async def fetch_time_range(self, variable_id: str, unit_id: str, year_start: int, year_end: int) -> Dict[str, Any]:
        """Fetches data for a specific unit over a bounded time range."""
        parsed_variable_id = _parse_int(variable_id)
        parsed_unit_id = _parse_int(unit_id)

        if parsed_variable_id is None or parsed_unit_id is None:
            raise GUSAOError("GUS variable_id and unit_id must be numeric.")

        years = list(range(year_start, year_end + 1))
        return await self._get_all_pages(
            f"data/by-unit/{parsed_unit_id}",
            {"var-id": parsed_variable_id, "year": years},
        )

    async def fetch_data(self, resource_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Fetch raw GUS data using explicit unit/year kwargs when available."""
        variable_id = _parse_int(resource_id)
        if variable_id is None:
            raise GUSAOError(f"Invalid GUS variable id: {resource_id}")

        years = self._normalize_years(kwargs.get("years") or kwargs.get("year"))
        year_params: Dict[str, Any] = {}
        if years:
            year_params["year"] = years

        unit_id_raw = kwargs.get("unit_id") or kwargs.get("id")
        if unit_id_raw is not None:
            unit_id = _parse_int(unit_id_raw)
            if unit_id is None:
                raise GUSAOError(f"Invalid GUS unit id: {unit_id_raw}")

            return await self._get_all_pages(
                f"data/by-unit/{unit_id}",
                {"var-id": variable_id, **year_params},
            )

        level = _parse_int(kwargs.get("unit_level", kwargs.get("level")))
        if level is None:
            level = 0

        return await self._get_all_pages(
            f"data/by-variable/{variable_id}",
            {"unit-level": level, **year_params},
        )

    async def resolve_and_fetch(
        self,
        query: str,
        region: Optional[str] = None,
        years: Optional[List[int]] = None,
    ) -> NormalizedSeries:
        """High-level helper for resolving a metric and optional location, then fetching data."""
        variable_id = await self._resolve_variable_id(query)

        normalize_kwargs: Dict[str, Any] = {"metric_name": query}
        fetch_kwargs: Dict[str, Any] = {}

        if region:
            unit = await self.resolve_unit(region)
            normalize_kwargs["unit_id"] = int(unit.get("id", 0))
            normalize_kwargs["region"] = str(unit.get("name", region))
            fetch_kwargs["unit_id"] = normalize_kwargs["unit_id"]

        if years:
            fetch_kwargs["years"] = self._normalize_years(years)

        raw_data = await self.fetch_data(variable_id, **fetch_kwargs)
        return self.normalize_response(raw_data, resource_id=variable_id, **normalize_kwargs)

    def normalize_response(self, raw_data: Dict[str, Any], **kwargs: Any) -> NormalizedSeries:
        """Transform GUS BDL data into a single precise normalized series."""
        results = _extract_results(raw_data)
        if not results:
            raise GUSNotFoundError("The response contains no results to normalize.")

        selected_result = self._select_unit_result(results, kwargs)
        values_raw_value = _first_present(selected_result, "values", "data", default=[])
        values_raw: List[Dict[str, Any]] = (
            values_raw_value if isinstance(values_raw_value, list) else []
        )

        selected_attr_id = self._select_attribute(values_raw, kwargs)
        data_points: List[DataPoint] = []

        for value_obj in values_raw:
            if not isinstance(value_obj, dict):
                continue

            attr_id = _parse_int(_first_present(value_obj, "attrId", "attributeId", default=0))
            if selected_attr_id is not None and (attr_id or 0) != selected_attr_id:
                continue

            date_val = self._date_from_value(value_obj)
            numeric_value = self._parse_numeric(_first_present(value_obj, "val", "value"))

            data_points.append(
                DataPoint(
                    date=date_val,
                    value=numeric_value if numeric_value is not None else 0.0,
                )
            )

        if not data_points:
            raise GUSNotFoundError("GUS response contained no usable values for the selected attribute.")

        data_points.sort(key=lambda point: str(point.date))

        start_date = data_points[0].date
        end_date = data_points[-1].date
        time_period = f"{start_date} to {end_date}" if len(data_points) > 1 else start_date

        resource_id = kwargs.get("resource_id") or _first_present(selected_result, "id", default="unknown")
        metric_name = str(
            kwargs.get("metric_name")
            or _first_present(
                selected_result,
                "name",
                "variableName",
                default=f"GUS Variable {resource_id}",
            )
        )
        region_name = str(
            kwargs.get("region")
            or _first_present(selected_result, "unitName", "name", default="Poland")
        )

        return NormalizedSeries(
            source=DataSource.GUS,
            metric_name=metric_name,
            region=region_name,
            time_period=time_period,
            values=data_points,
        )


    async def _cached_subjects(self, parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        cache_key = f"subjects:{parent_id or 'root'}"
        cached = self._cache.get(cache_key)

        if cached is not None:
            return list(cached)

        endpoint = "subjects" if parent_id is None else f"subjects/{parent_id}"
        payload = await self._get_all_pages(
            endpoint,
            {"lang": "pl", "format": "json"},
        )

        subjects = _extract_results(payload)
        self._cache.set(cache_key, subjects)

        return list(subjects)


    async def _find_subject_and_remaining(self, query: str) -> Tuple[Optional[str], str]:
        """
        Walk the GUS subject hierarchy:

            /subjects
            /subjects/K15
            /subjects/G186

        and return:
            (deepest_subject_id, remaining_query_terms)
        """
        raw_query = query.strip()
        if not raw_query:
            return None, ""

        stopwords = {
            "w", "na", "dla", "od", "do", "i", "oraz", "o", "z", "ze", "we",
            "wpolsce", "polsce", "polska", "lata", "rok", "dane",
        }

        raw_tokens = re.findall(r"\S+", raw_query.lower())
        remaining_norm_tokens = {
            _normalize_text(tok)
            for tok in raw_tokens
            if _normalize_text(tok) not in stopwords
        }

        if not remaining_norm_tokens:
            return None, raw_query

        parent_id: Optional[str] = None
        consumed_norm_tokens: set = set()

        while True:
            subjects = await self._cached_subjects(parent_id)
            if not subjects:
                break

            best_subject = None
            best_score = 0
            best_consumed: set = set()
            best_name_length = 0

            for subject in subjects:
                name_raw = str(_first_present(subject, "name", "name-en", default=""))
                name_norm = _normalize_text(name_raw)

                if not name_norm:
                    continue

                name_tokens = set(re.findall(r"[a-z0-9]+", name_norm)) - stopwords

                if not name_tokens:
                    continue

                matched: set = set()

                for query_token in remaining_norm_tokens:
                    if query_token in name_tokens:
                        matched.add(query_token)
                        continue

                    for name_token in name_tokens:
                        # Handle Polish inflection reasonably:
                        # "pszenicy" vs "pszenica", "rolnictwie" vs "rolnictwo"
                        if len(name_token) >= 4 and (
                            query_token.startswith(name_token)
                            or name_token.startswith(query_token)
                        ):
                            matched.add(query_token)
                            break

                score = len(matched)

                if score > best_score or (
                    score == best_score
                    and len(name_tokens) > best_name_length
                ):
                    best_subject = subject
                    best_score = score
                    best_consumed = matched
                    best_name_length = len(name_tokens)

            if best_subject is None or best_score == 0:
                break

            best_id = str(_first_present(best_subject, "id", default=""))
            if not best_id:
                break

            parent_id = best_id
            remaining_norm_tokens -= best_consumed
            consumed_norm_tokens |= best_consumed

        remaining_raw_tokens = []

        for token in raw_tokens:
            norm_token = _normalize_text(token)

            if norm_token in consumed_norm_tokens or norm_token in stopwords:
                continue

            remaining_raw_tokens.append(token)

        return parent_id, " ".join(remaining_raw_tokens)


    async def query_data(
    self,
    query: str,
    region: Optional[str] = None,
    years: Any = None,
    **kwargs: Any ) -> NormalizedSeries:
        """Resolve a natural-language GUS query and return a normalized series."""
        variable = await self.resolve_variable(query, **kwargs)

        if isinstance(variable, dict):
            variable_id = int(
                variable.get("id")
                or variable.get("variableId")
                or variable.get("variable_id")
                or 0
            )
        else:
            variable_id = int(variable or 0)

        if not variable_id:
            raise GUSNotFoundError(f"No GUS variable found for query: {query}")

        year_values = years if years not in (None, [], "") else query
        year_list = self._coerce_years(year_values)

        if not year_list:
            raise GUSNotFoundError(f"No year found in query: {query}")

        unit_level = 0
        resolved_region = region or "Poland"

        if region:
            unit = await self.resolve_unit(region)
            unit_level = int(unit.get("level", 0) or 0)

        return await self.fetch_data(
            variable_id=variable_id,
            unit_level=unit_level,
            years=year_list,
            region=resolved_region,
            **kwargs,
        )

    @staticmethod
    def _coerce_years(years: Any) -> List[int]:
        """Return a sorted list of unique years from various input forms."""
        if years is None:
            return []

        if isinstance(years, int):
            return [years]

        if isinstance(years, str):
            matches = re.findall(r"(?:18|19|20)\d{2}", years)
            return sorted({int(year) for year in matches})

        return sorted({int(year) for year in years if year is not None})


    async def resolve_variable_id(self, query: str, **kwargs: Any) -> str:
        """Return the numeric GUS variable id for a query."""
        result = await self.resolve_variable(query, **kwargs)
        return str(result["id"])

    async def resolve_variable(self, query: str, **kwargs: Any) -> Dict[str, Any]:
        """Resolve a GUS variable by numeric id or Polish name."""
        q = str(query or "").strip()
        if not q:
            raise GUSNotFoundError("Variable query is empty.")

        if q.isdigit():
            return {"id": q, "name": q}

        # Never strip Polish diacritics in the API query.
        try:
            response = await self._request(
                "GET",
                "variables",
                params={"query": q, "lang": "pl"},
            )
            candidates = self._extract_results(response)
            best = self._pick_best_variable(candidates, q)
            if best is not None:
                return {
                    "id": str(best["id"]),
                    "name": str(_first_present(best, "name", default=q)),
                }
        except (GUSNotFoundError, GUSAOError):
            pass

        return await self._resolve_variable_via_subjects(q, **kwargs)

    def _extract_results(self, response: Any) -> List[Dict[str, Any]]:
        if isinstance(response, list):
            return [r for r in response if isinstance(r, dict)]
        if isinstance(response, dict):
            for key in ("results", "values", "data"):
                value = response.get(key)
                if isinstance(value, list):
                    return [r for r in value if isinstance(r, dict)]
        return []

    def _pick_best_variable(
        self, candidates: List[Dict[str, Any]], query: str
    ) -> Optional[Dict[str, Any]]:
        normalized_query = _normalize_text(query)
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0

        for candidate in candidates:
            name = str(_first_present(candidate, "name", default=""))
            if not name:
                continue
            score = SequenceMatcher(
                None, _normalize_text(name), normalized_query
            ).ratio()
            if score > best_score:
                best_score = score
                best = candidate

        return best if best_score >= 0.45 else None

    async def _resolve_variable_via_subjects(
        self, query: str, **kwargs: Any
    ) -> Dict[str, Any]:
        normalized_query = _normalize_text(query)
        top_level = await self._get_all_pages("subjects")
        queue = self._extract_results(top_level)
        seen: set[str] = set()
        visited = 0

        while queue and visited < 500:
            subject = queue.pop(0)
            subject_id = str(subject.get("id") or "")
            if not subject_id or subject_id in seen:
                continue
            seen.add(subject_id)
            visited += 1

            try:
                variables_payload = await self._get_all_pages(
                    f"subjects/{subject_id}/variables"
                )
                variables = self._extract_results(variables_payload)
            except GUSNotFoundError:
                variables = []

            for variable in variables:
                variable_id = str(
                    _first_present(variable, "id", "variableId", default="")
                )
                name = str(_first_present(variable, "name", default=""))
                if variable_id and _variable_matches(
                    _normalize_text(name), normalized_query
                ):
                    return {"id": variable_id, "name": name}

            try:
                children_payload = await self._get_all_pages(
                    f"subjects/{subject_id}/subjects"
                )
                queue.extend(self._extract_results(children_payload))
            except GUSNotFoundError:
                pass

        raise GUSNotFoundError(
            f"No GUS variable found matching query: {query}"
        )