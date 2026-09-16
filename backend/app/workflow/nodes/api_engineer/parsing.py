"""JSON parsing and GUS payload item helpers."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


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