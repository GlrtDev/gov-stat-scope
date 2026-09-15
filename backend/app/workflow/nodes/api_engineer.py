"""API Engineer node executing native tool binding and parameter resolution."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple
import re
from datetime import date

from backend.app.workflow.progress import push_progress
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


import re

def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_json_payload(payload: str) -> Any:
    text = _strip_json_fence(payload)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    parsed = _parse_json_payload(text)
    return parsed if isinstance(parsed, dict) else None


def _item_id(item: Dict[str, Any]) -> Optional[str]:
    for key in ("id", "subject_id", "variable_id", "code"):
        value = item.get(key)
        if value is not None:
            return str(value)
    return None

def _item_name(item: Dict[str, Any]) -> str:
    for key in ("name", "nazwa", "title", "label"):
        value = item.get(key)
        if value:
            return str(value)
    return _item_id(item) or str(item)


def _item_label(item: Dict[str, Any]) -> str:
    for key in ("name", "nazwa", "title", "label"):
        value = item.get(key)
        if value:
            return str(value)
    return _item_id(item) or str(item)


def _extract_items(payload: str, *keys: str) -> List[Dict[str, Any]]:
    data = _parse_json_payload(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _subject_level(subject_id: Optional[str]) -> str:
    if not subject_id:
        return "?"
    prefix = subject_id.strip().upper()
    return prefix[0] if prefix[:1] in {"K", "G", "P"} else "?"


async def _select_best_item(
    items: List[Dict[str, Any]],
    raw_text: str,
    item_description: str,
) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    if len(items) == 1:
        return items[0]

    llm = get_llm(temperature=0.0)
    options = "\n".join(
        f"- {_item_label(item)} (id: {_item_id(item)})"
        for item in items
    )
    prompt = (
        f"Select the most relevant {item_description} for the user request.\n\n"
        f"User request: {raw_text}\n\n"
        f"Available options:\n{options}\n\n"
        'Respond with only JSON: {"selected_id": "<exact id>"}'
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = getattr(response, "content", None)
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    elif content is None:
        content = str(response)
    else:
        content = str(content)

    parsed = _parse_json_object(content)
    selected_id = parsed.get("selected_id") if parsed else None
    if selected_id is None:
        match = re.search(
            r'["\']?selected_id["\']?\s*:\s*["\']([^"\']+)["\']',
            content,
        )
        if match:
            selected_id = match.group(1)

    if selected_id:
        for item in items:
            if _item_id(item) == str(selected_id):
                return item

    return items[0]


async def _extract_gus_keywords(raw_text: str) -> List[str]:
    """Ask the LLM for a list of search keywords (not city/region) for GUS variable lookup."""
    llm = get_llm(temperature=0.0)
    prompt = (
        "Extract 3 to 5 distinct Polish noun phrases from the user request for searching "
        "statistical variables in GUS. Do not return cities, regions, or administrative units. "
        "Return the most relevant domain words ordered by relevance "
        "(e.g., ['pszenica', 'cena', 'ceny']).\n\n"
        f"User request: {raw_text}\n\n"
        'Respond with only JSON: {"keywords": ["<word1>", "<word2>", ...]}'
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = getattr(response, "content", None)
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    elif content is None:
        content = str(response)
    else:
        content = str(content)

    parsed = _parse_json_object(content)
    keywords = parsed.get("keywords") if parsed else None
    if not isinstance(keywords, list):
        keywords = []
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            try:
                arr = json.loads(match.group(0))
                if isinstance(arr, list):
                    keywords = [str(k).strip() for k in arr if str(k).strip()]
            except json.JSONDecodeError:
                pass

    if not keywords:
        tokens = [
            t.lower()
            for t in re.sub(r"[^\w\sąćęłńóśźż]+", " ", raw_text, flags=re.UNICODE).split()
            if t.lower()
            not in {
                "jaka", "jaki", "jakie", "była", "był", "było", "były",
                "jest", "są", "być", "w", "na", "dla", "po", "z", "do", "od",
                "roku", "lat", "latach", "ile", "wynosi", "wynosiła",
                "wyniosła", "wyniosły", "podaj", "pokaż", "chcę", "chciałbym",
                "chciałabym", "proszę",
            }
        ]
        seen = set()
        for t in tokens:
            if t not in seen:
                seen.add(t)
                keywords.append(t)

    seen = set()
    cleaned: List[str] = []
    for kw in keywords:
        kw = kw.strip()
        if kw and kw not in seen:
            seen.add(kw)
            cleaned.append(kw)
        if len(cleaned) >= 10:
            break

    return cleaned

def _extract_year_range(raw_text: str) -> Tuple[int, int]:
    """Extract `(year_start, year_end)` from the query; fallback to last 5 years."""
    current_year = date.today().year
    years = sorted({
        int(m.group(0))
        for m in re.finditer(r"\b(?:19|20)\d{2}\b", raw_text)
        if 1900 <= int(m.group(0)) <= current_year
    })
    if not years:
        return current_year - 4, current_year
    if len(years) == 1:
        return years[0], years[0]
    return years[0], years[-1]

async def _run_gus_resolution_agent(
    raw_text: str,
) -> Tuple[Optional[Dict[str, Any]], List[str], List[AnyMessage]]:
    node_messages: List[AnyMessage] = []
    node_errors: List[str] = []

    # 1. Top-level subjects (K-level)
    top_output = await fetch_gus_subjects.ainvoke({})
    node_messages.append(
        ToolMessage(content=top_output, tool_call_id="gus-subjects-top")
    )
    top_subjects = _extract_items(top_output, "subjects", "items", "results", "data")
    if not top_subjects:
        node_errors.append("No top-level GUS subjects returned.")
        return None, node_errors, node_messages

    selected_subject = await _select_best_item(
        top_subjects, raw_text, "top-level GUS subject"
    )
    if selected_subject is None:
        node_errors.append("Failed to select a top-level GUS subject.")
        return None, node_errors, node_messages

    # 2. Drill down K -> G -> P (max 2 child fetches)
    for _ in range(2):
        subject_id = _item_id(selected_subject)
        if not subject_id:
            node_errors.append("Selected GUS subject has no id.")
            return None, node_errors, node_messages
        if _subject_level(subject_id) == "P":
            break

        children_output = await fetch_gus_subjects.ainvoke({"parent_id": subject_id})
        node_messages.append(
            ToolMessage(
                content=children_output,
                tool_call_id=f"gus-children-{subject_id}",
            )
        )
        children = _extract_items(
            children_output, "subjects", "items", "results", "data"
        )
        if not children:
            node_errors.append(
                f"No child subjects returned for subject '{subject_id}'."
            )
            return None, node_errors, node_messages

        selected_subject = await _select_best_item(
            children,
            raw_text,
            f"GUS child subject under {subject_id}",
        )
        if selected_subject is None:
            node_errors.append(
                f"Failed to select a child subject under '{subject_id}'."
            )
            return None, node_errors, node_messages

    subject_id = _item_id(selected_subject)
    if not subject_id:
        node_errors.append("Selected GUS subject has no id.")
        return None, node_errors, node_messages

    if _subject_level(subject_id) != "P":
        node_errors.append(
            f"Could not reach a P-level GUS subject (stopped at '{subject_id}')."
        )
        return None, node_errors, node_messages

    # 3. List variables for the P-level subject using keyword fallbacks
    keywords = await _extract_gus_keywords(raw_text)
    variables_output = None
    for keyword in keywords:
        try:
            variables_output = await fetch_gus_variables.ainvoke({
                "subject_id": subject_id,
                "query": keyword,
            })
        except Exception:
            continue

        variables = _extract_items(variables_output, "variables", "items", "results", "data")
        if variables:
            break

    if variables_output is None or not variables:
        node_errors.append(
            f"No GUS variables returned for subject '{subject_id}' with any keyword."
            "Tried keywords: " + ", ".join(keywords)
        )
        return None, node_errors, node_messages

    node_messages.append(
        ToolMessage(
            content=variables_output,
            tool_call_id=f"gus-variables-{subject_id}",
        )
    )

    selected_variable = await _select_best_item(variables, raw_text, "GUS variable")
    if selected_variable is None:
        node_errors.append("Failed to select a GUS variable.")
        return None, node_errors, node_messages

    variable_id = _item_id(selected_variable)
    variable_name = _item_name(selected_variable)
    if not variable_id:
        node_errors.append("Selected GUS variable has no id.")
        return None, node_errors, node_messages

    # 4. Fetch the normalized series
    year_start, year_end = _extract_year_range(raw_text)
    data_output = await fetch_gus_data.ainvoke({
        "variable_id": variable_id,
        "variable_name": variable_name,
        "year_start": year_start,
        "year_end": year_end,
    })
    node_messages.append(
        ToolMessage(
            content=data_output,
            tool_call_id=f"gus-data-{variable_id}",
        )
    )

    parsed_data = _parse_json_payload(data_output)
    if parsed_data is None:
        node_errors.append("fetch_gus_data returned invalid JSON.")
        return None, node_errors, node_messages

    if isinstance(parsed_data, dict) and "error" in parsed_data:
        node_errors.append(str(parsed_data["error"]))
        return None, node_errors, node_messages

    if not isinstance(parsed_data, dict):
        node_errors.append("fetch_gus_data did not return a normalized dictionary.")
        return None, node_errors, node_messages

    return parsed_data, node_errors, node_messages


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