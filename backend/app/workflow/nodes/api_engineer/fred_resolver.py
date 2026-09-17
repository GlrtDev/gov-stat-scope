"""FRED resolution stub: returns a user-facing notice since FRED is not implemented yet."""
from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.messages import AIMessage, AnyMessage

from app.workflow.nodes.api_engineer.types import ResolutionResult
from app.workflow.progress import push_progress


async def _run_fred_resolution(
    _raw_text: str,
    session_id: str,
) -> ResolutionResult:
    """FRED integration is not implemented; report that to the user."""
    message = "Sorry, your query looks like it's for FRED, which is not implemented yet."
    await push_progress(session_id, {"type": "error", "message": message})

    node_errors: List[str] = [message]
    node_messages: List[AnyMessage] = [AIMessage(content=message)]
    resolved_context: Dict[str, Any] = {
        "source": "FRED",
        "series_id": None,
    }
    return None, node_errors, node_messages, resolved_context