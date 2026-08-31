"""Integration tests for the LangGraph workflow with live local LLM and external data sources."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator

import httpx
import pytest
import pytest_asyncio
from pytest import MonkeyPatch

import app.workflow.graph
from app.storage.dynamodb_saver import DynamoDBSaver, init_dynamodb_tables
from app.workflow.graph import invoke_workflow

DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://dynamodb-local:8000")
TABLE_NAME = "govdata-sessions-test"


def is_infrastructure_available() -> bool:
    """Verify local DynamoDB and LLM endpoints are reachable before executing integration tests."""
    api_base = os.getenv("OPENAI_API_BASE", "http://host.docker.internal:1234/v1")
    try:
        # Check LLM
        llm_resp = httpx.get(f"{api_base}/models", timeout=2.0)
        # Check DynamoDB Local
        ddb_resp = httpx.get(DYNAMODB_ENDPOINT, timeout=2.0)
        return llm_resp.status_code == 200 and ddb_resp.status_code == 400  # DDB Local returns 400 on root GET
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not is_infrastructure_available(),
    reason="Required local infrastructure (DynamoDB/LM Studio) is unreachable. Skipping live integration tests."
)


@pytest.fixture(autouse=True)
def setup_integration_env(monkeypatch: MonkeyPatch) -> Generator[None, None, None]:
    """Configure environment variables for local LLM inference and API keys."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", os.getenv("LLM_MODEL", "qwen3.8-27b"))
    monkeypatch.setenv("OPENAI_API_BASE", os.getenv("OPENAI_API_BASE", "http://host.docker.internal:1234/v1"))
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "lm-studio"))

    if not os.getenv("FRED_API_KEY"):
        monkeypatch.setenv("FRED_API_KEY", "dummy_key_for_test_skip")

    yield


@pytest_asyncio.fixture(autouse=True)
async def setup_workflow_checkpointer() -> AsyncGenerator[None, None]:
    """Ensure the sessions table exists and route the global graph checkpointer to it."""
    await init_dynamodb_tables(
        table_name=TABLE_NAME,
        region_name="us-east-1",
        endpoint_url=DYNAMODB_ENDPOINT,
    )

    saver = DynamoDBSaver(
        table_name=TABLE_NAME,
        region_name="us-east-1",
        endpoint_url=DYNAMODB_ENDPOINT,
    )

    # Route global graph to use the test checkpointer
    original_checkpointer = app.workflow.graph.app_graph.checkpointer
    app.workflow.graph.app_graph.checkpointer = saver

    yield

    # Restore global state
    app.workflow.graph.app_graph.checkpointer = original_checkpointer


@pytest.mark.asyncio
async def test_workflow_gus_routing_and_analysis() -> None:
    """Validate that a Poland-specific query routes to GUS, retrieves records, and synthesizes data."""
    query = "What is the population of Poland over the last few years?"
    session_id = "test-live-gus-001"

    result = await invoke_workflow(query=query, session_id=session_id)

    assert result["selected_source"] == "GUS"
    assert result["final_answer"] is not None
    assert len(result["final_answer"]) > 0
    assert "error" not in [err.lower() for err in result.get("errors", [])]
    assert result["normalized_data"] is not None
    assert result["analysis_result"] is not None


@pytest.mark.asyncio
async def test_workflow_fred_routing_and_analysis() -> None:
    """Validate that a US macroeconomic query routes to FRED, retrieves observations, and synthesizes data."""
    if os.getenv("FRED_API_KEY") == "dummy_key_for_test_skip":
        pytest.skip("FRED_API_KEY not configured. Skipping live FRED workflow test.")

    query = "What is the current US GDP and how has it changed recently?"
    session_id = "test-live-fred-001"

    result = await invoke_workflow(query=query, session_id=session_id)

    assert result["selected_source"] == "FRED"
    assert result["final_answer"] is not None
    assert len(result["final_answer"]) > 0
    assert "error" not in [err.lower() for err in result.get("errors", [])]
    assert result["normalized_data"] is not None
    assert result["analysis_result"] is not None


@pytest.mark.asyncio
async def test_workflow_unsupported_routing() -> None:
    """Validate that out-of-scope queries trigger the UNSUPPORTED route and error handling."""
    query = "Can you give me a recipe for chocolate chip cookies?"
    session_id = "test-live-unsupported-001"

    result = await invoke_workflow(query=query, session_id=session_id)

    assert result["selected_source"] == "UNSUPPORTED"
    assert len(result["errors"]) > 0
    assert "unsupported" in result["errors"][-1].lower() or "ambiguous" in result["errors"][-1].lower()
    assert result["final_answer"] is not None
    assert "sorry" in result["final_answer"].lower()