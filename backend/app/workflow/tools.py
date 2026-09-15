import json
from typing import Any, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.adapters.fred import FredClient
from app.adapters.gus import GUSClient


class GUSSubjectsArgsSchema(BaseModel):
    parent_id: Optional[str] = Field(
        default=None,
        description="Parent subject ID. Omit or pass null to fetch top-level subjects.",
    )


class GUSVariablesArgsSchema(BaseModel):
    subject_id: str = Field(description="The GUS subject ID to search within.")
    query: str = Field(
        description="Short keyword or metric name to search for (e.g., 'cena pszenicy')."
    )


class GUSDataArgsSchema(BaseModel):
    variable_id: str = Field(description="Resolved GUS variable ID to fetch.")
    variable_name: str = Field(
        default="",
        description="Human-readable metric name, used for normalized series metadata",
    )
    year_start: int = Field(description="Start year (YYYY).")
    year_end: int = Field(description="End year (YYYY).")


class FREDArgsSchema(BaseModel):
    query: str = Field(description="The macroeconomic indicator to search for (e.g., 'GDP', 'CPI').")
    start_date: str = Field(description="Start date (YYYY-MM-DD).")
    end_date: str = Field(description="End date (YYYY-MM-DD).")


@tool(args_schema=GUSSubjectsArgsSchema)
async def fetch_gus_subjects(parent_id: Optional[str] = None) -> str:
    """Fetch GUS subject categories. Use this first to discover subject IDs. Returns a JSON array of {id, name}."""
    client = GUSClient()
    try:
        subjects = await client.fetch_subjects(parent_id=parent_id)
        simplified = []
        for s in subjects:
            subject_id = s.get("id") or s.get("subject-id") or s.get("subjectId") or s.get("code")
            name = s.get("name") or s.get("title", "")
            if subject_id is None:
                continue
            item = {"id": str(subject_id), "name": str(name)}
            has_children = s.get("has-children", s.get("hasChildren", s.get("child-count", 0)))
            if isinstance(has_children, (bool, int)):
                item["hasChildren"] = has_children
            simplified.append(item)
        return json.dumps(simplified, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"GUS API Error: {str(e)}"})
    finally:
        await client.aclose()

def _build_gus_variable_name(v: Dict[str, Any]) -> str:
    """Combine GUS n1..n5 hierarchy fields into a single display name."""
    for field in ("name", "title", "nazwa"):
        value = v.get(field)
        if value:
            return str(value)

    parts = []
    for i in range(1, 6):
        value = v.get(f"n{i}")
        if value:
            parts.append(str(value))

    return " ".join(parts) if parts else ""

@tool(args_schema=GUSVariablesArgsSchema)
async def fetch_gus_variables(subject_id: str, query: str) -> str:
    """Search GUS variables inside a specific subject. Returns JSON array of {id, name, unit}."""
    client = GUSClient()
    try:
        variables = await client.fetch_variables_for_subject(
            subject_id=subject_id,
            search_term=query,
        )
        simplified = []
        for v in variables:
            var_id = v.get("id") or v.get("variable-id") or v.get("variableId")
            name = _build_gus_variable_name(v) or str(var_id)
            unit = v.get("unit") or v.get("unit-name") or v.get("measureUnit", "")
            if var_id is None:
                continue
            item = {"id": str(var_id), "name": name}
            if unit:
                item["unit"] = str(unit)
            simplified.append(item)
        return json.dumps(simplified, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"GUS API Error: {str(e)}"})
    finally:
        await client.aclose()


@tool(args_schema=GUSDataArgsSchema)
async def fetch_gus_data(variable_id: str, variable_name: str, year_start: int, year_end: int) -> str:
    """Fetch GUS time series for a resolved variable ID. This is the final data retrieval step."""
    client = GUSClient()
    try:
        raw_data = await client.fetch_time_range(
            variable_id=variable_id,
            year_start=year_start,
            year_end=year_end,
            unit_id="000000000000",
        )
        normalized = client.normalize_response(raw_data, metric_name=variable_name, region="Polska")
        return normalized.model_dump_json()
    except Exception as e:
        return json.dumps({"error": f"GUS API Error: {str(e)}"})
    finally:
        await client.aclose()


@tool(args_schema=FREDArgsSchema)
async def resolve_and_fetch_fred(query: str, start_date: str, end_date: str) -> str:
    """Resolves a natural language query to a FRED series ID and fetches the time series data for the US."""
    client = FredClient()
    try:
        series_id = await client.resolve_query(query)
        raw_data = await client.fetch_time_range(
            series_id=series_id,
            observation_start=start_date,
            observation_end=end_date,
        )
        normalized = client.normalize_response(raw_data, metric_name=query)
        return normalized.model_dump_json()
    except Exception as e:
        return json.dumps({"error": f"FRED API Error: {str(e)}"})
    finally:
        await client.aclose()