"""API Engineer node executing native tool binding and parameter resolution."""

from __future__ import annotations

import difflib
import json
from typing import Any, Dict, List, Optional, Tuple
import re
from datetime import date
import unicodedata

from app.workflow.progress import push_progress  # corrected import
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

# Add near top, after imports
RANK_POOL_SIZE = 10  # For LLM fallback only

async def _generate_expanded_keywords(raw_text: str) -> List[str]:
    """Use LLM to generate 10-20 Polish terms (synonyms, inflections, related words)
    for robust lexical matching. Called once per query to save tokens."""
    llm = get_llm(temperature=0.0)
    prompt = (
        "Generate 10 to 20 distinct Polish words or short phrases that would appear in "
        "statistical variable names related to the user request. Include synonyms, "
        "inflected forms (e.g., 'cena', 'ceny', 'cen'), and domain terms. "
        "Exclude city names, region names, or administrative units. "
        "Return a JSON list of strings.\n\n"
        f"User request: {raw_text}\n\n"
        'Respond with only JSON: {"terms": ["term1", "term2", ...]}'
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = getattr(response, "content", None)
    if isinstance(content, list):
        content = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    elif content is None:
        content = str(response)
    else:
        content = str(content)

    parsed = _parse_json_object(content)
    terms = parsed.get("terms") if parsed else None
    if not isinstance(terms, list):
        terms = []
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            try:
                arr = json.loads(match.group(0))
                if isinstance(arr, list):
                    terms = [str(k).strip() for k in arr if str(k).strip()]
            except json.JSONDecodeError:
                pass

    seen = set()
    cleaned = []
    for t in terms:
        t = t.strip().lower()
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    return cleaned[:20]

def _extract_query_string(user_query: Any) -> str:
    """Safely extract the raw query string from dicts, objects, or primitive strings."""
    if isinstance(user_query, str):
        return user_query
    if isinstance(user_query, dict):
        return str(user_query.get("raw_text") or user_query.get("query") or "")
    return str(getattr(user_query, "raw_text", user_query))


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


MAX_SELECTION_CANDIDATES = 30

# Minimal Polish stopwords for query-token fallback scoring
_QUERY_STOPWORDS = {
    "jaka", "jaki", "jakie", "była", "był", "było", "były", "jest", "są",
    "być", "w", "na", "dla", "po", "z", "do", "od", "roku", "lat",
    "latach", "ile", "wynosi", "wynosiła", "wyniosła", "wyniosły", "podaj",
    "pokaż", "chcę", "chciałbym", "chciałabym", "proszę",
}

_FOLLOWUP_STOPWORDS = _QUERY_STOPWORDS | {
    "what", "about", "how", "co", "z", "w", "a", "the", "for", "in", "and", "jak"
}


def _is_temporal_followup(raw_text: str, previous_context: Dict[str, Any]) -> bool:
    if not previous_context or previous_context.get("source") != "GUS":
        return False
    if not re.search(r"\b(?:19|20)\d{2}\b", raw_text):
        return False
    tokens = [
        t for t in re.findall(r"\w+", raw_text.lower())
        if t not in _FOLLOWUP_STOPWORDS and not t.isdigit()
    ]
    return len(tokens) == 0

def _normalize_text(text: str) -> str:
    """Lowercase, strip diacritics, keep only letters/digits/spaces."""
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9\s]", " ", text).strip()


def _term_match_score(candidate_name: str, terms: List[str]) -> int:
    """Score a candidate by how well related terms match its name."""
    norm = _normalize_text(candidate_name)
    name_tokens = norm.split()
    score = 0

    for term in terms:
        norm_term = _normalize_text(term)
        if not norm_term:
            continue

        # Exact substring match (handles "rynek pracy" in "Rynek pracy")
        if norm_term in norm:
            score += 3
            continue

        # Token subset match (handles "praca" in "rynek pracy")
        term_tokens = set(norm_term.split())
        if term_tokens and term_tokens.issubset(set(name_tokens)):
            score += 2
            continue

        # Fuzzy token match for Polish inflection ("bezrobocie" vs "bezrobocia")
        for name_token in name_tokens:
            if len(norm_term) >= 4 and len(name_token) >= 4:
                ratio = difflib.SequenceMatcher(None, norm_term, name_token).ratio()
                if ratio >= 0.85:
                    score += 1
                    break

    return score


def _query_terms(raw_text: str) -> List[str]:
    """Extract relevant tokens from the raw query for fallback scoring."""
    return [
        token
        for token in re.findall(r"\w+", _normalize_text(raw_text))
        if token not in _QUERY_STOPWORDS and len(token) > 2
    ]


async def _select_best_item(
    items: List[Dict[str, Any]],
    raw_text: str,
    item_description: str,
    expanded_terms: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    if len(items) == 1:
        return items[0]

    # Combine query tokens with expanded terms
    search_terms = _query_terms(raw_text)
    if expanded_terms:
        search_terms.extend(expanded_terms)

    if search_terms:
        scored = sorted(
            (
                (_term_match_score(_item_name(item), search_terms), index, item)
                for index, item in enumerate(items)
            ),
            key=lambda pair: (pair[0], -pair[1]),
            reverse=True,
        )
        if scored and scored[0][0] > 0:
            return scored[0][2]

    # Lexical failed – rare fallback to LLM (small pool only)
    llm = get_llm(temperature=0.0)
    candidates = items[:RANK_POOL_SIZE]
    options = "\n".join(f"- {_item_label(item)} (id: {_item_id(item)})" for item in candidates)
    prompt = (
        f"Select the most relevant {item_description} for the user request.\n"
        "Pay attention to Polish inflection.\n\n"
        f"User request: {raw_text}\n\n"
        f"Candidates:\n{options}\n\n"
        'Respond with only JSON: {"selected_id": "<exact id>"}'
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = getattr(response, "content", None)
    if isinstance(content, list):
        content = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    elif content is None:
        content = str(response)
    else:
        content = str(content)
    parsed = _parse_json_object(content)
    selected_id = parsed.get("selected_id") if parsed else None
    if selected_id:
        for item in candidates:
            if _item_id(item) == str(selected_id):
                return item
    return candidates[0]


async def _rank_candidates(
    items: List[Dict[str, Any]],
    raw_text: str,
    item_description: str,
    top_n: int = 5,
    expanded_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Lexical-only ranking using expanded terms. No LLM call."""
    if not items:
        return []
    if len(items) <= top_n:
        return items[:top_n]

    search_terms = _query_terms(raw_text)
    if expanded_terms:
        search_terms.extend(expanded_terms)

    if search_terms:
        scored = sorted(
            (
                (_term_match_score(_item_name(item), search_terms), index, item)
                for index, item in enumerate(items)
            ),
            key=lambda pair: (pair[0], -pair[1]),
            reverse=True,
        )
        ranked = [item for score, _, item in scored if score > 0]
        for _, _, item in scored:
            if item not in ranked:
                ranked.append(item)
            if len(ranked) >= top_n:
                break
        return ranked[:top_n]

    return items[:top_n]


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


# Add near existing constants
BEAM_K = 5
BEAM_G = 3
BEAM_P = 3


async def _run_gus_resolution_agent(
    raw_text: str,
    session_id: str,
) -> Tuple[Optional[Dict[str, Any]], List[str], List[AnyMessage], Optional[Dict[str, Any]]]:
    node_messages: List[AnyMessage] = []
    node_errors: List[str] = []

    await push_progress(session_id, {"type": "gus_search_started"})

    # One-time LLM call for expanded lexical terms
    expanded_terms = await _generate_expanded_keywords(raw_text)

    # 1. Top-level subjects (K)
    top_output = await fetch_gus_subjects.ainvoke({})
    node_messages.append(
        ToolMessage(content=top_output, tool_call_id="gus-subjects-top")
    )
    top_subjects = _extract_items(top_output, "subjects", "items", "results", "data")
    if not top_subjects:
        node_errors.append("No top-level GUS subjects returned.")
        await push_progress(session_id, {"type": "error", "message": node_errors[-1]})
        return None, node_errors, node_messages

    k_candidates = await _rank_candidates(
        top_subjects,
        raw_text,
        "top-level GUS subject",
        top_n=BEAM_K,
        expanded_terms=expanded_terms,
    )

    for k_index, k_subject in enumerate(k_candidates):
        k_id = _item_id(k_subject)
        k_name = _item_name(k_subject)
        await push_progress(session_id, {
            "type": "subject_selected",
            "level": "K",
            "subject_id": k_id,
            "name": k_name,
        })

        # 2. Fetch children of K (G-level)
        children_output = await fetch_gus_subjects.ainvoke({"parent_id": k_id})
        node_messages.append(
            ToolMessage(
                content=children_output,
                tool_call_id=f"gus-children-{k_id}",
            )
        )
        g_subjects = _extract_items(
            children_output, "subjects", "items", "results", "data"
        )
        if not g_subjects:
            continue

        g_candidates = await _rank_candidates(
            g_subjects,
            raw_text,
            f"GUS child subject under {k_id}",
            top_n=BEAM_G,
            expanded_terms=expanded_terms,
        )

        for g_index, g_subject in enumerate(g_candidates):
            g_id = _item_id(g_subject)
            g_name = _item_name(g_subject)
            await push_progress(session_id, {
                "type": "subject_selected",
                "level": "G",
                "subject_id": g_id,
                "name": g_name,
            })

            # 3. Fetch children of G (P-level)
            p_output = await fetch_gus_subjects.ainvoke({"parent_id": g_id})
            node_messages.append(
                ToolMessage(
                    content=p_output,
                    tool_call_id=f"gus-children-{g_id}",
                )
            )
            p_subjects = _extract_items(
                p_output, "subjects", "items", "results", "data"
            )
            if not p_subjects:
                continue

            p_candidates = await _rank_candidates(
                p_subjects,
                raw_text,
                f"GUS P-level subject under {g_id}",
                top_n=BEAM_P,
                expanded_terms=expanded_terms,
            )

            for p_index, p_subject in enumerate(p_candidates):
                p_id = _item_id(p_subject)
                p_name = _item_name(p_subject)
                await push_progress(session_id, {
                    "type": "subject_selected",
                    "level": "P",
                    "subject_id": p_id,
                    "name": p_name,
                })

                # 4. Search variables under this P subject
                variables_output = None
                variables: List[Dict[str, Any]] = []
                for term in expanded_terms:
                    try:
                        variables_output = await fetch_gus_variables.ainvoke({
                            "subject_id": p_id,
                            "query": term,
                        })
                    except Exception:
                        continue

                    variables = _extract_items(
                        variables_output, "variables", "items", "results", "data"
                    )
                    if variables:
                        break

                if variables_output is not None:
                    node_messages.append(
                        ToolMessage(
                            content=variables_output,
                            tool_call_id=f"gus-variables-{p_id}",
                        )
                    )
                if not variables:
                    continue

                # 5. Select best variable and fetch data
                selected_variable = await _select_best_item(
                    variables,
                    raw_text,
                    "GUS variable",
                    expanded_terms=expanded_terms,
                )
                if selected_variable is None:
                    continue

                variable_id = _item_id(selected_variable)
                variable_name = _item_name(selected_variable)
                if not variable_id:
                    continue

                await push_progress(session_id, {
                    "type": "variable_selected",
                    "variable_id": variable_id,
                    "name": variable_name,
                })

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
                    continue

                if isinstance(parsed_data, dict) and "error" in parsed_data:
                    node_errors.append(str(parsed_data["error"]))
                    continue

                if isinstance(parsed_data, dict):
                    await push_progress(session_id, {
                        "type": "data_fetched",
                        "variable_id": variable_id,
                        "years": [year_start, year_end],
                    })
                    resolved_context = {
                        "source": "GUS",
                        "subject_id": p_id,
                        "variable_id": variable_id,
                        "variable_name": variable_name,
                    }
                    return parsed_data, node_errors, node_messages, resolved_context

    node_errors.append("No suitable GUS subject/variable combination found for the query.")
    await push_progress(session_id, {"type": "error", "message": node_errors[-1]})
    return None, node_errors, node_messages, None


async def _run_fred_resolution(
    raw_text: str,
    session_id: str,
) -> Tuple[Optional[Dict[str, Any]], List[str], List[AnyMessage], Optional[Dict[str, Any]]]:
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