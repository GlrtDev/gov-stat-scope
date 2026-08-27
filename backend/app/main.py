"""FastAPI entrypoint for the GovData AI Orchestrator.

Phase 1: assembles the FastAPI application from focused building blocks
rather than defining everything inline:

* ``app.logging_config`` — structured key=value logging
* ``app.middleware`` — request/trace ID propagation (pure ASGI middleware)
* ``app.errors`` — global exception handler -> structured JSON errors
* ``app.api`` — public endpoints (GET /health, POST /ask)

This module only wires those pieces together.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import api, errors, middleware
from app.logging_config import configure_logging

configure_logging()

app = FastAPI(
    title="GovData AI Orchestrator",
    version="0.1.0",
    description="Multi-agent orchestrator over GUS and FRED government data.",
)

app.add_middleware(middleware.RequestContextMiddleware)
app.add_exception_handler(Exception, errors.unhandled_exception_handler)
app.include_router(api.router)