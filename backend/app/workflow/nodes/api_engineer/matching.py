"""Lexical and LLM-assisted candidate ranking for GUS ids."""
from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from app.workflow.llm_factory import get_llm
from app.workflow.nodes.api_engineer.constants import RANK_POOL_SIZE
from app.workflow.nodes.api_engineer.keywords import _query_terms
from app.workflow.nodes.api_engineer.parsing import (
    _item_id,
    _item_label,
    _item_name,
    _parse_json_object,
)
from app.workflow.nodes.api_engineer.text_utils import _normalize_text


def _term_match_score(candidate_name: str, terms: List[str]) -> int:
    """Score a candidate by how well related terms match its name."""
    norm = _normalize_text(candidate_name)
    name_tokens = norm.split()
    score = 0
    for term in terms:
        norm_term = _normalize_text(term)
        if not norm_term:
            continue
        if norm_term in norm:
            score += 3
            continue
        term_tokens = set(norm_term.split())
        if term_tokens and term_tokens.issubset(set(name_tokens)):
            score += 2
            continue
        for name_token in name_tokens:
            if len(norm_term) >= 4 and len(name_token) >= 4:
                ratio = difflib.SequenceMatcher(None, norm_term, name_token).ratio()
                if ratio >= 0.85:
                    score += 1
                    break
    return score


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
        return ranked[:top_n]

    return items[:top_n]