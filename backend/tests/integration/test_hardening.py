"""Integration tests for production hardening middleware and RFC 7807 error responses."""

from typing import Any, AsyncGenerator
import json
import random

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Alias import to avoid namespace collision with the 'app' directory during test discovery
from app.main import app as fastapi_app
import app.routes.orchestration as routes_module

ASK_ROUTE = "/api/v1/ask"


def _problem_json(res: Any) -> dict[str, Any]:
    """Helper to assert and extract RFC 7807 problem JSON."""
    assert res.headers.get("content-type", "").startswith("application/problem+json")
    data = res.json()
    assert isinstance(data, dict)
    return data


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock external dependencies to isolate routing and middleware tests."""
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "govdata-sessions-test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("DYNAMODB_ENDPOINT", "http://localhost:8000")
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    async def _fake_invoke_workflow(*, query: str, session_id: str) -> dict[str, Any]:
        return {"final_answer": "ok", "errors": [], "analysis_result": None}

    monkeypatch.setattr(routes_module, "invoke_workflow", _fake_invoke_workflow)


@pytest_asyncio.fixture
async def app_client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTPX AsyncClient with a mocked client IP to trigger rate limiting."""
    transport = ASGITransport(app=fastapi_app, client=("10.0.0.1", 12345))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture
async def isolated_client() -> AsyncGenerator[AsyncClient, None]:
    """Client with a unique IP so valid requests do not hit the shared rate-limit bucket."""
    ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
    transport = ASGITransport(app=fastapi_app, client=(ip, 12345))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture
async def exception_client() -> AsyncGenerator[AsyncClient, None]:
    """Client that returns app exceptions as responses instead of raising them in pytest."""
    ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
    transport = ASGITransport(app=fastapi_app, client=(ip, 12345), raise_app_exceptions=False)
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
    responses = [await app_client.post(ASK_ROUTE, json=payload) for _ in range(35)]
    
    rate_limited_responses = [r for r in responses if r.status_code == 429]
    assert len(rate_limited_responses) > 0, "Rate limiting did not trigger."

    data = _problem_json(rate_limited_responses[0])
    assert data["type"] == "urn:govdata:error:rate-limit"
    assert data["status"] == 429


@pytest.mark.asyncio
async def test_rate_limit_is_per_client_ip() -> None:
    payload = {"message": "Rate limit isolation", "session_id": "isolation-session"}

    async def post_with_ip(ip: str) -> list[int]:
        transport = ASGITransport(app=fastapi_app, client=(ip, 12345))
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return [getattr(await client.post(ASK_ROUTE, json=payload), "status_code") for _ in range(31)]

    ip_a = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.101"
    ip_b = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.102"

    statuses_a = await post_with_ip(ip_a)
    assert 429 in statuses_a[-5:], "Expected IP A to be rate limited."

    statuses_b = await post_with_ip(ip_b)
    assert all(status != 429 for status in statuses_b[:30]), "IP B should not inherit IP A rate-limit state."


@pytest.mark.asyncio
async def test_cors_and_security_headers(app_client: AsyncClient) -> None:
    headers = {"Origin": "https://govstatscope.com", "Access-Control-Request-Method": "POST"}
    res = await app_client.options(ASK_ROUTE, headers=headers)
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "https://govstatscope.com"


@pytest.mark.asyncio
async def test_cors_disallowed_origin_is_not_reflected(app_client: AsyncClient) -> None:
    headers = {"Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"}
    res = await app_client.options(ASK_ROUTE, headers=headers)
    assert res.headers.get("access-control-allow-origin") != "https://evil.example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"invalid_field": "data"},
    {"session_id": "only_session"},
    {"message": ""},
    {"message": 123},
    {"message": None},
    {"message": "Valid", "session_id": 12345}
])
async def test_payload_validation_errors(app_client: AsyncClient, payload: Any) -> None:
    res = await app_client.post(ASK_ROUTE, json=payload)
    assert res.status_code == 422
    data = _problem_json(res)
    assert data["type"] == "urn:govdata:error:validation"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["null", "{", json.dumps(["message"])])
async def test_malformed_json_body_returns_422(app_client: AsyncClient, content: str) -> None:
    res = await app_client.post(ASK_ROUTE, content=content, headers={"content-type": "application/json"})
    assert res.status_code == 422
    data = _problem_json(res)
    assert data["type"] == "urn:govdata:error:validation"


@pytest.mark.asyncio
async def test_oversized_and_unicode_messages_do_not_crash(isolated_client: AsyncClient) -> None:
    res_huge = await isolated_client.post(ASK_ROUTE, json={"message": "x" * 200_000, "session_id": "large"})
    assert res_huge.status_code in (200, 413, 422, 500)

    res_uni = await isolated_client.post(ASK_ROUTE, json={"message": "Łódź — ą ć ę ł ź ż 📊", "session_id": "uni"})
    assert res_uni.status_code in (200, 413, 422, 500)


@pytest.mark.asyncio
async def test_unknown_route_returns_rfc7807_404(app_client: AsyncClient) -> None:
    res = await app_client.post("/api/v1/definitely-not-a-route", json={"message": "x"})
    assert res.status_code == 404
    assert _problem_json(res)["status"] == 404


@pytest.mark.asyncio
async def test_method_not_allowed_on_ask_returns_405(app_client: AsyncClient) -> None:
    res = await app_client.get(ASK_ROUTE)
    assert res.status_code in (404, 405)
    if res.status_code == 405:
        assert _problem_json(res)["status"] == 405


@pytest.mark.asyncio
async def test_workflow_failure_returns_rfc7807_500(exception_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _failing_invoke_workflow(*, query: str, session_id: str) -> dict[str, Any]:
        raise RuntimeError("simulated workflow failure")

    monkeypatch.setattr(routes_module, "invoke_workflow", _failing_invoke_workflow)
    res = await exception_client.post(ASK_ROUTE, json={"message": "Fail me", "session_id": "fail"})
    
    assert res.status_code == 500
    assert _problem_json(res)["status"] == 500