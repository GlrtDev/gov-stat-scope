"""End-to-end integration test: real LLM routes a Polish query through GUS and returns the 2017 wheat price."""

from __future__ import annotations

import os
import uuid
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
    """Verify local DynamoDB and LLM endpoints are reachable before executing live E2E tests."""
    api_base = os.getenv("OPENAI_API_BASE", "http://host.docker.internal:1234/v1")
    try:
        llm_resp = httpx.get(f"{api_base}/models", timeout=2.0)
        ddb_resp = httpx.get(DYNAMODB_ENDPOINT, timeout=2.0)
        return llm_resp.status_code == 200 and ddb_resp.status_code == 400
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not is_infrastructure_available(),
    reason="Required local infrastructure (DynamoDB/LM Studio) is unreachable. Skipping live E2E test.",
)


@pytest.fixture(autouse=True)
def setup_integration_env(monkeypatch: MonkeyPatch) -> Generator[None, None, None]:
    """Configure environment variables for local LLM inference and API keys."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", os.getenv("LLM_MODEL", "qwen3.8-27b"))
    monkeypatch.setenv("OPENAI_API_BASE", os.getenv("OPENAI_API_BASE", "http://host.docker.internal:1234/v1"))
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "lm-studio"))
    monkeypatch.setenv("DYNAMODB_ENDPOINT", DYNAMODB_ENDPOINT)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_SECURITY_TOKEN", raising=False)

    if not os.getenv("GUS_API_KEY"):
        monkeypatch.setenv("GUS_API_KEY", "test-key")

    yield


def _contains_numeric_value(data: object, expected: float, tolerance: float = 0.01) -> bool:
    """Recursively search for a numeric value in the workflow result payload."""
    if isinstance(data, dict):
        return any(_contains_numeric_value(v, expected, tolerance) for v in data.values())
    if isinstance(data, list):
        return any(_contains_numeric_value(item, expected, tolerance) for item in data)
    if isinstance(data, (int, float)):
        return abs(float(data) - expected) <= tolerance
    return False


@pytest.mark.asyncio
async def test_workflow_gus_pszenica_price_2017_e2e() -> None:
    """Full LLM-guided flow: Polish query -> GUS routing -> data retrieval -> numeric result 66.44."""
    query = "Jaka była cena pszenicy w 2017?"
    session_id = f"test-e2e-gus-{uuid.uuid4().hex}"

    result = await invoke_workflow(query=query, session_id=session_id)

    assert result["selected_source"] == "GUS"
    assert result["final_answer"] is not None
    assert len(result["final_answer"]) > 0
    assert "error" not in [err.lower() for err in result.get("errors", [])]
    assert result["normalized_data"] is not None
    assert result["analysis_result"] is not None

    assert _contains_numeric_value(result["normalized_data"], 66.44), (
        f"Expected 66.44 in normalized data, got: {result['normalized_data']}"
    )

    # TODO add follow up question like "a jaka w 2018?"