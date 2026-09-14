from __future__ import annotations

import pytest

from app.adapters.gus import GUSClient


def _select_contains(items: list[dict], keyword: str, key: str = "name") -> dict:
    """Deterministic stand-in for the LLM's subject/variable selection."""
    return next(
        item for item in items
        if keyword.lower() in str(item.get(key, "")).lower()
    )


@pytest.mark.asyncio
async def test_deep_llm_guided_flow_pszenica_price_in_poland_2017(
    mock_gus_api_responses,
) -> None:
    """Simulates the LLM step-by-step GUS traversal using the shared mock fixture."""
    client = GUSClient(api_key="test-key")

    try:
        # Step 1 — LLM fetches top-level subjects.
        top_subjects = await client.fetch_subjects()

        # Step 2 — LLM selects the most relevant top-level subject and drills down.
        agriculture = _select_contains(top_subjects, "ceny")
        child_subjects = await client.fetch_subjects(agriculture["id"])

        # Step 3 — LLM picks the price/agri sub-subject and drills deeper.
        price_child = _select_contains(child_subjects, "rolnictwie")
        grandchild_subjects = await client.fetch_subjects(price_child["id"])

        # Step 4 — LLM selects the most specific subject and searches variables.
        # (GUS has a flat variables/search endpoint, so no extra drill-down override.)
        variables = await client.fetch_variables_for_subject(
            grandchild_subjects[5]["id"],
            "pszenica",
        )
        variable = _select_contains(variables, "pszenica", key="n1")

        # Step 5 — LLM fetches data for the resolved variable.
        raw_data = await client.fetch_time_range(
            variable_id=variable["id"],
            year_start=2017,
            year_end=2017,
            unit_level="0"
        )

        assert variable["id"] == 4859
        assert raw_data["results"][0]["values"][0]["val"] == pytest.approx(66.44)

        assert any(
            "data/by-variable/4859" in call
            for call in mock_gus_api_responses
        )
    finally:
        await client.aclose()