"""Contract tests validating Pydantic v2 models and Bedrock structured output schemas."""

from __future__ import annotations

import pytest
from app.models import AskRequest, AskResponse, ErrorResponse
from app.workflow.nodes.api_engineer import ApiEngineerOutput
from app.workflow.nodes.router import RouterOutput
from pydantic import ValidationError


def test_router_output_contract() -> None:
    """Assert valid and invalid JSON deserialization for RouterOutput."""
    valid_payload = {
        "selected_source": "GUS",
        "reason": "Identified Polish territorial data requirement.",
        "confidence": 0.95,
        "extracted_entities": {"metric": "stopa bezrobocia"},
    }
    instance = RouterOutput.model_validate(valid_payload)
    assert instance.selected_source == "GUS"
    assert instance.reason == "Identified Polish territorial data requirement."
    assert instance.confidence == 0.95
    assert instance.extracted_entities == {"metric": "stopa bezrobocia"}

    # Missing mandatory reason field
    with pytest.raises(ValidationError):
        RouterOutput.model_validate({"selected_source": "GUS", "confidence": 1.0})


def test_api_engineer_output_contract() -> None:
    """Assert valid schema enforcement for ApiEngineerOutput."""
    valid_payload = {
        "endpoint_target": "by-variable",
        "resolved_metric_id": "12345",
        "query_parameters": {"unit-level": "2", "year": ["2021", "2022"]},
        "justification": "Parameters mapped for regional GUS data retrieval.",
    }
    instance = ApiEngineerOutput.model_validate(valid_payload)
    assert instance.endpoint_target == "by-variable"
    assert instance.resolved_metric_id == "12345"
    assert instance.query_parameters["unit-level"] == "2"

    # Invalid type for parameters
    with pytest.raises(ValidationError):
        ApiEngineerOutput.model_validate({
            "endpoint_target": "by-variable",
            "resolved_metric_id": "12345",
            "query_parameters": "not-a-dict",
            "justification": "Invalid payload",
        })


def test_ask_request_contract() -> None:
    """Assert validation rules for API incoming payloads."""
    # Standard valid payload
    req = AskRequest(message="What is the GDP growth rate?", session_id="abc-123")
    assert req.message == "What is the GDP growth rate?"
    assert req.session_id == "abc-123"

    # Missing session_id is auto-generated, not rejected
    req_auto = AskRequest.model_validate({"message": "What is the GDP growth rate?"})
    assert isinstance(req_auto.session_id, str) and len(req_auto.session_id) > 0

    # Missing message
    with pytest.raises(ValidationError):
        AskRequest.model_validate({"session_id": "abc-123"})


def test_ask_response_contract() -> None:
    """Assert formatting and serialization rules for API responses."""
    resp = AskResponse(
        answer="Poland GDP grew by 5.3% in 2022.",
        source="GUS",
        metadata={
            "session_id": "sess-456",
            "request_id": "req-789",
            "trace_id": "trace-101",
        },
    )
    dumped = resp.model_dump()
    assert dumped["answer"] == "Poland GDP grew by 5.3% in 2022."
    assert dumped["source"] == "GUS"
    assert dumped["metadata"]["request_id"] == "req-789"


def test_error_response_contract() -> None:
    """Assert structured JSON error format."""
    err = ErrorResponse(
        error="internal_server_error",
        detail="DynamoDB connection timed out.",
        request_id="req-123",
        trace_id="trace-456",
    )
    assert err.error == "internal_server_error"
    assert err.detail == "DynamoDB connection timed out."
    assert err.request_id == "req-123"