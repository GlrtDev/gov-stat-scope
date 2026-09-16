"""LLM keyword expansion and query token extraction."""
from __future__ import annotations

import json
import re
from typing import List

from langchain_core.messages import HumanMessage

from app.workflow.llm_factory import get_llm
from app.workflow.nodes.api_engineer.constants import _QUERY_STOPWORDS
from app.workflow.nodes.api_engineer.parsing import _parse_json_object
from app.workflow.nodes.api_engineer.text_utils import _normalize_text


async def _generate_expanded_keywords(raw_text: str) -> List[str]:
    """Use LLM to generate 10-20 Polish terms for robust lexical matching."""
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
    cleaned: List[str] = []
    for t in terms:
        t = str(t).strip().lower()
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    return cleaned[:20]


def _query_terms(raw_text: str) -> List[str]:
    """Extract relevant tokens from the raw query for fallback scoring."""
    return [
        token
        for token in re.findall(r"\w+", _normalize_text(raw_text))
        if token not in _QUERY_STOPWORDS and len(token) > 2
    ]