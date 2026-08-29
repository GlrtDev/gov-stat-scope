"""Unit tests for hardening-related pure logic."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import AskRequest, DataSource
from app.routes.orchestration import _normalize_selected_source


class TestAskRequestValidation:
    def test_valid_minimal_payload(self) -> None:
        payload = AskRequest.model_validate({"message": "test"})
        assert payload.message == "test"
        assert payload.session_id is not None  # Should auto-generate if missing

    def test_missing_message_fails(self) -> None:
        with pytest.raises(ValidationError):
            AskRequest.model_validate({})

    @pytest.mark.parametrize("bad_message", [123, None, ["x"], {"message": "x"}])
    def test_non_string_message_fails(self, bad_message: object) -> None:
        with pytest.raises(ValidationError):
            AskRequest.model_validate({"message": bad_message})

    @pytest.mark.parametrize("bad_session_id", [12345, ["x"], {"id": "x"}])
    def test_non_string_session_id_fails(self, bad_session_id: object) -> None:
        with pytest.raises(ValidationError):
            AskRequest.model_validate({
                "message": "valid",
                "session_id": bad_session_id,
            })


class TestNormalizeSelectedSource:
    @pytest.mark.parametrize("value", ["GUS", DataSource.GUS])
    def test_gus_is_normalized_to_enum(self, value: object) -> None:
        assert _normalize_selected_source(value) is DataSource.GUS

    @pytest.mark.parametrize("value", ["FRED", DataSource.FRED])
    def test_fred_is_normalized_to_enum(self, value: object) -> None:
        assert _normalize_selected_source(value) is DataSource.FRED

    @pytest.mark.parametrize("value", [None, "", "UNKNOWN", "gus", 123])
    def test_invalid_values_fall_back_to_unknown(self, value: object) -> None:
        assert _normalize_selected_source(value) == "unknown"