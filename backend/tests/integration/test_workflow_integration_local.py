import os
from typing import Generator

import httpx
import pytest
from pytest import MonkeyPatch

from app.workflow.graph import invoke_workflow


def is_lm_studio_available() -> bool:
    """Checks if LM Studio is running and accessible from the test container."""
    try:
        response = httpx.get("http://host.docker.internal:1234/v1/models", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


# Skip all tests in this file if LM Studio is offline
pytestmark = pytest.mark.skipif(
    not is_lm_studio_available(),
    reason="LM Studio is not reachable on http://host.docker.internal:1234. Skipping local LLM integration tests."
)


@pytest.fixture(autouse=True)
def setup_lm_studio_env(monkeypatch: MonkeyPatch) -> Generator[None, None, None]:
    """
    Forces the workflow to use the local LM Studio instance for integration testing.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-27b")
    monkeypatch.setenv("OPENAI_API_BASE", "http://host.docker.internal:1234/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "lm-studio")
    
    # Ensure FRED API key exists for FRED-specific tests, fallback to dummy to prevent crash
    if not os.getenv("FRED_API_KEY"):
        monkeypatch.setenv("FRED_API_KEY", "dummy_key_for_test_skip")
        
    yield


@pytest.mark.asyncio
async def test_workflow_gus_routing_and_analysis() -> None:
    """
    Tests if a Poland-specific query correctly routes to GUS, fetches data, and analyzes it.
    """
    query = "What is the population of Poland over the last few years?"
    session_id = "test-session-gus-001"
    
    result = await invoke_workflow(query, session_id)
    
    assert result["selected_source"] == "GUS"
    assert result["final_answer"] is not None
    assert len(result["final_answer"]) > 0
    assert "error" not in [err.lower() for err in result.get("errors", [])]
    assert result["normalized_data"] is not None
    assert result["analysis_result"] is not None


@pytest.mark.asyncio
async def test_workflow_fred_routing_and_analysis() -> None:
    """
    Tests if a US-specific macroeconomic query correctly routes to FRED.
    Skips if no valid FRED_API_KEY is present in the host environment.
    """
    if os.getenv("FRED_API_KEY") == "dummy_key_for_test_skip":
        pytest.skip("FRED_API_KEY not set. Skipping live FRED workflow test.")
        
    query = "What is the current US GDP and how has it changed recently?"
    session_id = "test-session-fred-001"
    
    result = await invoke_workflow(query, session_id)
    
    assert result["selected_source"] == "FRED"
    assert result["final_answer"] is not None
    assert len(result["final_answer"]) > 0
    assert "error" not in [err.lower() for err in result.get("errors", [])]
    assert result["normalized_data"] is not None
    assert result["analysis_result"] is not None


@pytest.mark.asyncio
async def test_workflow_unsupported_routing() -> None:
    """
    Tests if an out-of-scope query triggers the UNSUPPORTED route and error handler.
    """
    query = "Can you give me a recipe for chocolate chip cookies?"
    session_id = "test-session-unsupported-001"
    
    result = await invoke_workflow(query, session_id)
    
    assert result["selected_source"] == "UNSUPPORTED"
    assert len(result["errors"]) > 0
    assert "unsupported" in result["errors"][-1].lower() or "ambiguous" in result["errors"][-1].lower()
    assert result["final_answer"] is not None
    assert "sorry" in result["final_answer"].lower()