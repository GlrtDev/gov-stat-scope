from typing import Any, Dict

from langgraph.graph import END, START, StateGraph

from app.workflow.nodes.analyst import analyst_agent_node
from app.workflow.nodes.api_engineer import api_engineer_agent_node, route_after_api
from app.workflow.nodes.error_handler import error_handler_node
from app.workflow.nodes.router import route_after_intent, router_agent_node
from app.workflow.state import OrchestratorState

# Initialize Graph
workflow = StateGraph(OrchestratorState)

# Add Nodes
workflow.add_node("router", router_agent_node)
workflow.add_node("api_engineer", api_engineer_agent_node)
workflow.add_node("analyst", analyst_agent_node)
workflow.add_node("error_handler", error_handler_node)

# Define Edges
workflow.add_edge(START, "router")

workflow.add_conditional_edges(
    "router",
    route_after_intent,
    {
        "api_engineer": "api_engineer",
        "error_handler": "error_handler",
        END: END
    }
)

workflow.add_conditional_edges(
    "api_engineer",
    route_after_api,
    {
        "analyst": "analyst",
        "error_handler": "error_handler"
    }
)

workflow.add_edge("analyst", END)
workflow.add_edge("error_handler", END)

# Compile Graph
app_graph = workflow.compile()


async def invoke_workflow(query: str, session_id: str) -> Dict[str, Any]:
    """
    Entrypoint function to execute the LangGraph workflow.
    """
    initial_state = {
        "session_id": session_id,
        "user_query": query,
        "messages": [],
        "errors": [],
        "metadata": {}
    }
    
    result = await app_graph.ainvoke(initial_state)
    return result # type: ignore