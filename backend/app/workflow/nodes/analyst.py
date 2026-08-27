from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.workflow.llm_factory import get_llm
from app.workflow.state import OrchestratorState


class AnalystOutput(BaseModel):
    final_answer: str = Field(
        description="Natural language summary of the data, answering the user's query with source and time range citations."
    )
    key_metrics: List[Dict[str, Any]] = Field(
        description="List of key data points or milestones extracted from the normalized data."
    )
    calculations_performed: List[str] = Field(
        description="List of text descriptions of any calculations performed (e.g., 'Calculated year-over-year percentage change')."
    )
    confidence_notes: str = Field(
        description="Any caveats regarding data gaps, anomalies, or assumptions made during analysis."
    )


async def analyst_agent_node(state: OrchestratorState) -> Dict[str, Any]:
    llm = get_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(AnalystOutput)
    
    system_prompt = (
        "You are the Data Analyst Agent for the GovStatScope AI Orchestrator. "
        "Your task is to analyze the provided normalized government data to answer the user's query. "
        "Calculate trends, differences, averages, or percentages where applicable. "
        "Your final answer must be highly readable, cite the specific data source (GUS or FRED), "
        "and mention the exact time range of the data used."
    )
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"User Query: {state['user_query']}\n\nNormalized Data: {state['normalized_data']}")
    ]
    
    result: AnalystOutput = await structured_llm.ainvoke(messages) # type: ignore
    
    ai_message = AIMessage(content=result.final_answer)
    
    return {
        "analysis_result": result.model_dump(),
        "final_answer": result.final_answer,
        "messages": [ai_message]
    }