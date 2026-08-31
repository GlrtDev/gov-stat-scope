# backend/app/workflow/state.py
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class OrchestratorState(TypedDict):
    """Represents the state of the LangGraph workflow for the GovData AI Orchestrator."""

    session_id: str
    user_query: str
    messages: Annotated[List[AnyMessage], add_messages]
    selected_source: Optional[str]
    api_plan: Optional[Dict[str, Any]]
    raw_response: Optional[Dict[str, Any]]
    normalized_data: Optional[Dict[str, Any]]
    analysis_result: Optional[Dict[str, Any]]
    final_answer: Optional[str]
    errors: Annotated[List[str], operator.add]
    metadata: Dict[str, Any]
