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
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.errors import add_exception_handlers
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.rate_limiter import limiter
from app.routes import router as core_router
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
            return JSONResponse(
                status_code=408,
                content={
                    "type": "urn:govdata:error:timeout",
                    "title": "Request Timeout",
                    "status": 408,
                    "detail": "The server timed out waiting for the request to complete.",
                    "instance": str(request.url)
                },
                media_type="application/problem+json"
            )


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

app.add_middleware(TimeoutMiddleware, timeout=30)
app.add_middleware(RequestContextMiddleware)

# Secure CORS configuration supporting local development, AWS cloud previews, and production domains
ALLOWED_ORIGIN_REGEXES = [
    r"http://localhost(:\d+)?",
    r"http://127\.0\.0\.1(:\d+)?",
    r"https://.*\.amazonaws\.com",
    r"https://(www\.)?govstatscope\.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex="|".join(ALLOWED_ORIGIN_REGEXES),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core_router)