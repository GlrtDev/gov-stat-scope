"""API Engineer graph node and routing."""
from __future__ import annotations

from typing import Any, Dict

from langchain_core.messages import ToolMessage

from app.workflow.nodes.api_engineer.dates import _extract_year_range, _is_temporal_followup
from app.workflow.nodes.api_engineer.fred_resolver import _run_fred_resolution
from app.workflow.nodes.api_engineer.gus_resolver import _run_gus_resolution_agent
from app.workflow.nodes.api_engineer.parsing import _extract_query_string, _parse_json_payload
from app.workflow.progress import push_progress
from app.workflow.state import OrchestratorState
from app.workflow.tools import fetch_gus_data


async def api_engineer_agent_node(state: OrchestratorState) -> Dict[str, Any]:
    """Bind adapter tools, extract parameters via LLM, and execute data retrieval."""
    source = state.get("selected_source", "UNKNOWN")
    raw_text = _extract_query_string(state.get("user_query", ""))
    session_id = state.get("session_id", "")
    previous_context = state.get("previous_context") or {}

    if not raw_text:
        await push_progress(session_id, {"type": "error", "message": "API Engineer received an empty query."})
        return {"errors": ["API Engineer received an empty query."], "context": None}

    # Temporal follow-up: reuse saved GUS variable if query mentions a new year
    if source == "GUS" and _is_temporal_followup(raw_text, previous_context):
        year_start, year_end = _extract_year_range(raw_text)
        try:
            data_output = await fetch_gus_data.ainvoke({
                "variable_id": previous_context["variable_id"],
                "variable_name": previous_context["variable_name"],
                "year_start": year_start,
                "year_end": year_end,
            })
            parsed = _parse_json_payload(data_output)
            if parsed and isinstance(parsed, dict) and "error" not in parsed:
                await push_progress(session_id, {
                    "type": "context_reused",
                    "variable_id": previous_context["variable_id"],
                    "years": [year_start, year_end],
                })
                return {
                    "messages": [ToolMessage(content=data_output, tool_call_id="gus-data-reused")],
                    "normalized_data": parsed,
                    "errors": [],
                    "context": previous_context,
                }
        except Exception:
            pass  # fall back to full resolution

    if source == "FRED":
        normalized_data, node_errors, node_messages, resolved_context = await _run_fred_resolution(raw_text, session_id)
    else:
        normalized_data, node_errors, node_messages, resolved_context = await _run_gus_resolution_agent(raw_text, session_id)

    return {
        "messages": node_messages,
        "normalized_data": normalized_data,
        "errors": node_errors,
        "context": resolved_context,
    }


def route_after_api(state: OrchestratorState) -> str:
    """Determine next graph transition based on data presence and error state."""
    if state.get("normalized_data") and not state.get("errors"):
        return "analyst"
    return "error_handler"