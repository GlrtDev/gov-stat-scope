"""API Engineer node executing native tool binding and parameter resolution."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.workflow.llm_factory import get_llm
from app.workflow.state import OrchestratorState
from app.workflow.tools import (
    fetch_gus_data,
    fetch_gus_subjects,
    fetch_gus_variables,
    resolve_and_fetch_fred,
)


def _extract_query_string(user_query: Any) -> str:
    """Safely extract the raw query string from dicts, objects, or primitive strings."""
    if isinstance(user_query, str):
        return user_query
    if isinstance(user_query, dict):
        return str(user_query.get("raw_text") or user_query.get("query") or "")
    return str(getattr(user_query, "raw_text", user_query))


async def _run_gus_resolution_agent(
    raw_text: str,
) -> Tuple[Optional[Dict[str, Any]], List[str], List[AnyMessage]]:
    """Run the step-by-step LLM-guided GUS subject traversal."""
    llm = get_llm(temperature=0.0)
    tools = [fetch_gus_subjects, fetch_gus_variables, fetch_gus_data]
    llm_with_tools = llm.bind_tools(tools)
    tool_map = {tool.name: tool for tool in tools}

    system_prompt = (
        "You are the API Engineer Agent for GUS (Polish Central Statistical Office). "
        "Resolve the user's request by traversing the GUS subject hierarchy step by step:\n"
        "1. Call fetch_gus_subjects with no parent_id to list top-level subjects.\n"
        "2. Choose the subject most relevant to the user's query and call fetch_gus_subjects with its id to drill down.\n"
        "3. Repeat drilling until you reach a subject that is specific enough (usually 2-3 levels deep).\n"
        "4. Call fetch_gus_variables with the chosen subject_id and a short keyword from the query.\n"
        "5. Pick the most relevant variable and call fetch_gus_data to retrieve the series.\n"
        "If years are not explicitly provided, use the last 5 full years (e.g., 2019-2023).\n"
        "Do not stop until data has been fetched via fetch_gus_data."
    )

    messages: List[AnyMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=raw_text),
    ]

    node_messages: List[AnyMessage] = []
    node_errors: List[str] = []
    normalized_data: Optional[Dict[str, Any]] = None
    max_iterations = 12

    for _ in range(max_iterations):
        ai_message: AIMessage = await llm_with_tools.ainvoke(messages)  # type: ignore
        node_messages.append(ai_message)

        if not ai_message.tool_calls:
            node_errors.append("API Engineer stopped without fetching data.")
            break

        for tool_call in ai_message.tool_calls:
            tool = tool_map.get(tool_call["name"])
            if tool is None:
                node_errors.append(f"Requested tool '{tool_call['name']}' is not available.")
                continue

            try:
                output_str = await tool.ainvoke(tool_call["args"])
            except Exception as e:
                output_str = json.dumps({"error": f"Tool execution unhandled exception: {str(e)}"})
                node_errors.append(f"Tool '{tool_call['name']}' failed: {str(e)}")

            node_messages.append(ToolMessage(content=output_str, tool_call_id=tool_call["id"]))

            if tool_call["name"] == "fetch_gus_data":
                try:
                    parsed = json.loads(output_str)
                    if isinstance(parsed, dict) and "error" not in parsed:
                        normalized_data = parsed
                except json.JSONDecodeError:
                    pass

        if normalized_data is not None:
            break

    if normalized_data is None and not node_errors:
        node_errors.append("API Engineer did not return normalized data.")

    return normalized_data, node_errors, node_messages


async def _run_fred_resolution(
    raw_text: str,
) -> Tuple[Optional[Dict[str, Any]], List[str], List[AnyMessage]]:
    """Use the single-tool FRED resolution flow."""
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

    if not ai_message.tool_calls:
        node_errors.append("API Engineer failed to generate a tool call for FRED.")
    else:
        tool_call = ai_message.tool_calls[0]
        try:
            output_str = await resolve_and_fetch_fred.ainvoke(tool_call["args"])
            output_dict = json.loads(output_str)
            node_messages.append(ToolMessage(content=output_str, tool_call_id=tool_call["id"]))
            if "error" in output_dict:
                node_errors.append(output_dict["error"])
            else:
                normalized_data = output_dict
        except Exception as e:
            node_errors.append(f"FRED tool execution failed: {str(e)}")

    return normalized_data, node_errors, node_messages


async def api_engineer_agent_node(state: OrchestratorState) -> Dict[str, Any]:
    """Bind adapter tools, extract parameters via LLM, and execute data retrieval."""
    source = state.get("selected_source", "UNKNOWN")
    raw_text = _extract_query_string(state.get("user_query", ""))

    if not raw_text:
        return {"errors": ["API Engineer received an empty query."]}

    if source == "FRED":
        normalized_data, node_errors, node_messages = await _run_fred_resolution(raw_text)
    else:
        normalized_data, node_errors, node_messages = await _run_gus_resolution_agent(raw_text)

    return {
        "messages": node_messages,
        "normalized_data": normalized_data,
        "errors": node_errors,
    }


def route_after_api(state: OrchestratorState) -> str:
    """Determine next graph transition based on data presence and error state."""
    if state.get("normalized_data") and not state.get("errors"):
        return "analyst"
    return "error_handler"