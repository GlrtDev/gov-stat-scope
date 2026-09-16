"""
GUS (Główny Urząd Statystyczny) API adapter.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

from app.adapters.base import DataSourceClient
from app.adapters.schemas import DataPoint, NormalizedSeries
from app.models import DataSource


DEFAULT_BASE_URL = "https://bdl.stat.gov.pl/api/v1"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 100
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

    def __init__(self, ttl_seconds: float = 3600):
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


def _normalize_text(value: str) -> str:
    """Normalize text for searching/matching."""
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def _api_search_text(query: str) -> str:
    """Prepare query for the GUS API, preserving Polish diacritics."""
    q = query.strip().lower()
    q = GUS_TERM_ALIASES.get(q, q)
    # Restore a few common Polish diacritics if typed without them.
    q = q.replace("ludnosc", "ludność")
    q = q.replace("pszenzyto", "pszenżyto")
    q = q.replace("zboze", "zboże")
    q = q.replace("zywienie", "żywienie")
    return " ".join(q.split())


def _parse_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(data: dict, *keys: str, default: Any = None) -> Any:
    """Return the first key found in a dict."""
    if not isinstance(data, dict):
        return default
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _extract_results(payload: Any) -> List[Dict[str, Any]]:
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


def _subject_id(subject: Dict[str, Any]) -> str:
    return str(
        _first_present(
            subject,
            "id",
            "subject-id",
            "subjectId",
            "code",
            default="",
        )
    )


class GUSClient(DataSourceClient):
    """Asynchronous GUS API client."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = (
            base_url or os.getenv("GUS_API_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = api_key or os.getenv("GUS_API_KEY")

        headers = {
            "User-Agent": "GovScope-GUS-Adapter/1.0",
            "Accept": "application/json",
        }

        if self.api_key:
            headers["api_key"] = self.api_key

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=headers,
        )

        self._subject_cache = TTLCache(ttl_seconds=3600)
        self._variable_cache = TTLCache(ttl_seconds=3600)
        self._variable_id_cache = TTLCache(ttl_seconds=3600)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)

        if response.status_code == 401:
            raise GUSInvalidClientIdError(
                "GUS API rejected the client id/api key."
            )

        if response.status_code == 404:
            raise GUSNotFoundError(
                f"GUS endpoint not found: {method} {path} ({response.status_code})"
            )

        if response.status_code >= 400:
            raise GUSAOError(
                f"GUS API error {response.status_code}: {response.text[:200]}"
            )

        if response.status_code == 204 or not response.content:
            return {}

        return response.json()

    async def _get_page(
        self,
        endpoint: str,
        params: Dict[str, Any],
    ) -> Any:
        request_params = dict(params)
        request_params.setdefault("page", 0)
        request_params.setdefault("page-size", DEFAULT_PAGE_SIZE)
        return await self._request("GET", endpoint, params=request_params)

    def _extract_total_pages(
        self,
        data: Any,
        page_size: int,
        page_results_len: int,
        current_page: int,
    ) -> Optional[int]:
        if isinstance(data, dict):
            meta = (
                data.get("meta")
                or data.get("pagination")
                or data.get("pageInfo")
                or data.get("info")
            )
            if isinstance(meta, dict):
                total_pages = _parse_int(
                    _first_present(
                        meta,
                        "totalPages",
                        "total_pages",
                        "pageCount",
                        "page_count",
                        default=None,
                    )
                )
                if total_pages is not None:
                    return total_pages

                total_count = _parse_int(
                    _first_present(
                        meta,
                        "totalCount",
                        "total_count",
                        "count",
                        default=None,
                    )
                )
                if total_count is not None:
                    return (total_count + page_size - 1) // page_size

            total_pages = _parse_int(
                _first_present(
                    data,
                    "totalPages",
                    "total_pages",
                    "pageCount",
                    "page_count",
                    default=None,
                )
            )
            if total_pages is not None:
                return total_pages

            total_count = _parse_int(
                _first_present(
                    data,
                    "totalCount",
                    "total_count",
                    "count",
                    default=None,
                )
            )
            if total_count is not None:
                return (total_count + page_size - 1) // page_size

        if page_results_len < page_size:
            return current_page + 1

        return None

    async def _get_all_pages(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base_params = dict(params or {})
        all_results: List[Dict[str, Any]] = []

        page = 0
        while True:
            page_params = dict(base_params)
            page_params["page"] = page
            page_params.setdefault("page-size", DEFAULT_PAGE_SIZE)

            data = await self._get_page(endpoint, page_params)

            if isinstance(data, list):
                all_results.extend(item for item in data if isinstance(item, dict))
                break

            page_results = _extract_results(data)
            all_results.extend(page_results)

            total_pages = self._extract_total_pages(
                data=data,
                page_size=int(page_params.get("page-size", DEFAULT_PAGE_SIZE)),
                page_results_len=len(page_results),
                current_page=page,
            )

            if total_pages is not None and page + 1 >= total_pages:
                break

            if total_pages is None and len(page_results) < int(
                page_params.get("page-size", DEFAULT_PAGE_SIZE)
            ):
                break

            page += 1

        return {
            "results": all_results,
            "page": page,
            "pageSize": DEFAULT_PAGE_SIZE,
        }

    async def fetch_subjects(self, parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch top-level or child subjects from GUS API. Public method for LLM-guided resolution."""
        cache_key = f"subjects:{parent_id or 'root'}"
        cached = self._subject_cache.get(cache_key)
        if cached is not None:
            return cached

        params: Dict[str, Any] = {"page-size": 60}  # Fetch all children in one request
        if parent_id:
            params["parent-id"] = parent_id

        data = await self._request("GET", "subjects", params=params)
        results = _extract_results(data)
        self._subject_cache.set(cache_key, results)
        return results

    async def fetch_variables_for_subject(
        self,
        subject_id: str,
        search_term: Optional[str] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> List[Dict[str, Any]]:
        """Fetch variables for a given subject ID. Public method for LLM-guided resolution."""
        params: Dict[str, Any] = {
            "subject-id": subject_id,
            "page-size": page_size,
        }
        if search_term:
            params["name"] = _api_search_text(search_term)

        cache_key = f"vars:{subject_id}:{_normalize_text(search_term or 'all')}"
        cached = self._variable_cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get_all_pages("variables/search", params=params)
        results = _extract_results(data)
        self._variable_cache.set(cache_key, results)
        return results

    async def _fetch_units(
        self,
        level: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if level is not None:
            params["level"] = level

        cache_key = f"units:{level if level is not None else 'all'}"
        cached = self._subject_cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get_all_pages("units", params=params)
        results = _extract_results(data)
        self._subject_cache.set(cache_key, results)
        return results

    async def resolve_query(self, query: str) -> str:
        """Legacy compatibility method. Redirects to local resolution via cached variable ID."""
        return await self._resolve_variable_id(query)

    async def _resolve_variable_id(self, query: str) -> str:
        cache_key = f"varid:{_normalize_text(query)}"
        cached = self._variable_id_cache.get(cache_key)
        if cached is not None:
            return str(cached)

        # Simple fallback: search across all subjects using repository endpoint
        try:
            data = await self._get_all_pages("variables", params={"search": _api_search_text(query)})
            variables = _extract_results(data)
            if not variables:
                raise GUSNotFoundError(f"GUS variable not found for query '{query}'")

            best_variable = max(
                variables,
                key=lambda v: self._score_variable(v, query),
                default=None,
            )
            if best_variable is None:
                raise GUSNotFoundError(f"GUS variable not found for query '{query}'")

            variable_id = str(_first_present(best_variable, "id", "variable-id", "variableId", default=""))
            if not variable_id:
                raise GUSNotFoundError(f"GUS variable ID missing for query '{query}'")

            self._variable_id_cache.set(cache_key, variable_id)
            return variable_id
        except GUSAOError as e:
            raise GUSNotFoundError(f"GUS variable not found for query '{query}': {str(e)}")

    def _score_variable(
        self,
        variable: Dict[str, Any],
        query: str,
    ) -> int:
        name = str(
            _first_present(
                variable,
                "name",
                "title",
                "description",
                default="",
            )
        )
        norm_name = _normalize_text(name)
        query_norm = _normalize_text(query)

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

    def _unit_score(self, query: str, unit_name: str) -> int:
        query_norm = _normalize_text(query)
        unit_norm = _normalize_text(unit_name)

        if not query_norm or not unit_norm:
            return 0

        if query_norm == unit_norm:
            return 1000

        if query_norm in unit_norm or unit_norm in query_norm:
            return 500

        return 0

    async def _find_unit_in_level(
        self,
        name: str,
        level: int,
    ) -> Optional[Dict[str, Any]]:
        units = await self._fetch_units(level=level)

        best_unit = None
        best_score = 0

        for unit in units:
            unit_name = str(
                _first_present(unit, "name", "name-en", default="")
            )
            score = self._unit_score(name, unit_name)
            if score > best_score:
                best_score = score
                best_unit = unit

        return best_unit

    async def resolve_unit(self, name: str) -> Dict[str, Any]:
        if name:
            for level in (0, 1, 2, 3):
                found = await self._find_unit_in_level(name, level)
                if found:
                    return found

        return await self.resolve_unit_for_level(0)

    async def resolve_unit_for_level(self, level: int) -> Dict[str, Any]:
        units = await self._fetch_units(level=level)

        if not units:
            raise GUSNotFoundError(f"No GUS units found at level {level}")

        for unit in units:
            unit_name = _normalize_text(
                str(_first_present(unit, "name", "name-en", default=""))
            )
            if "polska" in unit_name:
                return unit

        return units[0]

    def _normalize_years(self, years: Any) -> List[int]:
        if years is None:
            return [DEFAULT_YEAR]

        if isinstance(years, int):
            return [years]

        if isinstance(years, str):
            years = years.split(",")

        result: List[int] = []
        for year in years:
            parsed = _parse_int(year)
            if parsed is not None:
                result.append(parsed)

        if not result:
            result = [DEFAULT_YEAR]

        return result

    async def _get_unit_info(self, unit_id: str) -> Dict[str, Any]:
        """Fetch a single GUS unit by id and cache the response."""
        cache_key = f"unit:{unit_id}"
        cached = self._subject_cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._request("GET", f"units/{unit_id}")
        results = _extract_results(data)
        if not results:
            raise GUSNotFoundError(f"GUS unit not found for id '{unit_id}'")

        unit = results[0]
        self._subject_cache.set(cache_key, unit)
        return unit

    async def _resolve_unit_level(
        self,
        unit_id: Optional[str],
        requested_level: Any,
    ) -> int:
        """Resolve the numeric GUS unit level used by data/by-variable."""
        if requested_level is not None:
            level = _parse_int(requested_level)
            if level is None:
                raise GUSAOError(
                    f"Invalid GUS unit level: {requested_level!r}"
                )
            return level

        if unit_id:
            unit = await self._get_unit_info(unit_id)
            level = _parse_int(
                _first_present(
                    unit,
                    "level",
                    "poziom",
                    default=None,
                )
            )
            if level is None:
                raise GUSAOError(
                    f"Could not determine unit level for unit '{unit_id}'"
                )
            return level

        return 0

    async def fetch_data(
        self,
        variable_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        variable_id = str(variable_id)
        years = self._normalize_years(kwargs.get("years"))
        year_start = kwargs.get("year_start")
        year_end = kwargs.get("year_end")

        if year_start is not None and year_end is not None:
            years = list(
                range(
                    _parse_int(year_start) or DEFAULT_YEAR,
                    (_parse_int(year_end) or DEFAULT_YEAR) + 1,
                )
            )
        elif year_start is not None:
            years = [_parse_int(year_start) or DEFAULT_YEAR]
        elif year_end is not None:
            years = [DEFAULT_YEAR, _parse_int(year_end) or DEFAULT_YEAR]

        unit_id_raw = (
            kwargs.get("unit_id")
            or kwargs.get("unitId")
            or kwargs.get("unit-id")
        )
        unit_id = str(unit_id_raw) if unit_id_raw is not None else None
        requested_level = kwargs.get("unit_level") or kwargs.get("unit-level")

        unit_level = await self._resolve_unit_level(
            unit_id=unit_id,
            requested_level=requested_level,
        )

        if unit_id is None and kwargs.get("region") is None:
            try:
                unit = await self.resolve_unit_for_level(unit_level)
                unit_id = str(
                    _first_present(unit, "id", "unit-id", default="")
                )
            except GUSNotFoundError:
                unit_id = None

        results: List[Dict[str, Any]] = []

        for year in years:
            params = {
                "unit-level": unit_level,
                "year": year,
            }
            data = await self._get_all_pages(
                f"data/by-variable/{variable_id}",
                params=params,
            )
            results.extend(_extract_results(data))

        return {
            "results": results,
            "variable-id": variable_id,
            "unit-id": unit_id,
            "unit-level": unit_level,
            "years": years,
        }

    async def fetch_time_range(
        self,
        variable_id: str,
        year_start: int,
        year_end: int,
        unit_id: Optional[str] = None,
        unit_level: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Fetch data for a specific year range."""
        kwargs: Dict[str, Any] = {
            "variable_id": variable_id,
            "year_start": year_start,
            "year_end": year_end,
        }
        if unit_id:
            kwargs["unit_id"] = unit_id
        if unit_level is not None:
            kwargs["unit_level"] = unit_level
        return await self.fetch_data(**kwargs)

    async def fetch_series(
        self,
        variable_id: str,
        unit_level: int = 0,
        years: Optional[Union[int, List[int]]] = None,
    ) -> Dict[str, Any]:
        return await self.fetch_data(
            variable_id=variable_id,
            unit_level=unit_level,
            years=years,
        )

    async def fetch_series(
        self,
        variable_id: str,
        unit_level: int = 0,
        years: Optional[Union[int, List[int]]] = None,
    ) -> Dict[str, Any]:
        unit = await self.resolve_unit_for_level(unit_level)
        unit_id = _first_present(unit, "id", "unit-id", default="0")

        return await self.fetch_data(
            variable_id=variable_id,
            unit_id=str(unit_id),
            years=years,
        )

    async def fetch_comparison(
        self,
        variable_id: str,
        unit_parent_id: str,
    ) -> Dict[str, Any]:
        params = {"parent-id": unit_parent_id}
        data = await self._get_all_pages("units", params=params)
        results = _extract_results(data)

        return {
            "results": results,
            "variable-id": variable_id,
            "parent-id": unit_parent_id,
        }

    async def list_regions(
        self,
        level: int = 0,
        page: int = 0,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Dict[str, Any]:
        params = {
            "level": level,
            "page": page,
            "page-size": page_size,
        }
        return await self._request("GET", "units", params=params)

    async def list_variables(
        self,
        subject_id: str,
        page: int = 0,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Dict[str, Any]:
        params = {
            "subject-id": subject_id,
            "page": page,
            "page-size": page_size,
        }
        return await self._request("GET", "variables", params=params)

    def _select_result(
        self,
        results: List[Dict[str, Any]],
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not results:
            return {}

        unit_id = kwargs.get("unit_id") or kwargs.get("unitId")
        region = kwargs.get("region")

        for result in results:
            result_unit_id = str(
                _first_present(
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
                unit_name = str(
                    _first_present(
                        result,
                        "unit-name",
                        "unitName",
                        "name",
                        default="",
                    )
                )
                region_norm = _normalize_text(str(region))
                unit_norm = _normalize_text(unit_name)
                if region_norm and (
                    region_norm in unit_norm or unit_norm in region_norm
                ):
                    return result

        return results[0]

    def normalize_response(
        self,
        raw_data: Dict[str, Any],
        **kwargs: Any,
    ) -> NormalizedSeries:
        results = _extract_results(raw_data)

        if not results:
            raise GUSNotFoundError("No data returned from GUS API")

        selected = self._select_result(results, kwargs)

        values_raw = _first_present(
            selected,
            "values",
            "data",
            "series",
            "points",
            default=[],
        )

        if not values_raw:
            values_raw = [selected]

        data_points: List[Dict[str, Any]] = []

        for item in values_raw:
            if isinstance(item, dict):
                date = _first_present(
                    item,
                    "date",
                    "year",
                    "period",
                    default=None,
                )
                value = _first_present(
                    item,
                    "value",
                    "val",
                    "wartość",
                    "wartosc",
                    default=None,
                )
            else:
                date = _first_present(
                    selected,
                    "year",
                    "rok",
                    "date",
                    default=None,
                )
                value = item

            parsed_value = _parse_float(value)
            if parsed_value is None:
                continue

            if date is None:
                date = _first_present(
                    selected,
                    "year",
                    "rok",
                    "date",
                    default="",
                )

            data_points.append(
                {
                    "date": str(date),
                    "value": parsed_value,
                }
            )

        if not data_points:
            raise GUSNotFoundError("No numeric values in GUS data response")

        unit = str(
            _first_present(
                selected,
                "unit",
                "jednostka",
                "unit-name",
                "unitName",
                default="",
            )
        )

        region = str(
            kwargs.get("region")
            or _first_present(
                selected,
                "unit-name",
                "unitName",
                "region",
                "name",
                default="",
            )
        )

        dates = [dp["date"] for dp in data_points]
        data_points_list = [DataPoint(date=dp["date"], value=dp["value"]) for dp in data_points]
        if len(dates) > 1 and dates[0] != dates[-1]:
            time_period = f"{dates[0]} to {dates[-1]}"
        else:
            time_period = dates[0] if dates else ""

        metric_name = str(
            kwargs.get("metric_name")
            or _first_present(
                selected,
                "name",
                "variable-name",
                "variableName",
                default="",
            )
        )

        return NormalizedSeries(
            source=DataSource.GUS,
            metric_name=metric_name,
            region=region,
            time_period=time_period,
            values=data_points_list,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class GUSAOError(Exception):
    """Base GUS adapter error."""


class GUSNotFoundError(GUSAOError):
    """Raised when a GUS resource is not found."""


class GUSInvalidClientIdError(GUSAOError):
    """Raised when the GUS API client id is invalid."""