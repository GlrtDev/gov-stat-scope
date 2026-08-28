"""Unit tests for the LangGraph workflow using mocked LLM and HTTP boundaries."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response

from app.workflow.graph import invoke_workflow
from app.workflow.nodes.analyst import AnalystOutput
from app.workflow.nodes.api_engineer import ApiEngineerOutput
from app.workflow.nodes.router import RouterOutput


@pytest.fixture
def mock_llm_chain() -> None:
    """Mock the LLM factory to return deterministic Pydantic schemas."""
    with patch("app.workflow.nodes.router.get_llm") as mock_router_llm, \
         patch("app.workflow.nodes.api_engineer.get_llm") as mock_api_llm, \
         patch("app.workflow.nodes.analyst.get_llm") as mock_analyst_llm:

        router_chain = AsyncMock()
        router_chain.ainvoke.return_value = RouterOutput(
            selected_source="FRED", reasoning="Targeting US data.", extracted_metric="GDP"
        )
        mock_router_llm.return_value.with_structured_output.return_value = router_chain

        api_chain = AsyncMock()
        api_chain.ainvoke.return_value = ApiEngineerOutput(
            endpoint_target="observations",
            resolved_metric_id="GDP",
            query_parameters={"series_id": "GDP", "file_type": "json"},
            justification="Fetching US GDP."
        )
        mock_api_llm.return_value.with_structured_output.return_value = api_chain

        analyst_chain = AsyncMock()
        analyst_chain.ainvoke.return_value = AnalystOutput(
            final_answer="The US GDP is mocked.",
            key_metrics=[{"date": "2023-01-01", "value": 25000.0}],
            calculations_performed=["Mocked average calculation."],
            confidence_notes="Mocked data."
        )
        mock_analyst_llm.return_value.with_structured_output.return_value = analyst_chain

        yield


@pytest.mark.asyncio
@respx.mock
async def test_mocked_end_to_end_workflow(mock_llm_chain: None) -> None:
    """Validate full LangGraph traversal with HTTP and LLM boundaries mocked."""
    respx.get(url__startswith="https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=Response(
            status_code=200, 
            json={"observations": [{"date": "2023-01-01", "value": "25000.0"}]}
        )
    )

    result = await invoke_workflow(query="What is the US GDP?", session_id="unit-test-01")

    assert result["selected_source"] == "FRED"
    assert "api_parameters" in result
    assert result["final_answer"] == "The US GDP is mocked."
    assert "normalized_data" in result