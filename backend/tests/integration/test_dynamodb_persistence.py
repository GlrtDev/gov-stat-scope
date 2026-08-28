import os
import uuid
from typing import AsyncGenerator

import boto3
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.workflow.graph import invoke_workflow

# Local DynamoDB endpoint used in testing environments
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8000")


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure environment variables for local DynamoDB and LLM execution."""
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "govdata-sessions-test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "dummy")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")
    monkeypatch.setenv("DYNAMODB_ENDPOINT", DYNAMODB_ENDPOINT)
    
    # Bypass Bedrock, redirect to local LM Studio (from Phase 3 setup)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-27b")
    monkeypatch.setenv("OPENAI_API_BASE", "http://host.docker.internal:1234/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "lm-studio")


@pytest_asyncio.fixture
async def app_client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTPX AsyncClient that automatically triggers FastAPI lifespan events."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_dynamodb_multi_turn_memory(app_client: AsyncClient) -> None:
    """
    Tests multi-turn persistence in LangGraph backed by DynamoDB.
    Validates that follow-up queries inherit session context and checkpoints are saved.
    """
    session_id = f"test-session-{uuid.uuid4()}"

    # Turn 1: Initial query
    turn1_query = "What is the population of Poland in 2022?"
    res1 = await invoke_workflow(turn1_query, session_id)
    
    assert res1.get("selected_source") == "GUS"
    assert res1.get("final_answer") is not None
    turn1_msg_count = len(res1.get("messages", []))
    assert turn1_msg_count > 0

    # Verify DynamoDB Local explicitly using boto3
    dynamodb = boto3.client(
        "dynamodb", 
        endpoint_url=DYNAMODB_ENDPOINT, 
        region_name="us-east-1"
    )
    db_items = dynamodb.query(
        TableName="govdata-sessions-test",
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": {"S": session_id}}
    )
    assert len(db_items["Items"]) > 0, "Checkpoints must be persisted in DynamoDB"

    # Turn 2: Contextual follow-up
    turn2_query = "How about 2023?"
    res2 = await invoke_workflow(turn2_query, session_id)
    
    # Ensure graph successfully processed the follow-up and accumulated state
    assert res2.get("final_answer") is not None
    assert len(res2.get("messages", [])) > turn1_msg_count, "Messages should accumulate in persistent state"


@pytest.mark.asyncio
async def test_get_session_endpoint(app_client: AsyncClient) -> None:
    """
    Queries the REST API to retrieve stored conversational state and validates its schema.
    """
    session_id = f"test-api-{uuid.uuid4()}"
    
    # Populate graph state
    await invoke_workflow("What is the US GDP?", session_id)
    
    # Fetch from the HTTP endpoint
    response = await app_client.get(f"/sessions/{session_id}")
    
    assert response.status_code == 200, f"Endpoint returned {response.status_code}: {response.text}"
    
    data = response.json()
    assert data["session_id"] == session_id
    assert "messages" in data
    assert isinstance(data["messages"], list)
    assert len(data["messages"]) > 0
    assert data.get("selected_source") == "FRED"
    assert data.get("final_answer") is not None
    assert "metadata" in data