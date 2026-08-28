"""Unit tests for the LangGraph router classification node."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from app.workflow.nodes.router import RouterOutput, router_agent_node
from app.workflow.state import OrchestratorState


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "mock_source", "mock_reasoning"),
    [
        (
            "Jaka jest stopa bezrobocia w Polsce w 2023 roku?",
            "GUS",
            "Query targets Polish macroeconomic data from GUS.",
        ),
        (
            "What was the US GDP in Q4 2022?",
            "FRED",
            "Query targets United States economic indicators from FRED.",
        ),
        (
            "Show me the latest inflation rate.",
            "AMBIGUOUS",
            "Query lacks geographical context to determine GUS vs FRED.",
        ),
        (
            "Predict tomorrow's stock price for Apple.",
            "UNKNOWN",
            "Query requests predictive financial advice outside statistical agency scope.",
        ),
    ],
)
async def test_router_classification_paths(
    query: str,
    mock_source: str,
    mock_reasoning: str,
) -> None:
    """Validate that the router node classifies queries into deterministic data sources."""
    mock_output = RouterOutput(
        selected_source=mock_source,
        reasoning=mock_reasoning,
        extracted_metric="unemployment_or_gdp",
    )

    state: OrchestratorState = {
        "user_query": query,
        "session_id": "test-session-123",
        "messages": [],
        "selected_source": "",
        "api_parameters": {},
        "raw_data": {},
        "normalized_data": {},
        "analysis_result": {},
        "final_answer": "",
        "errors": [],
    }

    with patch("app.workflow.nodes.router.get_llm") as mock_get_llm:
        mock_llm_instance = AsyncMock()
        mock_structured_llm = AsyncMock()
        mock_structured_llm.ainvoke.return_value = mock_output
        mock_llm_instance.with_structured_output.return_value = mock_structured_llm
        mock_get_llm.return_value = mock_llm_instance

        result: Dict[str, Any] = await router_agent_node(state)

        assert result["selected_source"] == mock_source
        assert len(result["messages"]) == 1
        assert result["messages"][0].content == mock_reasoning