"""GUS adapter exposing ``GUSClient`` as a ``DataSourceClient`` implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from app.adapters.base import DataSourceClient
from app.adapters.gus_api import DEFAULT_TIMEOUT, GUSApiClient
from app.adapters.gus_errors import (
    GUSAOError,
    GUSInvalidClientIdError,
    GUSNotFoundError,
)
from app.adapters.gus_helpers import (
    DEFAULT_YEAR,
    api_search_text,
    extract_results,
    first_present,
    normalize_text,
    normalize_years,
    parse_float,
    parse_int,
    score_unit,
    score_variable,
    select_result,
)
from app.adapters.schemas import DataPoint, NormalizedSeries
from app.models import DataSource

__all__ = [
    "GUSClient",
    "GUSAOError",
    "GUSNotFoundError",
    "GUSInvalidClientIdError",
]


class GUSClient(GUSApiClient, DataSourceClient):
    """Asynchronous GUS API client exposing a DataSourceClient-compatible API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        GUSApiClient.__init__(
            self,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )

    async def resolve_query(self, query: str) -> str:
        """Legacy compatibility method. Resolves a query to a GUS variable id."""
        return await self._resolve_variable_id(query)

    async def _resolve_variable_id(self, query: str) -> str:
        cache_key = f"varid:{normalize_text(query)}"
        cached = self._variable_id_cache.get(cache_key)
        if cached is not None:
            return str(cached)

        try:
            data = await self._get_all_pages(
                "variables",
                params={"search": api_search_text(query)},
            )
            variables = extract_results(data)
            if not variables:
                raise GUSNotFoundError(
                    f"GUS variable not found for query '{query}'"
                )

            best_variable = max(
                variables,
                key=lambda v: score_variable(v, query),
                default=None,
            )
            if best_variable is None:
                raise GUSNotFoundError(
                    f"GUS variable not found for query '{query}'"
                )

            variable_id = str(
                first_present(
                    best_variable,
                    "id",
                    "variable-id",
                    "variableId",
                    default="",
                )
            )
            if not variable_id:
                raise GUSNotFoundError(
                    f"GUS variable ID missing for query '{query}'"
                )

            self._variable_id_cache.set(cache_key, variable_id)
            return variable_id
        except GUSAOError as exc:
            raise GUSNotFoundError(
                f"GUS variable not found for query '{query}': {exc}"
            ) from exc

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
                first_present(unit, "name", "name-en", default="")
            )
            score = score_unit(name, unit_name)
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
            unit_name = normalize_text(
                str(first_present(unit, "name", "name-en", default=""))
            )
            if "polska" in unit_name:
                return unit

        return units[0]

    async def fetch_data(
        self,
        variable_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        variable_id = str(variable_id)
        years = normalize_years(kwargs.get("years"))

        year_start = kwargs.get("year_start")
        year_end = kwargs.get("year_end")

        if year_start is not None and year_end is not None:
            years = list(
                range(
                    parse_int(year_start) or DEFAULT_YEAR,
                    (parse_int(year_end) or DEFAULT_YEAR) + 1,
                )
            )
        elif year_start is not None:
            years = [parse_int(year_start) or DEFAULT_YEAR]
        elif year_end is not None:
            years = [DEFAULT_YEAR, parse_int(year_end) or DEFAULT_YEAR]

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
                    first_present(unit, "id", "unit-id", default="")
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
            results.extend(extract_results(data))

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
        unit = await self.resolve_unit_for_level(unit_level)
        unit_id = str(
            first_present(unit, "id", "unit-id", default="0")
        )
        return await self.fetch_data(
            variable_id=variable_id,
            unit_id=unit_id,
            years=years,
        )

    async def fetch_comparison(
        self,
        variable_id: str,
        unit_parent_id: str,
    ) -> Dict[str, Any]:
        params = {"parent-id": unit_parent_id}
        data = await self._get_all_pages("units", params=params)
        results = extract_results(data)
        return {
            "results": results,
            "variable-id": variable_id,
            "parent-id": unit_parent_id,
        }

    def normalize_response(
        self,
        raw_data: Dict[str, Any],
        **kwargs: Any,
    ) -> NormalizedSeries:
        results = extract_results(raw_data)
        if not results:
            raise GUSNotFoundError("No data returned from GUS API")

        selected = select_result(results, kwargs)

        values_raw = first_present(
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
                date = first_present(
                    item,
                    "date",
                    "year",
                    "period",
                    default=None,
                )
                value = first_present(
                    item,
                    "value",
                    "val",
                    "wartość",
                    "wartosc",
                    default=None,
                )
            else:
                date = first_present(
                    selected,
                    "year",
                    "rok",
                    "date",
                    default=None,
                )
                value = item

            parsed_value = parse_float(value)
            if parsed_value is None:
                continue

            if date is None:
                date = first_present(
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
            first_present(
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
            or first_present(
                selected,
                "unit-name",
                "unitName",
                "region",
                "name",
                default="",
            )
        )

        dates = [dp["date"] for dp in data_points]
        data_points_list = [
            DataPoint(date=dp["date"], value=dp["value"])
            for dp in data_points
        ]

        if len(dates) > 1 and dates[0] != dates[-1]:
            time_period = f"{dates[0]} to {dates[-1]}"
        else:
            time_period = dates[0] if dates else ""

        metric_name = str(
            kwargs.get("metric_name")
            or first_present(
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