"""Intent router node for data source classification."""

from typing import Any, Dict, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from pydantic import BaseModel, Field

from app.models import DataSource
from app.workflow.llm_factory import get_llm


class RouterOutput(BaseModel):
    selected_source: Literal["GUS", "FRED", "UNSUPPORTED"] = Field(
        description="The target data source based on the user query."
    )
    confidence: float = Field(
        description="Confidence score of the routing decision between 0.0 and 1.0."
    )
    extracted_entities: Dict[str, Any] = Field(
        default_factory=dict,
        description="Key entities extracted from the query such as location, metric, or time period."
    )
    reason: str = Field(
        description="Explanation of why this source was selected."
    )


def _extract_query_string(user_query: Any) -> str:
    """Safely extract the raw query string."""
    if isinstance(user_query, str):
        return user_query
    if isinstance(user_query, dict):
        return str(user_query.get("raw_text") or user_query.get("query") or "")
    return str(getattr(user_query, "raw_text", user_query))


async def router_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    current_source = state.get("selected_source")
    if current_source and current_source not in (DataSource.UNKNOWN, "UNKNOWN", None):
        return {
            "messages": [
                AIMessage(content=f"LLM routing bypassed. Explicit UI selection: {current_source}.")
            ]
        }

    raw_text = _extract_query_string(state.get("user_query", ""))

    llm = get_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(RouterOutput)

    system_prompt = (
        "You are the Intent and Routing Agent for the GovStatScope AI Orchestrator. "
        "Analyze the user query and route it to the correct government data source using these strict rules:\n"
        "1. Route to 'GUS' if the query contains references to Poland, Polish regions, Polish cities, "
        "or Polish economic/demographic metric terms.\n"
        "2. Route to 'FRED' if the query contains references to the US, United States, macroeconomics, "
        "FRED, CPI, GDP, or US unemployment rates.\n"
        "3. Route to 'UNSUPPORTED' if the query asks for data outside these bounds, asks for personal advice, "
        "or cannot be clearly resolved to Poland or the US."
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=raw_text)
    ]

    result: RouterOutput = await structured_llm.ainvoke(messages)  # type: ignore

    ai_message = AIMessage(
        content=f"Routing decision made: {result.selected_source}. Reason: {result.reason}"
    )

    node_errors = []
    if result.selected_source == "UNSUPPORTED":
        node_errors.append(f"Query '{raw_text}' is out of scope or ambiguous: {result.reason}")

    metadata = dict(state.get("metadata", {}))
    metadata["router"] = {
        "confidence": result.confidence,
        "extracted_entities": result.extracted_entities,
        "reason": result.reason
    }

    existing_errors = list(state.get("errors", []))
    final_errors = existing_errors + node_errors

    return {
        "selected_source": result.selected_source,
        "metadata": metadata,
        "messages": [ai_message],
        "errors": final_errors
    }


def route_after_intent(state: dict[str, Any]) -> str:
    selected = state.get("selected_source")
    normalized_source = selected.value if hasattr(selected, "value") else str(selected)

    if normalized_source in ("GUS", "FRED"):
        return "api_engineer"
    elif normalized_source == "UNSUPPORTED":
        return "error_handler"
    
    return END