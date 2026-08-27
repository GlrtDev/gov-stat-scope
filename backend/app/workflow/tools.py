import json
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.adapters.fred import FredAOError, FredClient
from app.adapters.gus import GUSAOError, GUSClient


class GUSArgsSchema(BaseModel):
    query: str = Field(description="The natural language metric name to search for (e.g., 'population', 'unemployment').")
    year_start: int = Field(description="The start year for the data range (YYYY format).")
    year_end: int = Field(description="The end year for the data range (YYYY format).")


class FREDArgsSchema(BaseModel):
    query: str = Field(description="The macroeconomic indicator to search for (e.g., 'GDP', 'CPI').")
    start_date: str = Field(description="The start date for the data range (YYYY-MM-DD format).")
    end_date: str = Field(description="The end date for the data range (YYYY-MM-DD format).")


@tool(args_schema=GUSArgsSchema)
async def resolve_and_fetch_gus(query: str, year_start: int, year_end: int) -> str:
    """Resolves a natural language query to a GUS variable ID and fetches the time series data for Poland."""
    client = GUSClient()
    try:
        var_id = await client.resolve_query(query)
        # Using 000000000000 for national-level data fallback
        raw_data = await client.fetch_time_range(
            variable_id=var_id, unit_id="000000000000", year_start=year_start, year_end=year_end
        )
        normalized = client.normalize_response(raw_data, metric_name=query)
        return normalized.model_dump_json()
    except Exception as e:
        return json.dumps({"error": f"GUS API Error: {str(e)}"})
    finally:
        await client.close()


@tool(args_schema=FREDArgsSchema)
async def resolve_and_fetch_fred(query: str, start_date: str, end_date: str) -> str:
    """Resolves a natural language query to a FRED series ID and fetches the time series data for the US."""
    client = FredClient()
    try:
        series_id = await client.resolve_query(query)
        raw_data = await client.fetch_time_range(
            series_id=series_id, observation_start=start_date, observation_end=end_date
        )
        normalized = client.normalize_response(raw_data, metric_name=query)
        return normalized.model_dump_json()
    except Exception as e:
        return json.dumps({"error": f"FRED API Error: {str(e)}"})
    finally:
        await client.close()