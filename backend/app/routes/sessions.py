from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.workflow.graph import app_graph

router = APIRouter(prefix="/sessions", tags=["Sessions"])


class MessageResponse(BaseModel):
    role: str
    content: str


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: List[MessageResponse]
    selected_source: Optional[str] = None
    normalized_data: Optional[Dict[str, Any]] = None
    final_answer: Optional[str] = None
    metadata: Dict[str, Any]


@router.get("/{session_id}", response_model=SessionHistoryResponse)
async def get_session(session_id: str) -> SessionHistoryResponse:
    """Retrieves the complete state and memory for a given LangGraph session."""
    config = {"configurable": {"thread_id": session_id}}
    state = await app_graph.aget_state(config)
    
    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Session not found")
        
    values = state.values
    
    parsed_messages = []
    for msg in values.get("messages", []):
        role = "assistant" if msg.type == "ai" else "user" if msg.type == "human" else msg.type
        # Handle complex message structures if tool calls are embedded
        content = str(msg.content)
        parsed_messages.append(MessageResponse(role=role, content=content))
        
    return SessionHistoryResponse(
        session_id=values.get("session_id", session_id),
        messages=parsed_messages,
        selected_source=values.get("selected_source"),
        normalized_data=values.get("normalized_data"),
        final_answer=values.get("final_answer"),
        metadata=values.get("metadata", {})
    )