from typing import Any, Dict

from langchain_core.messages import AIMessage

from app.workflow.state import OrchestratorState


async def error_handler_node(state: OrchestratorState) -> Dict[str, Any]:
    """
    Extracts the latest error from the state and generates a user-friendly termination message.
    """
    errors = state.get("errors", [])
    
    if errors:
        latest_error = errors[-1]
        error_details = f"The system encountered an error: {latest_error}"
    else:
        error_details = "An unknown error occurred while processing your request."

    final_message_content = (
        f"I'm sorry, I couldn't complete your request. {error_details} "
        "Please try rephrasing your query or checking the available data sources."
    )
    
    ai_message = AIMessage(content=final_message_content)

    return {
        "messages": [ai_message],
        "final_answer": final_message_content
    }