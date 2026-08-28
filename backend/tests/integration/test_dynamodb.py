"""Integration tests validating DynamoDB operations and session endpoints against live local infrastructure."""

from __future__ import annotations

import os
import uuid
from typing import AsyncGenerator

import boto3
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langchain_core.runnables import RunnableConfig

from app.main import app
from app.storage.dynamodb_saver import DynamoDBSaver, init_dynamodb_tables
from app.workflow.graph import invoke_workflow

DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8000")
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
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not is_infrastructure_available(),
    reason="Required local infrastructure (DynamoDB/LM Studio) is unreachable. Skipping live integration tests."
)


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure environment variables for DynamoDB Local and local LLM execution."""
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", TABLE_NAME)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "dummy")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")
    monkeypatch.setenv("DYNAMODB_ENDPOINT", DYNAMODB_ENDPOINT)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", os.getenv("LLM_MODEL", "qwen3.8-27b"))
    monkeypatch.setenv("OPENAI_API_BASE", os.getenv("OPENAI_API_BASE", "http://host.docker.internal:1234/v1"))
    monkeypatch.setenv("OPENAI_API_KEY", "lm-studio")


@pytest_asyncio.fixture
async def ddb_saver() -> AsyncGenerator[DynamoDBSaver, None]:
    """Ensure the target DynamoDB table exists and yield a configured DynamoDBSaver instance."""
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
    yield saver


@pytest_asyncio.fixture
async def app_client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTPX AsyncClient that manages FastAPI lifecycle events."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_dynamodb_checkpointer_direct_io(ddb_saver: DynamoDBSaver) -> None:
    """Validate direct write, read, and non-existent thread lookups via DynamoDBSaver."""
    thread_id = f"unit-thread-{uuid.uuid4()}"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    checkpoint = {
        "v": 1,
        "id": "chk-001",
        "ts": "2026-08-28T00:00:00.000Z",
        "channel_values": {"user_query": "Direct I/O verification"},
    }
    metadata = {"source": "input", "step": 1, "writes": {}, "parents": {}}

    await ddb_saver.aput(config, checkpoint, metadata, {})
    retrieved = await ddb_saver.aget_tuple(config)

    assert retrieved is not None
    assert retrieved.checkpoint["id"] == "chk-001"
    assert retrieved.checkpoint["channel_values"]["user_query"] == "Direct I/O verification"

    missing_lookup = await ddb_saver.aget_tuple({"configurable": {"thread_id": f"missing-{uuid.uuid4()}"}})
    assert missing_lookup is None


@pytest.mark.asyncio
async def test_dynamodb_multi_turn_memory(ddb_saver: DynamoDBSaver) -> None:
    """Validate multi-turn conversation persistence and checkpoint creation in DynamoDB."""
    session_id = f"test-session-{uuid.uuid4()}"

    turn1_res = await invoke_workflow("What is the population of Poland in 2022?", session_id)
    assert turn1_res.get("selected_source") == "GUS"
    assert turn1_res.get("final_answer") is not None
    turn1_msg_count = len(turn1_res.get("messages", []))
    assert turn1_msg_count > 0

    dynamodb = boto3.client("dynamodb", endpoint_url=DYNAMODB_ENDPOINT, region_name="us-east-1")
    db_items = dynamodb.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": {"S": session_id}},
    )
    assert len(db_items["Items"]) > 0

    turn2_res = await invoke_workflow("How about 2023?", session_id)
    assert turn2_res.get("final_answer") is not None
    assert len(turn2_res.get("messages", [])) > turn1_msg_count


@pytest.mark.asyncio
async def test_get_session_endpoint(app_client: AsyncClient, ddb_saver: DynamoDBSaver) -> None:
    """Validate that stored session state is retrievable through the REST API."""
    session_id = f"test-api-{uuid.uuid4()}"

    await invoke_workflow("What is the US GDP?", session_id)

    response = await app_client.get(f"/sessions/{session_id}")
    assert response.status_code == 200, f"Failed with {response.status_code}: {response.text}"

    data = response.json()
    assert data["session_id"] == session_id
    assert isinstance(data.get("messages"), list)
    assert len(data["messages"]) > 0
    assert data.get("selected_source") == "FRED"
    assert data.get("final_answer") is not None
    assert "metadata" in data