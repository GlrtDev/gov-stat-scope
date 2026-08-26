import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.app.adapters.base import DataSourceClient
from backend.app.adapters.schemas import DataPoint, NormalizedSeries
from backend.models import DataSource


class GUSAOError(Exception):
    """Base exception for GUS Adapter errors."""
    pass


class GUSNotFoundError(GUSAOError):
    """Raised when a requested resource is not found in the GUS API."""
    pass


class GUSClient(DataSourceClient):
    """
    Adapter for the Polish Central Statistical Office (GUS) Local Data Bank (BDL) API.
    Supports optional API key authentication via X-ClientId header, falling back to unauthenticated rate limits.
    """

    BASE_URL = "https://bdl.stat.gov.pl/api/v1/"

    def __init__(self) -> None:
        api_key = os.getenv("GUS_API_KEY") or os.getenv("GUS_CLIENT_ID")
        headers: Dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["X-ClientId"] = api_key

        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=httpx.Timeout(15.0),
        )

    async def close(self) -> None:
        """Closes the underlying HTTP client."""
        await self.client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes HTTP request with exponential backoff for transient network errors and rate limits."""
        response = await self.client.request(method, endpoint, params=params)

        if response.status_code == 404:
            raise GUSNotFoundError(f"Resource not found at {endpoint} with params {params}")

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise GUSAOError(f"GUS API Error: {e.response.status_code} - {e.response.text}") from e

        return response.json()

    # --- Metadata Discovery Methods ---

    async def list_regions(self, level: int = 0, page: int = 0, page_size: int = 100) -> Dict[str, Any]:
        """Lists geographical units at a specific administrative level."""
        params = {"level": level, "page": page, "page-size": page_size}
        return await self._request("GET", "units", params=params)

    async def list_variables(self, subject_id: str, page: int = 0, page_size: int = 100) -> Dict[str, Any]:
        """Lists variables belonging to a specific subject category."""
        params = {"subject-id": subject_id, "page": page, "page-size": page_size}
        return await self._request("GET", "variables", params=params)

    async def resolve_variable_id(self, name: str) -> str:
        """Searches for a variable by name and returns the best matching ID."""
        params = {"name-en": name, "page-size": 1}
        response = await self._request("GET", "variables/search", params=params)

        results = response.get("results", [])
        if not results:
            params_pl = {"name": name, "page-size": 1}
            response_pl = await self._request("GET", "variables/search", params=params_pl)
            results = response_pl.get("results", [])

        if not results:
            raise GUSNotFoundError(f"No variable found matching name/query: {name}")

        return str(results[0]["id"])

    # --- Interface Implementation Methods ---

    async def resolve_query(self, query: str) -> str:
        """Implements DataSourceClient.resolve_query."""
        return await self.resolve_variable_id(query)

    async def fetch_series(self, variable_id: str, unit_level: int = 0, year: Optional[List[int]] = None) -> Dict[str, Any]:
        """Fetches a single data series for a given variable."""
        params: Dict[str, Any] = {"unit-level": unit_level, "page-size": 100}
        if year:
            params["year"] = year
        return await self._request("GET", f"data/by-variable/{variable_id}", params=params)

    async def fetch_comparison(self, variable_id: str, unit_parent_id: str) -> Dict[str, Any]:
        """Fetches comparative data across multiple sub-units for a specific parent unit."""
        params: Dict[str, Any] = {"unit-parent-id": unit_parent_id, "page-size": 100}
        return await self._request("GET", f"data/by-variable/{variable_id}", params=params)

    async def fetch_time_range(self, variable_id: str, unit_id: str, year_start: int, year_end: int) -> Dict[str, Any]:
        """Fetches data for a specific unit over a bounded time range."""
        params: Dict[str, Any] = {"page-size": 100}
        params["year"] = list(range(year_start, year_end + 1))
        return await self._request("GET", f"data/by-unit/{unit_id}", params=params)

    async def fetch_data(self, resource_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Implements DataSourceClient.fetch_data, defaulting to fetch_series."""
        unit_level = kwargs.get("unit_level", 0)
        years = kwargs.get("years")
        return await self.fetch_series(variable_id=resource_id, unit_level=unit_level, year=years)

    def normalize_response(self, raw_data: Dict[str, Any], **kwargs: Any) -> NormalizedSeries:
        """Transforms GUS BDL 'data/by-variable' JSON structure into NormalizedSeries."""
        results = raw_data.get("results", [])
        if not results:
            raise GUSNotFoundError("The response contains no results to normalize.")

        first_result = results[0]
        region_name = first_result.get("name", "Poland")
        metric_name = kwargs.get("metric_name", f"GUS Variable {kwargs.get('resource_id', 'Unknown')}")

        values_raw = first_result.get("values", [])

        data_points: List[DataPoint] = []
        for val_obj in values_raw:
            year_str = str(val_obj.get("year"))
            try:
                date_val = datetime(int(year_str), 1, 1).date().isoformat()
            except (ValueError, TypeError):
                date_val = year_str

            value = float(val_obj.get("val", 0.0))
            data_points.append(DataPoint(date=date_val, value=value))

        data_points.sort(key=lambda dp: str(dp.date))

        time_period = "Unknown"
        if data_points:
            start = data_points[0].date
            end = data_points[-1].date
            time_period = f"{start} to {end}"

        return NormalizedSeries(
            source=DataSource.GUS,
            metric_name=metric_name,
            region=region_name,
            time_period=time_period,
            values=data_points,
        )