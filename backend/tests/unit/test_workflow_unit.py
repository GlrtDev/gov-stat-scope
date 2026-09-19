"""Unit tests for the LangGraph workflow using mocked LLM and HTTP boundaries."""

from __future__ import annotations

from typing import Any, Generator, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

import app.workflow.graph
from app.workflow.graph import invoke_workflow
from app.workflow.nodes.analyst import AnalystOutput
from app.workflow.nodes.api_engineer import node as api_node_module
from app.workflow.nodes.router import RouterOutput


@pytest.fixture(autouse=True)
def override_checkpointer() -> Generator[None, None, None]:
    """Replace the global DynamoDB checkpointer with an in-memory saver."""
    original_checkpointer = app.workflow.graph.app_graph.checkpointer
    app.workflow.graph.app_graph.checkpointer = MemorySaver()
    yield
    app.workflow.graph.app_graph.checkpointer = original_checkpointer


def safe_human_message(*args: Any, **kwargs: Any) -> HumanMessage:
    """Prevent Pydantic ValidationErrors if legacy nodes pass dicts."""
    if "content" in kwargs and isinstance(kwargs["content"], dict):
        kwargs["content"] = kwargs["content"].get("raw_text", str(kwargs["content"]))
    elif len(args) > 0 and isinstance(args[0], dict):
        args = (args[0].get("raw_text", str(args[0])),) + args[1:]
    return HumanMessage(*args, **kwargs)


@pytest.fixture
def mock_llm_chain() -> Generator[None, None, None]:
    """Mock router/analyst LLM chains and bypass API Engineer internals for FRED."""
    # Router LLM: with_structured_output must be synchronous, returning an AsyncMock chain.
    router_chain = AsyncMock()
    router_chain.ainvoke.return_value = RouterOutput(
        selected_source="FRED",
        reason="Targeting US data.",
        confidence=0.99,
        extracted_entities={"metric": "GDP"},
    )
    router_llm = MagicMock()
    router_llm.with_structured_output.return_value = router_chain

    # Analyst LLM: same pattern.
    analyst_chain = AsyncMock()
    analyst_chain.ainvoke.return_value = AnalystOutput(
        final_answer="The US GDP is mocked.",
        key_metrics=[{"date": "2023-01-01", "value": 25000.0}],
        calculations_performed=["Mocked average calculation."],
        confidence_notes="Mocked data.",
    )
    analyst_llm = MagicMock()
    analyst_llm.with_structured_output.return_value = analyst_chain

    # API Engineer: bypass the FRED resolver and return normalized data directly.
    async def fake_fred_resolution(
        raw_text: str, session_id: str
    ) -> Tuple[dict[str, Any], list[str], list[Any], dict[str, Any]]:
        normalized_data = {
            "metric_name": "Gross Domestic Product",
            "region": "US",
            "frequency": "Annual",
            "values": [{"date": "2023-01-01", "value": 25000.0}],
        }
        return (
            normalized_data,
            [],
            [],
            {"resolved_series_id": "GDP", "metric": "GDP"},
        )

    with patch("app.workflow.nodes.router.get_llm", return_value=router_llm), \
         patch("app.workflow.nodes.analyst.get_llm", return_value=analyst_llm), \
         patch("app.workflow.nodes.router.push_progress", new=AsyncMock()), \
         patch("app.workflow.nodes.analyst.push_progress", new=AsyncMock()), \
         patch.object(api_node_module, "_run_fred_resolution", new=fake_fred_resolution), \
         patch("app.workflow.nodes.api_engineer.node.HumanMessage", new=safe_human_message, create=True), \
         patch("app.workflow.nodes.analyst.HumanMessage", new=safe_human_message, create=True):
        yield


@pytest.mark.asyncio
@respx.mock
async def test_mocked_end_to_end_workflow(mock_llm_chain: None) -> None:
    """Validate full LangGraph traversal with HTTP, Checkpointer, and LLM boundaries mocked."""
    # Safety-net route; no real HTTP occurs because the FRED resolver is mocked.
    respx.get(url__startswith="https://api.stlouisfed.org/").mock(
        return_value=Response(
            status_code=200,
            json={"observations": [{"date": "2023-01-01", "value": "25000.0"}]},
        )
    )

    result = await invoke_workflow(query="What is the US GDP?", session_id="unit-test-01")

    assert result["selected_source"] == "FRED"
    assert result["final_answer"] == "The US GDP is mocked."
    assert "normalized_data" in result