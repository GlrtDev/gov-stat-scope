"""Integration tests for production hardening middleware and RFC 7807 error responses."""

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock external dependencies to isolate routing and middleware tests."""
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "govdata-sessions-test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("DYNAMODB_ENDPOINT", "http://localhost:8000")
    monkeypatch.setenv("LLM_PROVIDER", "openai")


@pytest_asyncio.fixture
async def app_client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_liveness_and_readiness_probes(app_client: AsyncClient) -> None:
    live_res = await app_client.get("/health/live")
    assert live_res.status_code == 200
    assert live_res.json()["status"] == "ok"
    
    ready_res = await app_client.get("/health/ready")
    assert ready_res.status_code in (200, 503)
    data = ready_res.json()
    assert "status" in data
    assert "checks" in data
    assert "dynamodb" in data["checks"]


@pytest.mark.asyncio
async def test_rate_limiting_enforcement(app_client: AsyncClient) -> None:
    payload = {"message": "Rate limit test ping", "session_id": "test_session_123"}
    
    # Intentionally overflow the 30/minute limit
    responses = []
    for _ in range(35):
        responses.append(await app_client.post("/ask", json=payload, headers={"X-Forwarded-For": "10.0.0.1"}))
    
    rate_limited_responses = [r for r in responses if r.status_code == 429]
    assert len(rate_limited_responses) > 0, "Rate limiting did not trigger."
    
    res = rate_limited_responses[0]
    assert res.headers["content-type"] == "application/problem+json"
    
    data = res.json()
    assert data["type"] == "urn:govdata:error:rate-limit"
    assert data["title"] == "Rate Limit Exceeded"
    assert data["status"] == 429


@pytest.mark.asyncio
async def test_cors_and_security_headers(app_client: AsyncClient) -> None:
    headers = {
        "Origin": "https://govstatscope.com",
        "Access-Control-Request-Method": "POST"
    }
    res = await app_client.options("/ask", headers=headers)
    
    assert res.status_code == 200
    assert "access-control-allow-origin" in res.headers
    assert res.headers["access-control-allow-origin"] == "https://govstatscope.com"


@pytest.mark.asyncio
async def test_payload_validation_errors(app_client: AsyncClient) -> None:
    res = await app_client.post("/ask", json={"invalid_field": "data"})
    
    assert res.status_code == 422
    assert res.headers["content-type"] == "application/problem+json"
    
    data = res.json()
    assert data["type"] == "urn:govdata:error:validation"
    assert data["title"] == "Request Validation Failed"
    assert "errors" in data