"""RFC 7807 standardized exception handlers with trace context injection."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException

from app.context import get_request_id, get_trace_id
from app.logging_config import get_logger
from app.rate_limiter import limiter


def _build_problem_payload(
    request: Request,
    error_type: str,
    title: str,
    status: int,
    detail: str,
    **kwargs: Any
) -> dict[str, Any]:
    """Constructs an RFC 7807 compliant error payload with observability trace IDs."""
    payload = {
        "type": f"urn:govdata:error:{error_type}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": str(request.url),
        "request_id": get_request_id(),
        "trace_id": get_trace_id(),
    }
    payload.update(kwargs)
    return payload


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    get_logger().warning("request_validation_failed", extra={"path": request.url.path})
    payload = _build_problem_payload(
        request,
        error_type="validation",
        title="Request Validation Failed",
        status=422,
        detail="The request body or parameters are invalid.",
        errors=jsonable_encoder(exc.errors())
    )
    return JSONResponse(status_code=422, content=payload, media_type="application/problem+json")


async def rate_limit_error_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    get_logger().warning("rate_limit_exceeded", extra={"path": request.url.path, "detail": str(exc.detail)})
    payload = _build_problem_payload(
        request,
        error_type="rate-limit",
        title="Rate Limit Exceeded",
        status=429,
        detail=str(exc.detail)
    )
    return JSONResponse(status_code=429, content=payload, media_type="application/problem+json")


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    get_logger().warning("http_exception", extra={"path": request.url.path, "status_code": exc.status_code})
    payload = _build_problem_payload(
        request,
        error_type=f"http-{exc.status_code}",
        title="HTTP Error",
        status=exc.status_code,
        detail=str(exc.detail)
    )
    return JSONResponse(status_code=exc.status_code, content=payload, media_type="application/problem+json")


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    get_logger().exception(
        "unhandled_exception",
        extra={"path": request.url.path, "error_type": type(exc).__name__},
    )
    payload = _build_problem_payload(
        request,
        error_type="internal-server-error",
        title="Internal Server Error",
        status=500,
        detail="An unexpected error occurred while processing the request."
    )
    return JSONResponse(status_code=500, content=payload, media_type="application/problem+json")


def add_exception_handlers(app: FastAPI) -> None:
    """Registers all global exception handlers to the FastAPI application instance."""
    app.state.limiter = limiter

    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
