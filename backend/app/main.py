# backend/app/main.py
"""FastAPI application entrypoint with lifecycle, middleware, and routers."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.errors import add_exception_handlers
from app.ddos import DDoSPreventionMiddleware, SecurityHeadersMiddleware
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.rate_limiter import limiter
from app.routes import router as core_router
from app.services.secrets_client import AsyncSecretsClient
from app.storage.dynamodb_saver import init_dynamodb_tables

configure_logging()
logger = logging.getLogger(__name__)

class TimeoutMiddleware:
    """Pure ASGI timeout: no BaseHTTPMiddleware (buffers SSE frames).
    Streaming endpoints are exempt — the Lambda timeout (300s) governs them."""

    EXEMPT_PATHS: tuple[str, ...] = ("/api/v1/ask/stream",)

    def __init__(self, app: ASGIApp, timeout: int = 120) -> None:
        self.app = app
        self.timeout = timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "") in self.EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            async with asyncio.timeout(self.timeout):
                await self.app(scope, receive, send_wrapper)
        except TimeoutError:
            if response_started:
                raise
            response = JSONResponse(
                status_code=408,
                content={
                    "type": "urn:govdata:error:timeout",
                    "title": "Request Timeout",
                    "status": 408,
                    "detail": f"The server timed out waiting for the request to complete after {self.timeout} seconds.",
                    "instance": scope.get("path", ""),
                },
                media_type="application/problem+json",
            )
            await response(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifecycle manager handling resource initialization and graceful shutdown."""
    region_name = os.getenv("AWS_REGION", "eu-north-1")
    app.state.http_client = httpx.AsyncClient()

    # API keys are optional: GUS BDL works unauthenticated and FRED-backed
    # queries degrade at request time via the workflow's error handling.
    missing_keys = [key for key in ("GUS_API_KEY", "FRED_API_KEY") if not os.getenv(key)]
    if missing_keys:
        logger.warning(
            "Optional data-source API keys not set: %s. Related adapters will fail per-request.",
            ", ".join(missing_keys),
        )

    table_name = os.getenv("DYNAMODB_TABLE_NAME", "govdata-sessions")
    await init_dynamodb_tables(table_name=table_name, region_name=region_name)

    yield

    logger.info("Initiating graceful shutdown...")
    if hasattr(app.state, "http_client"):
        await app.state.http_client.aclose()
        logger.info("HTTP client sessions closed.")

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()

    logger.info(f"Cancelling {len(tasks)} outstanding background tasks.")
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Connections drained. Shutdown complete.")


tags_metadata = [
    {"name": "Orchestration", "description": "LangGraph multi-agent orchestration endpoints."},
    {"name": "Health", "description": "ECS Liveness and AWS Readiness probes."},
    {"name": "Sessions", "description": "DynamoDB conversational state retrieval."}
]

app = FastAPI(
    title="GovStatScope AI Orchestrator",
    description="Stateful multi-agent orchestration API for government data sources.",
    version="1.0.0",
    contact={"name": "GovData API Team", "url": "https://github.com/GlrtDev/gov-stat-scope"},
    license_info={"name": "MIT License", "url": "https://opensource.org/licenses/MIT"},
    openapi_tags=tags_metadata,
    servers=[
        {"url": "http://localhost:8000", "description": "Local development environment"},
        {"url": "https://<YOUR_ALB_DNS_NAME>.elb.amazonaws.com", "description": "Production AWS Environment"}
    ],
    lifespan=lifespan,
)


# Apply global exception handlers
add_exception_handlers(app)

# Middlewares and Router bindings
app.state.limiter = limiter

REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "120"))
logger.info(f"Request timeout configured: {REQUEST_TIMEOUT_SECONDS}s")

app.add_middleware(TimeoutMiddleware, timeout=REQUEST_TIMEOUT_SECONDS)
app.add_middleware(RequestContextMiddleware)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Order matters: last added runs first, so DDoS guard runs outermost
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    DDoSPreventionMiddleware,
    blocked_ips=set(),      
    allowed_ips=set(), # for admin routes
    redis=None, 
)

app.include_router(core_router)
