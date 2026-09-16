"""Low-level asynchronous HTTP client for the GUS BDL API."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from app.adapters.gus_errors import (
    GUSAOError,
    GUSInvalidClientIdError,
    GUSNotFoundError,
)
from app.adapters.gus_helpers import (
    TTLCache,
    api_search_text,
    extract_results,
    first_present,
    normalize_text,
    parse_int,
)

DEFAULT_BASE_URL = "https://bdl.stat.gov.pl/api/v1"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 100


class GUSApiClient:
    """Low-level GUS API client: HTTP transport, pagination, and caching."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
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
                total_pages = parse_int(
                    first_present(
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

                total_count = parse_int(
                    first_present(
                        meta,
                        "totalCount",
                        "total_count",
                        "count",
                        default=None,
                    )
                )
                if total_count is not None:
                    return (total_count + page_size - 1) // page_size

        total_pages = parse_int(
            first_present(
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

        total_count = parse_int(
            first_present(
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

            page_results = extract_results(data)
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

    async def fetch_subjects(
        self,
        parent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch top-level or child subjects from GUS API."""
        cache_key = f"subjects:{parent_id or 'root'}"
        cached = self._subject_cache.get(cache_key)
        if cached is not None:
            return cached

        params: Dict[str, Any] = {"page-size": 60}
        if parent_id:
            params["parent-id"] = parent_id

        data = await self._request("GET", "subjects", params=params)
        results = extract_results(data)
        self._subject_cache.set(cache_key, results)
        return results

    async def fetch_variables_for_subject(
        self,
        subject_id: str,
        search_term: Optional[str] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> List[Dict[str, Any]]:
        """Fetch variables for a given subject ID."""
        params: Dict[str, Any] = {
            "subject-id": subject_id,
            "page-size": page_size,
        }
        if search_term:
            params["name"] = api_search_text(search_term)

        cache_key = f"vars:{subject_id}:{normalize_text(search_term or 'all')}"
        cached = self._variable_cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get_all_pages("variables/search", params=params)
        results = extract_results(data)
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
        results = extract_results(data)
        self._subject_cache.set(cache_key, results)
        return results

    async def _get_unit_info(self, unit_id: str) -> Dict[str, Any]:
        """Fetch a single GUS unit by id and cache the response."""
        cache_key = f"unit:{unit_id}"
        cached = self._subject_cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._request("GET", f"units/{unit_id}")
        results = extract_results(data)
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
            level = parse_int(requested_level)
            if level is None:
                raise GUSAOError(
                    f"Invalid GUS unit level: {requested_level!r}"
                )
            return level

        if unit_id:
            unit = await self._get_unit_info(unit_id)
            level = parse_int(
                first_present(
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

    async def aclose(self) -> None:
        await self._client.aclose()