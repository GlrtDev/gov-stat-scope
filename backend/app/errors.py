"""Global exception handlers converting failures into structured JSON errors."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.context import get_request_id, get_trace_id
from app.logging_config import get_logger
from models import ErrorResponse


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch unhandled exceptions and return a structured JSON error."""
    get_logger().exception(
        "unhandled_exception",
        extra={"path": request.url.path, "error_type": type(exc).__name__},
    )
    body = ErrorResponse(
        error="internal_server_error",
        detail=str(exc) or None,
        request_id=get_request_id(),
        trace_id=get_trace_id(),
    )
    return JSONResponse(status_code=500, content=body.model_dump())