"""FRED resolution: single-tool LLM binding and data fetch."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.workflow.llm_factory import get_llm
from app.workflow.nodes.api_engineer.types import ResolutionResult
from app.workflow.progress import push_progress
from app.workflow.tools import resolve_and_fetch_fred


async def _run_fred_resolution(
    raw_text: str,
    session_id: str,
) -> ResolutionResult:
    """Use the single-tool FRED resolution flow."""
    await push_progress(session_id, {"type": "fred_started"})
    llm = get_llm(temperature=0.0)
    tools = [resolve_and_fetch_fred]
    llm_with_tools = llm.bind_tools(tools)
    system_prompt = (
        "You are the API Engineer Agent. The intent router selected 'FRED'. "
        "Extract exact parameters and call resolve_and_fetch_fred. "
        "If dates are not specified, default to the last 5 years."
    )
    messages: List[AnyMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=raw_text),
    ]
    ai_message: AIMessage = await llm_with_tools.ainvoke(messages)  # type: ignore
    node_messages: List[AnyMessage] = [ai_message]
    node_errors: List[str] = []
    normalized_data: Optional[Dict[str, Any]] = None
    output_dict: Dict[str, Any] = {}

    if not ai_message.tool_calls:
        node_errors.append("API Engineer failed to generate a tool call for FRED.")
        await push_progress(session_id, {"type": "error", "message": node_errors[-1]})
    else:
        tool_call = ai_message.tool_calls[0]
        try:
            output_str = await resolve_and_fetch_fred.ainvoke(tool_call["args"])
            output_dict = json.loads(output_str)
            node_messages.append(ToolMessage(content=output_str, tool_call_id=tool_call["id"]))
            if "error" in output_dict:
                node_errors.append(output_dict["error"])
                await push_progress(session_id, {"type": "error", "message": node_errors[-1]})
            else:
                normalized_data = output_dict
                await push_progress(session_id, {
                    "type": "fred_completed",
                    "series_id": output_dict.get("series_id") or output_dict.get("id"),
                    "observations": len(output_dict.get("data", [])) if isinstance(output_dict.get("data"), list) else None
                })
        except Exception as e:
            node_errors.append(f"FRED tool execution failed: {str(e)}")
            await push_progress(session_id, {"type": "error", "message": node_errors[-1]})

    resolved_context = {
        "source": "FRED",
        "series_id": output_dict.get("series_id") or output_dict.get("id"),
    }
    return normalized_data, node_errors, node_messages, resolved_context