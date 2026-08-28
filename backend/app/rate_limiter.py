"""Rate limiting engine configured for IP and session-based throttling."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def get_rate_limit_key(request: Request) -> str:
    """Extract session ID header or fallback to client IP address."""
    return request.headers.get("x-session-id") or get_remote_address(request)


limiter = Limiter(key_func=get_rate_limit_key)


def rate_limit_error_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Format RFC-compliant rate limit error payload."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "RateLimitExceeded",
            "message": f"Rate limit exceeded: {exc.detail}",
            "path": request.url.path,
        },
    )