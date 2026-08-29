"""Unit tests for the LangGraph workflow using mocked LLM and HTTP boundaries."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

import app.workflow.graph
from app.workflow.graph import invoke_workflow
from app.workflow.nodes.analyst import AnalystOutput
from app.workflow.nodes.router import RouterOutput


@pytest.fixture(autouse=True)
def override_checkpointer() -> None:
    """Replace the global DynamoDB checkpointer with an in-memory saver to prevent network I/O."""
    original_checkpointer = app.workflow.graph.app_graph.checkpointer
    app.workflow.graph.app_graph.checkpointer = MemorySaver()
    yield
    # Restore the original checkpointer to prevent cross-test pollution
    app.workflow.graph.app_graph.checkpointer = original_checkpointer


@pytest.fixture
def mock_llm_chain() -> None:
    """Mock the LLM factory to return deterministic Pydantic schemas and Native Tool Calls."""
    with patch("app.workflow.nodes.router.get_llm") as mock_router_llm, \
         patch("app.workflow.nodes.api_engineer.get_llm") as mock_api_llm, \
         patch("app.workflow.nodes.analyst.get_llm") as mock_analyst_llm:

        # 1. Mock Router Node
        router_chain = AsyncMock()
        router_chain.ainvoke.return_value = RouterOutput(
            selected_source="FRED", 
            reason="Targeting US data.", 
            confidence=0.99,
            extracted_entities={"metric": "GDP"}
        )
        mock_router_llm.return_value.with_structured_output.return_value = router_chain

        # 2. Mock API Engineer Node (Now using bind_tools instead of structured_output)
        api_chain = AsyncMock()
        api_chain.ainvoke.return_value = AIMessage(
            content="",
            tool_calls=[{
                "name": "resolve_and_fetch_fred",
                "args": {
                    "query": "GDP",
                    "start_date": "2018-01-01",
                    "end_date": "2023-01-01"
                },
                "id": "mock_tool_call_123"
            }]
        )
        mock_api_llm.return_value.bind_tools.return_value = api_chain

        # 3. Mock Analyst Node
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
    """Validate full LangGraph traversal with HTTP, Checkpointer, and LLM boundaries mocked."""
    # Intercept the FRED adapter HTTP request
    respx.get(url__startswith="https://api.stlouisfed.org/").mock(
        return_value=Response(
            status_code=200, 
            json={"observations": [{"date": "2023-01-01", "value": "25000.0"}]}
        )
    )

    result = await invoke_workflow(query="What is the US GDP?", session_id="unit-test-01")

    assert result["selected_source"] == "FRED"
    assert result["final_answer"] == "The US GDP is mocked."
    assert "normalized_data" in result