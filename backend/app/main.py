"""FastAPI application entrypoint with lifecycle, middleware, and routers."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.rate_limiter import limiter, rate_limit_error_handler
from app.routes import router as core_router
from app.routes.health import router as health_router
from app.routes.sessions import router as sessions_router
from app.services.secrets_client import AsyncSecretsClient
from app.storage.dynamodb_saver import init_dynamodb_tables

configure_logging()
logger = logging.getLogger(__name__)


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Enforce a global request timeout to prevent hanging connections."""
    def __init__(self, app: FastAPI, timeout: int = 30) -> None:
        super().__init__(app)
        self.timeout = timeout

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            async with asyncio.timeout(self.timeout):
                return await call_next(request)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=408, content={"detail": "Request Timeout"})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifecycle manager handling resource initialization and graceful shutdown."""
    region_name = os.getenv("AWS_REGION", "us-east-1")
    app.state.http_client = httpx.AsyncClient()

    if os.getenv("ENVIRONMENT") == "production":
        logger.info("Production environment detected. Fetching secrets from AWS Secrets Manager.")
        secrets_client = AsyncSecretsClient(region_name=region_name)
        try:
            gus_secret = await secrets_client.get_secret("govdata/gus-api-key")
            fred_secret = await secrets_client.get_secret("govdata/fred-api-key")
            for key, value in {**gus_secret, **fred_secret}.items():
                os.environ[key] = str(value)
            logger.info("Successfully injected production API keys from Secrets Manager.")
        except Exception as e:
            logger.critical(f"Failed to load required secrets from AWS: {e}")
            raise

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


app = FastAPI(
    title="GovStatScope AI Orchestrator",
    description="Stateful multi-agent orchestration API for government data sources.",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_error_handler)

app.add_middleware(TimeoutMiddleware, timeout=30)
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Replace the placeholder core_router health check by overriding it
app.include_router(core_router)
app.include_router(health_router)
app.include_router(sessions_router)