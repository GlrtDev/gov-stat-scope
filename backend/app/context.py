"""Request-scoped trace context (request_id / trace_id).

These IDs are generated in the request middleware, consumed by the logging
filter, the error handler, and the endpoints, and echoed back to callers in
response headers and payloads. They are held in ContextVars so each request
(and each spawned async task) sees its own values without explicit threading
through every call site.
"""

from __future__ import annotations

from contextvars import ContextVar

_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_request_context(request_id: str, trace_id: str) -> None:
    """Bind the IDs for the current request to this async context."""
    _request_id_ctx.set(request_id)
    _trace_id_ctx.set(trace_id)


def get_request_id() -> str | None:
    """Return the current request ID, or None if no request is active."""
    return _request_id_ctx.get()


def get_trace_id() -> str | None:
    """Return the current trace ID, or None if no request is active."""
    return _trace_id_ctx.get()
