"""Unit tests for rate limiter key extraction and error handling."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse

from app.rate_limiter import get_rate_limit_key, rate_limit_error_handler


def _build_request(
    *,
    session_id: str | None = None,
    client_ip: str = "1.2.3.4",
    path: str = "/api/query",
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if session_id is not None:
        headers.append((b"x-session-id", session_id.encode()))
    scope: Dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": headers,
        "query_string": b"",
        "server": ("testserver", 80),
        "client": (client_ip, 1234),
        "scheme": "http",
        "http_version": "1.1",
    }
    return Request(scope)


def test_get_rate_limit_key_uses_session_header() -> None:
    request = _build_request(session_id="session-abc")

    assert get_rate_limit_key(request) == "session-abc"


def test_get_rate_limit_key_falls_back_to_client_ip() -> None:
    request = _build_request(client_ip="203.0.113.10")

    assert get_rate_limit_key(request) == "203.0.113.10"


def test_rate_limit_error_handler_returns_problem_json() -> None:
    request = _build_request(path="/api/query")
    exc = SimpleNamespace(detail="Too many requests")

    response = rate_limit_error_handler(request, exc)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 429
    assert response.media_type == "application/problem+json"

    body = json.loads(response.body.decode("utf-8"))
    assert body == {
        "type": "urn:govdata:error:rate-limit",
        "title": "Rate Limit Exceeded",
        "status": 429,
        "detail": "Rate limit exceeded: Too many requests",
        "path": "/api/query",
    }