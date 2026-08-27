"""Core domain models and API contract for the GovData AI Orchestrator.

Phase 1: defines the Pydantic models shared by the FastAPI layer and the
LangGraph workflow, plus the public request/response schemas.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DataSource(str, Enum):
    """Supported government data sources."""

    GUS = "GUS"
    FRED = "FRED"


class ChatRole(str, Enum):
    """Role of a message in a conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    role: ChatRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class UserQuery(BaseModel):
    """A normalized user query extracted from the raw message."""

    raw_text: str
    session_id: str
    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ApiRequestPlan(BaseModel):
    """The concrete API request plan produced by the API Engineer agent."""

    source: DataSource
    endpoint: str
    params: dict[str, Any] = Field(default_factory=dict)
    metric_name: str | None = None
    region: str | None = None
    time_range: tuple[str, str] | None = None


class RawApiResponse(BaseModel):
    """The raw, unvalidated response received from a data source."""

    source: DataSource
    status_code: int
    body: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class AnalyzedResult(BaseModel):
    """Structured output of the Data Analyst agent."""

    final_answer: str
    key_metrics: list[dict[str, Any]] = Field(default_factory=list)
    calculations_performed: list[str] = Field(default_factory=list)
    data_source: DataSource
    confidence_notes: str | None = None


class OrchestratorState(BaseModel):
    """Immutable state passed between LangGraph nodes.

    State transitions happen only through explicit LangGraph node updates;
    this model is never mutated in place.
    """

    model_config = {"frozen": True}

    session_id: str
    user_query: UserQuery
    messages: list[ChatMessage] = Field(default_factory=list)
    selected_source: DataSource | None = None
    api_plan: ApiRequestPlan | None = None
    raw_response: RawApiResponse | None = None
    normalized_data: list[dict[str, Any]] = Field(default_factory=list)
    analysis_result: AnalyzedResult | None = None
    final_answer: str | None = None
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API contract
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    """Request body for POST /ask."""

    message: str = Field(..., min_length=1, description="User message text")
    session_id: str = Field(..., min_length=1, description="Conversation session identifier")


class AskResponse(BaseModel):
    """Response body for POST /ask."""

    answer: str
    source: DataSource | Literal["unknown"] = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Structured error body returned by the global error handler."""

    error: str
    detail: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
