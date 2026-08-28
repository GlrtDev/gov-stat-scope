"""Pure ASGI middleware threading request/trace IDs and logging request metrics."""

from __future__ import annotations

import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.context import set_request_context
from app.logging_config import get_logger


class RequestContextMiddleware:
    """Pure ASGI middleware calculating request duration and injecting correlation IDs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        request_id = _header(scope, "x-request-id") or uuid.uuid4().hex
        trace_id = _header(scope, "x-trace-id") or uuid.uuid4().hex

        set_request_context(request_id, trace_id)

        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        get_logger().info(
            "request_started",
            extra={
                "http_method": method,
                "http_path": path,
                "client_ip": client_ip,
            },
        )

        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                headers.append((b"x-trace-id", trace_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            get_logger().error(
                "request_failed",
                extra={
                    "http_method": method,
                    "http_path": path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                },
                exc_info=True,
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            get_logger().info(
                "request_completed",
                extra={
                    "http_method": method,
                    "http_path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )


def _header(scope: Scope, name: str) -> str | None:
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return None