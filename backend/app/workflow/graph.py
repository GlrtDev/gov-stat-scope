import os
from typing import Any, Dict

from langgraph.graph import END, START, StateGraph

from app.storage.dynamodb_saver import DynamoDBSaver
from app.workflow.nodes.analyst import analyst_agent_node
from app.workflow.nodes.api_engineer import api_engineer_agent_node, route_after_api
from app.workflow.nodes.error_handler import error_handler_node
from app.workflow.nodes.router import route_after_intent, router_agent_node
from app.workflow.state import OrchestratorState

# Initialize global checkpointer
table_name = os.getenv("DYNAMODB_TABLE_NAME", "govdata-sessions")
memory = DynamoDBSaver(table_name=table_name)

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

# Compile Graph with DynamoDB persistence
app_graph = workflow.compile(checkpointer=memory)


async def invoke_workflow(query: str, session_id: str) -> Dict[str, Any]:
    """
    Entrypoint function to execute the LangGraph workflow with session persistence.
    """
    config = {"configurable": {"thread_id": session_id}}
    
    # We provide only the updated fields. The checkpointer merges these with existing state.
    input_state = {
        "session_id": session_id,
        "user_query": query,
        "errors": [] # Clear transient errors on new turns
    }
    
    result = await app_graph.ainvoke(input_state, config=config)
    return result # type: ignore