"""Golden dataset test for GUS resolution.

Uses real LLM keyword expansion (local LLM if configured) against a mocked
GUS subject hierarchy loaded from fixture files by conftest.py.
Variable and data fetchers are monkeypatched to isolate subject routing.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.workflow.nodes.api_engineer.gus_resolver import _run_gus_resolution_agent

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


async def _mock_fetch_variables(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"variables": [{"id": "VAR001", "name": "Test variable"}]}


async def _mock_fetch_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"data": [{"year": payload["year_start"], "value": 100}]}


@pytest.fixture(autouse=True)
def _reset_progress():
    global recorded_progress
    recorded_progress = []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected_k,expected_g,expected_p",
    GOLDEN_DATA,
    ids=[item[0] for item in GOLDEN_DATA],
)
async def test_gus_golden_dataset(
    monkeypatch,
    mock_gus_api_responses,
    query: str,
    expected_k: str,
    expected_g: str,
    expected_p: str,
):
    # Only subject traversal is tested; variables/data are mocked away.
    monkeypatch.setattr(
        "app.workflow.nodes.api_engineer.gus_resolver.push_progress",
        _push_progress,
    )
    monkeypatch.setattr(
        "app.workflow.nodes.api_engineer.gus_resolver.fetch_gus_variables",
        SimpleNamespace(ainvoke=_mock_fetch_variables),
    )
    monkeypatch.setattr(
        "app.workflow.nodes.api_engineer.gus_resolver.fetch_gus_data",
        SimpleNamespace(ainvoke=_mock_fetch_data),
    )

    parsed_data, node_errors, _, resolved_context = await _run_gus_resolution_agent(
        raw_text=query,
        session_id="unit-test",
    )

    assert resolved_context is not None, f"Resolver failed: {node_errors}"
    assert parsed_data is not None, "No data returned"

    visited = _visited_ids()
    assert visited.get("K") == expected_k, f"Expected K {expected_k}, got {visited.get('K')}"
    assert visited.get("G") == expected_g, f"Expected G {expected_g}, got {visited.get('G')}"
    assert (
        resolved_context["subject_id"] == expected_p
    ), f"Expected P {expected_p}, got {resolved_context['subject_id']}"