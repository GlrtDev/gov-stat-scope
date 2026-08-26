"""ASGI middleware that generates and threads request/trace IDs."""

from __future__ import annotations

import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.context import set_request_context
from app.logging_config import get_logger


class RequestContextMiddleware:
    """Generate and log a request ID and trace ID for every incoming request.

    Implemented as pure ASGI middleware (not BaseHTTPMiddleware) so that
    exceptions raised downstream still reach the app-level exception handler
    and are converted into structured JSON errors.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _header(scope, "x-request-id") or uuid.uuid4().hex
        trace_id = _header(scope, "x-trace-id") or uuid.uuid4().hex

        set_request_context(request_id, trace_id)

        get_logger().info(
            "request_started",
            extra={"method": scope.get("method"), "path": scope.get("path")},
        )

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                headers.append((b"x-trace-id", trace_id.encode()))
                message["headers"] = headers
            await send(message)

        # Note: context vars are intentionally NOT cleared here. The global
        # exception handler runs in ServerErrorMiddleware, outside this
        # middleware, and needs the IDs to still be set. Each request
        # overwrites them at the start, so no cross-request leakage occurs.
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            get_logger().info(
                "request_completed",
                extra={"method": scope.get("method"), "path": scope.get("path")},
            )


def _header(scope: Scope, name: str) -> str | None:
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return None