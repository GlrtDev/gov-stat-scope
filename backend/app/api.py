"""HTTP endpoints: the liveness probe and the chat/ask contract."""

from __future__ import annotations

from fastapi import APIRouter

from app.context import get_request_id, get_trace_id
from app.logging_config import get_logger
from models import AskRequest, AskResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    """Accept a user query and return an answer.

    Phase 1: returns a dummy/mock response. The LangGraph workflow will
    replace this in later phases.
    """
    get_logger().info(
        "ask_received",
        extra={"session_id": payload.session_id, "message_length": len(payload.message)},
    )
    return AskResponse(
        answer=(
            f"Mock response for: {payload.message!r}. "
            "The orchestrator workflow is not wired up yet."
        ),
        source="unknown",
        metadata={
            "session_id": payload.session_id,
            "phase": "phase1-mock",
            "request_id": get_request_id(),
            "trace_id": get_trace_id(),
        },
    )