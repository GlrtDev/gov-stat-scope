"""Analyst node execution logic and deterministic math helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.workflow.llm_factory import get_llm
from app.workflow.state import OrchestratorState


class AnalystOutput(BaseModel):
    final_answer: str = Field(
        description="Natural language summary of the data, answering the user's query with source and time range citations."
    )
    key_metrics: List[Dict[str, Any]] = Field(
        description="List of key data points or milestones extracted from the normalized data."
    )
    calculations_performed: List[str] = Field(
        description="List of text descriptions of calculations performed (e.g., 'Calculated year-over-year percentage change')."
    )
    confidence_notes: str = Field(
        description="Any caveats regarding data gaps, anomalies, or assumptions made during analysis."
    )


def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """Calculate percentage change between two values."""
    if old_value == 0.0:
        raise ValueError("Base value cannot be zero when computing percentage change.")
    return ((new_value - old_value) / abs(old_value)) * 100.0


def calculate_average(values: Sequence[float]) -> float:
    """Calculate arithmetic mean of a sequence."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def calculate_min_max(values: Sequence[float]) -> Dict[str, float]:
    """Calculate min, max, and total spread of a sequence."""
    if not values:
        return {"min": 0.0, "max": 0.0, "spread": 0.0}
    min_val = min(values)
    max_val = max(values)
    return {
        "min": min_val,
        "max": max_val,
        "spread": max_val - min_val,
    }


def calculate_cagr(start_value: float, end_value: float, periods: int) -> float:
    """Calculate Compound Annual Growth Rate across given periods."""
    if periods <= 0:
        raise ValueError("Periods must be greater than zero.")
    if start_value <= 0.0 or end_value <= 0.0:
        raise ValueError("Values must be positive to compute CAGR.")
    return ((end_value / start_value) ** (1.0 / periods) - 1.0) * 100.0


async def analyst_agent_node(state: OrchestratorState) -> Dict[str, Any]:
    """Analyze data using deterministic math injected into LLM synthesis."""
    llm = get_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(AnalystOutput)
    
    normalized_data = state.get("normalized_data", {})
    records = normalized_data.get("values", [])
    
    numeric_values = [
        float(record["value"]) for record in records if record.get("value") is not None
    ]
    
    deterministic_metrics = {
        "count": len(numeric_values),
        "average": calculate_average(numeric_values),
        "min_max": calculate_min_max(numeric_values),
    }

    system_prompt = (
        "You are the Data Analyst Agent for the GovStatScope AI Orchestrator. "
        "Your task is to synthesize the provided government data to answer the user's query. "
        "DO NOT perform raw arithmetic; rely strictly on the 'Pre-Calculated Metrics' provided. "
        "Your final answer must be highly readable, cite the specific data source (GUS or FRED), "
        "and mention the exact time range of the data used."
    )
    
    human_payload = (
        f"User Query: {state.get('user_query')}\n\n"
        f"Pre-Calculated Metrics: {deterministic_metrics}\n\n"
        f"Normalized Data: {normalized_data}"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_payload)
    ]
    
    result: AnalystOutput = await structured_llm.ainvoke(messages)  # type: ignore
    
    return {
        "analysis_result": result.model_dump(),
        "final_answer": result.final_answer,
        "messages": [AIMessage(content=result.final_answer)]
    }