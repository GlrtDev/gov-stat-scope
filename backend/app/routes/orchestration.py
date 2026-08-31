"""HTTP endpoints executing the LangGraph orchestration pipeline."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request

from app.context import get_request_id, get_trace_id
from app.logging_config import get_logger
from app.models import AskRequest, AskResponse, DataSource
from app.rate_limiter import limiter
from app.workflow.graph import invoke_workflow


router = APIRouter(prefix="/api/v1", tags=["Orchestration"])


@router.post("/ask", response_model=AskResponse)
@limiter.limit("30/minute")
async def ask(request: Request, payload: AskRequest) -> AskResponse:
    """Accept a user query and execute the LangGraph orchestrator workflow."""
    session_id = payload.session_id or uuid.uuid4().hex

    get_logger().info(
        "ask_received",
        extra={
            "session_id": session_id,
            "message_length": len(payload.message),
            "forced_source": payload.data_source,
        },
    )

    # Pass the explicitly selected data_source to bypass LangGraph's router node
    result: dict[str, Any] = await invoke_workflow(
        query=payload.message, 
        session_id=session_id,
        forced_source=payload.data_source
    )

    return AskResponse(
        answer=result.get("final_answer", "No analysis result was produced."),
        source=_normalize_selected_source(result.get("selected_source")),
        metadata={
            "session_id": session_id,
            "errors": result.get("errors", []),
            "analysis_result": result.get("analysis_result"),
            "request_id": get_request_id(),
            "trace_id": get_trace_id(),
        },
    )


def _normalize_selected_source(value: Any) -> DataSource | str:
    if value == DataSource.GUS or value == "GUS":
        return DataSource.GUS

    if value == DataSource.FRED or value == "FRED":
        return DataSource.FRED

    return "unknown"