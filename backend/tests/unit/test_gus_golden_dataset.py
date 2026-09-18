"""Golden dataset test for GUS resolution.

Uses real LLM keyword expansion against a mocked GUS subject hierarchy loaded
from fixture files. Supports a local LLM (LM Studio) via environment variables.
The test passes if at least 5 of 10 queries resolve to the expected K/G/P path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import httpx
import pytest

from app.workflow.nodes.api_engineer.gus_resolver import _run_gus_resolution_agent

LLM_API_BASE = os.getenv("OPENAI_API_BASE", "http://host.docker.internal:1234/v1")


def _is_local_llm_available() -> bool:
    try:
        response = httpx.get(f"{LLM_API_BASE}/models", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _is_local_llm_available(),
    reason="Local LLM is not reachable; skipping golden dataset test.",
)

# query -> (expected K, expected G, expected P)
GOLDEN_DATA: List[tuple[str, str, str, str]] = [
    ("Ile wynosiły ceny skupu pszenicy?", "K15", "G186", "P1458"),
    ("Ile wyniosły dochody budżetów powiatów?", "K27", "G195", "P1514"),
    ("Wyniki finansowe przedsiębiorstw według sekcji PKD", "K43", "G418", "P2695"),
    ("Płatności w ramach jednej Kampanii z funduszy unijnych", "K47", "G556", "P3578"),
    ("Ile energii elektrycznej zużywają gospodarstwa domowe w miastach?", "K11", "G57", "P1604"),
    ("Liczba organizacji według klasy przychodów", "K44", "G446", "P2829"),
    ("Liczba sklepów i stacji paliw", "K16", "G64", "P2323"),
    ("Stopa inwestycji w gospodarce narodowej", "K28", "G277", "P3473"),
    ("Ile było bibliotek publicznych?", "K23", "G226", "P1688"),
    ("Ćwiczący w sekcjach sportowych - sporty walki", "K24", "G334", "P2158"),
]

recorded_progress: List[Dict[str, Any]] = []


@pytest.fixture(autouse=True)
def setup_local_llm_env(monkeypatch) -> None:
    """Point get_llm to the local LM Studio-compatible API."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", os.getenv("LLM_MODEL", "qwen3.8-27b"))
    monkeypatch.setenv("OPENAI_API_BASE", LLM_API_BASE)
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "lm-studio"))



async def _push_progress(session_id: str, data: Dict[str, Any]) -> None:
    recorded_progress.append(data)


def _visited_ids() -> Dict[str, str]:
    visited: Dict[str, str] = {}
    for entry in recorded_progress:
        if entry.get("type") == "subject_selected":
            level = entry.get("level")
            subject_id = entry.get("subject_id")
            if level and subject_id:
                visited[level] = subject_id
    return visited


@pytest.mark.asyncio
async def test_gus_golden_dataset_at_least_six(
    monkeypatch,
    mock_gus_tool_responses,
) -> None:
    monkeypatch.setattr(
        "app.workflow.nodes.api_engineer.gus_resolver.push_progress",
        _push_progress,
    )

    failures = []

    for query, expected_k, expected_g, expected_p in GOLDEN_DATA:
        recorded_progress.clear()
        try:
            parsed_data, node_errors, _, resolved_context = await _run_gus_resolution_agent(
                raw_text=query,
                session_id="golden-test",
            )

            assert resolved_context is not None, f"Resolver failed: {node_errors}"
            assert parsed_data is not None, "No data returned"

            visited = _visited_ids()
            assert visited.get("K") == expected_k, (
                f"Expected {expected_k}, got {visited.get('K')}"
            )
            assert visited.get("G") == expected_g, (
                f"Expected {expected_g}, got {visited.get('G')}"
            )
            assert resolved_context["subject_id"] == expected_p, (
                f"Expected {expected_p}, got {resolved_context['subject_id']}"
            )
        except Exception as exc:  # noqa: BLE001 - routing failure counts as one miss
            failures.append(f"{query!r}: {exc}")

    passed = len(GOLDEN_DATA) - len(failures)
    assert passed >= 5, (
        f"Only {passed}/{len(GOLDEN_DATA)} queries resolved correctly.\n"
        + "\n".join(failures)
   )