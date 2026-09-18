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

from typing import Any, Dict, List, Optional

def _score_item(item: Dict[str, Any], search_terms: List[str]) -> float:
    """Public wrapper around _term_match_score for a single GUS item."""
    return _term_match_score(_item_name(item), search_terms)


def _token_similar(term_token: str, name_token: str) -> bool:
    """Inflection-aware equivalence for Polish tokens."""
    if term_token == name_token:
        return True
    if len(term_token) < 4 or len(name_token) < 4:
        return False
    return difflib.SequenceMatcher(None, term_token, name_token).ratio() >= 0.7


def _phrase_matches(
    term: str, name_tokens: List[str]
) -> Optional[List[str]]:
    """Match an inflected multi-word phrase against adjacent label tokens."""
    term_tokens = term.split()
    if len(term_tokens) < 2:
        return None
    for i in range(len(name_tokens) - len(term_tokens) + 1):
        window = name_tokens[i:i + len(term_tokens)]
        if all(_token_similar(t, w) for t, w in zip(term_tokens, window)):
            return window
    return None


def _term_match_score(candidate_name: str, terms: List[str]) -> float:
    """Score candidate by inflected phrase/term matches and label coverage."""
    norm = _normalize_text(candidate_name)
    if not norm:
        return 0.0
    name_tokens = norm.split()
    token_set = set(name_tokens)
    score = 0.0
    covered_tokens = set()

    for term in terms:
        norm_term = _normalize_text(term)
        if not norm_term:
            continue
        term_tokens = norm_term.split()
        matched_tokens = None

        if len(term_tokens) > 1:
            matched_tokens = _phrase_matches(norm_term, name_tokens)
            if matched_tokens:
                score += 4.0
        else:
            term_token = norm_term
            if norm_term in norm:
                matched_tokens = [tok for tok in name_tokens if term_token in tok]
            elif any(_token_similar(term_token, name_token) for name_token in name_tokens):
                matched_tokens = [
                    name_token
                    for name_token in name_tokens
                    if _token_similar(term_token, name_token)
                ]
            if matched_tokens:
                score += 3.0

        if not matched_tokens:
            continue

        new_tokens = [tok for tok in matched_tokens if tok not in covered_tokens]
        if not new_tokens:
            continue
        covered_tokens.update(new_tokens)
        if len(new_tokens) < len(matched_tokens):
            score += 0.5  # partial overlap with already-covered tokens

    if token_set:
        coverage = len(covered_tokens & token_set) / len(token_set)
        score += coverage * 4.0

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

    scored = None
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
        # If the top two scores are close, let the LLM decide
        if len(scored) >= 2 and (scored[0][0] - scored[1][0]) < 1.0:
            # LLM fallback (same as existing code but only among top-2)
            candidates = items[:2]
            options = "\n".join(f"- {_item_label(item)} (id: {_item_id(item)})" for item in candidates)
            prompt = (
                f"Select the most relevant {item_description} for the user request.\n"
                "Pay attention to Polish inflection.\n\n"
                f"User request: {raw_text}\n\n"
                f"Candidates:\n{options}\n\n"
                'Respond with only JSON: {"selected_id": "<exact id>"}'
            )
            llm = get_llm(temperature=0.0)
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
        return scored[0][2]

    # No lexical hit → use LLM on the full pool (existing logic)
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