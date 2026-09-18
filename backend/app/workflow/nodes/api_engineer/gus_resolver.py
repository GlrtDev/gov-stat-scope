"""GUS resolution agent: beam search through subject hierarchy and variable selection."""
from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.messages import AnyMessage, ToolMessage

from app.workflow.nodes.api_engineer.constants import BEAM_G, BEAM_K, BEAM_P
from app.workflow.nodes.api_engineer.dates import _extract_year_range
from app.workflow.nodes.api_engineer.keywords import _generate_expanded_keywords, _query_terms
from app.workflow.nodes.api_engineer.matching import _rank_candidates, _select_best_item, _score_item
from app.workflow.nodes.api_engineer.parsing import (
    _extract_items,
    _item_id,
    _item_name,
    _parse_json_payload,
)
from app.workflow.nodes.api_engineer.types import ResolutionResult
from app.workflow.progress import push_progress
from app.workflow.tools import (
    fetch_gus_data,
    fetch_gus_subjects,
    fetch_gus_variables,
)


async def _run_gus_resolution_agent(
    raw_text: str,
    session_id: str,
) -> ResolutionResult:
    node_messages: List[AnyMessage] = []
    node_errors: List[str] = []
    await push_progress(session_id, {"type": "gus_search_started"})

    expanded_terms = await _generate_expanded_keywords(raw_text)
    search_terms = _query_terms(raw_text) + (expanded_terms or [])

    # --- Phase 1: top‑level subjects ---
    top_output = await fetch_gus_subjects.ainvoke({})
    node_messages.append(ToolMessage(content=top_output, tool_call_id="gus-subjects-top"))
    top_subjects = _extract_items(top_output, "subjects", "items", "results", "data")
    if not top_subjects:
        node_errors.append("No top-level GUS subjects returned.")
        await push_progress(session_id, {"type": "error", "message": node_errors[-1]})
        return None, node_errors, node_messages, None

    k_candidates = await _rank_candidates(
        top_subjects,
        raw_text,
        "top-level GUS subject",
        top_n=BEAM_K,
        expanded_terms=expanded_terms,
    )

    # Pre‑fetch children and compute child scores to break ties
    k_with_scores = []
    for k_subject in k_candidates:
        k_id = _item_id(k_subject)
        k_name = _item_name(k_subject)

        # Fetch children once
        children_output = await fetch_gus_subjects.ainvoke({"parent_id": k_id})
        node_messages.append(ToolMessage(content=children_output, tool_call_id=f"gus-children-{k_id}"))
        g_subjects = _extract_items(children_output, "subjects", "items", "results", "data")

        # Lexical score for the K subject itself
        k_score = _score_item(k_subject, search_terms)

        # Best lexical score among its children
        child_score = 0.0
        if g_subjects:
            child_score = max(_score_item(g, search_terms) for g in g_subjects)

        k_with_scores.append((k_score, child_score, k_subject, g_subjects))

    # Sort by (K score, child score) descending – breaks ties
    k_with_scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
    k_candidates = [entry[2] for entry in k_with_scores]
    g_children_map = {_item_id(entry[2]): entry[3] for entry in k_with_scores}

    # --- Phase 2: iterate over re‑ranked K candidates ---
    for k_index, k_subject in enumerate(k_candidates):
        k_id = _item_id(k_subject)
        k_name = _item_name(k_subject)
        await push_progress(session_id, {
            "type": "subject_selected",
            "level": "K",
            "subject_id": k_id,
            "name": k_name,
        })

        g_subjects = g_children_map[k_id]  # already fetched
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
            node_messages.append(ToolMessage(content=p_output, tool_call_id=f"gus-children-{g_id}"))
            p_subjects = _extract_items(p_output, "subjects", "items", "results", "data")
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

                # 4. Search variables under this P subject (unchanged)
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
                    node_messages.append(ToolMessage(
                        content=variables_output,
                        tool_call_id=f"gus-variables-{p_id}",
                    ))

                if not variables:
                    continue

                # 5. Select best variable and fetch data (unchanged)
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
                node_messages.append(ToolMessage(
                    content=data_output,
                    tool_call_id=f"gus-data-{variable_id}",
                ))

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