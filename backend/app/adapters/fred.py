import os
from datetime import datetime
from typing import Any, Dict, Optional

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


class FredAOError(Exception):
    """Base exception for FRED Adapter errors."""
    pass


class FredNotFoundError(FredAOError):
    """Raised when a requested resource is not found in the FRED API."""
    pass


class FredClient(DataSourceClient):
    """
    Adapter for the Federal Reserve Economic Data (FRED) API.
    """

    BASE_URL = "https://api.stlouisfed.org/fred/"
    
    # Common metric to FRED series ID mapping
    COMMON_METRICS = {
        "cpi": "CPIAUCSL",
        "unemployment": "UNRATE",
        "gdp": "GDP",
        "interest rates": "FEDFUNDS",
        "inflation": "CPIAUCSL",
        "mortgage rate": "MORTGAGE30US",
    }

    def __init__(self) -> None:
        self.api_key = os.getenv("FRED_API_KEY")
        if not self.api_key:
            raise ValueError("FRED_API_KEY environment variable is required.")
            
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=httpx.Timeout(15.0)
        )

    async def close(self) -> None:
        """Closes the underlying HTTP client."""
        await self.client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True
    )
    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes HTTP request with exponential backoff for transient errors."""
        query_params = params or {}
        query_params.update({
            "api_key": self.api_key,
            "file_type": "json"
        })
        
        response = await self.client.request(method, endpoint, params=query_params)
        
        if response.status_code == 404:
            raise FredNotFoundError(f"Resource not found at {endpoint} with params {params}")
            
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise FredAOError(f"FRED API Error: {e.response.status_code} - {e.response.text}") from e
            
        return response.json()

    async def resolve_query(self, query: str) -> str:
        """
        Resolves natural language queries to FRED series IDs using a hardcoded map
        or falling back to the FRED search API.
        """
        normalized_query = query.lower().strip()
        if normalized_query in self.COMMON_METRICS:
            return self.COMMON_METRICS[normalized_query]

        params = {
            "search_text": query,
            "limit": 1
        }
        response = await self._request("GET", "series/search", params=params)
        
        series_list = response.get("seriess", [])
        if not series_list:
            raise FredNotFoundError(f"No FRED series found matching query: {query}")
            
        return str(series_list[0]["id"])

    async def fetch_series(self, series_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Fetches observations for a specific FRED series."""
        params = {"series_id": series_id}
        params.update(kwargs)
        # We also fetch series metadata to get the actual metric name
        metadata = await self._request("GET", "series", params={"series_id": series_id})
        observations = await self._request("GET", "series/observations", params=params)
        
        return {
            "metadata": metadata.get("seriess", [{}])[0],
            "observations": observations.get("observations", [])
        }

    async def fetch_comparison(self, series_id_1: str, series_id_2: str) -> Dict[str, Any]:
        """Fetches and returns raw comparison data for two series."""
        series_1 = await self.fetch_series(series_id_1)
        series_2 = await self.fetch_series(series_id_2)
        return {
            series_id_1: series_1,
            series_id_2: series_2
        }

    async def fetch_time_range(self, series_id: str, observation_start: str, observation_end: str) -> Dict[str, Any]:
        """Fetches data for a specific series over a bounded time range (YYYY-MM-DD)."""
        return await self.fetch_series(
            series_id, 
            observation_start=observation_start, 
            observation_end=observation_end
        )

    async def fetch_data(self, resource_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Implements DataSourceClient.fetch_data."""
        return await self.fetch_series(resource_id, **kwargs)

    def normalize_response(self, raw_data: Dict[str, Any], **kwargs: Any) -> NormalizedSeries:
        """Transforms FRED JSON observation structure into NormalizedSeries schema."""
        observations = raw_data.get("observations", [])
        if not observations:
            raise FredNotFoundError("The response contains no observations to normalize.")

        metadata = raw_data.get("metadata", {})
        metric_name = metadata.get("title", kwargs.get("metric_name", "Unknown FRED Series"))
        region_name = "United States" # Default for FRED unless otherwise specified
        
        data_points = []
        for obs in observations:
            val_str = obs.get("value", "")
            # FRED returns "." for missing data points
            if val_str == ".":
                continue
            
            try:
                date_val = obs.get("date")
                value = float(val_str)
                data_points.append(DataPoint(date=date_val, value=value))
            except (ValueError, TypeError):
                continue
                
        data_points.sort(key=lambda dp: str(dp.date))
        
        time_period = "Unknown"
        if data_points:
            time_period = f"{data_points[0].date} to {data_points[-1].date}"

        return NormalizedSeries(
            source=DataSource.FRED,
            metric_name=metric_name,
            region=region_name,
            time_period=time_period,
            values=data_points
        )