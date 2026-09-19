"""HTTP endpoints executing the LangGraph orchestration pipeline."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.context import get_request_id, get_trace_id, set_session_id
from app.logging_config import get_logger
from app.models import AskRequest, AskResponse, DataSource
from app.rate_limiter import limiter
from app.workflow.graph import invoke_workflow
from app.workflow.progress import register_progress_queue, unregister_progress_queue
from app.services.llm_quota import DynamoDBLLMQuota, LLMQuotaExceeded

router = APIRouter(prefix="/api/v1", tags=["Orchestration"])

llm_quota = DynamoDBLLMQuota()

def _client_scope(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    return f"ip:{client_ip}"

@router.post("/ask", response_model=AskResponse)
@limiter.limit("10/minute")
async def ask(request: Request, payload: AskRequest) -> AskResponse:
    scope_id = _client_scope(request)
    quota_check = await llm_quota.check_and_increment_async(scope_id)
    if not quota_check["allowed"]:
        raise LLMQuotaExceeded(limit=quota_check["limit"], used=quota_check["used"])

    session_id = payload.session_id or uuid.uuid4().hex
    set_session_id(session_id)

    get_logger().info(
        "ask_received",
        extra={
            "session_id": session_id,
            "message_length": len(payload.message),
            "forced_source": payload.data_source,
        },
    )
    try:
        result: dict[str, Any] = await invoke_workflow(
            query=payload.message,
            session_id=session_id,
            forced_source=payload.data_source,
        )
    except LLMQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

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


@router.post("/ask/stream")
@limiter.limit("5/minute")
async def ask_stream(request: Request, payload: AskRequest):
    """Stream progress events while executing the workflow via SSE."""
    session_id = payload.session_id or uuid.uuid4().hex
    set_session_id(session_id)  # propagate to bedrock_client for quota
    queue = register_progress_queue(session_id)

    async def event_generator():
        try:
            task = asyncio.create_task(
                invoke_workflow(
                    query=payload.message,
                    session_id=session_id,
                    forced_source=payload.data_source,
                )
            )
            yield f"event: started\ndata: {json.dumps({'session_id': session_id})}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if task.done():
                        try:
                            result = task.result()
                        except LLMQuotaExceeded as exc:
                            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                            break
                        safe_payload = {
                            "session_id": session_id,
                            "final_answer": result.get("final_answer"),
                            "selected_source": result.get("selected_source"),
                            "errors": result.get("errors", []),
                            "analysis_result": result.get("analysis_result"),
                        }
                        yield f"event: done\ndata: {json.dumps({'final': safe_payload})}\n\n"
                        break
                    continue
                yield f"event: progress\ndata: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        finally:
            unregister_progress_queue(session_id)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _normalize_selected_source(value: Any) -> DataSource | str:
    if value == DataSource.GUS or value == "GUS":
        return DataSource.GUS

    if value == DataSource.FRED or value == "FRED":
        return DataSource.FRED

    return "unknown"