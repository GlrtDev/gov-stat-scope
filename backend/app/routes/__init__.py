"""API routes router aggregation module."""

from __future__ import annotations

from fastapi import APIRouter

from app.routes.health import router as health_router
from app.routes.sessions import router as sessions_router

router = APIRouter()
router.include_router(health_router)
router.include_router(sessions_router)