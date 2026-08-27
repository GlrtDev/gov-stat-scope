import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel


def get_llm(temperature: float = 0.0) -> BaseChatModel:
    """
    Instantiates the LLM based on environment configuration.
    Defaults to Amazon Bedrock (Claude Haiku) if no provider is specified.
    
    Environment Variables:
    - LLM_PROVIDER: 'bedrock', 'openai', 'openrouter', or 'gemini'
    - LLM_MODEL: Model identifier string
    - OPENAI_API_KEY: Required for openai/openrouter
    - OPENAI_API_BASE: Required for openrouter (e.g., https://openrouter.ai/api/v1)
    - GOOGLE_API_KEY: Required for gemini
    """
    provider = os.getenv("LLM_PROVIDER", "bedrock").lower()
    model_name = os.getenv("LLM_MODEL", "anthropic.claude-3-haiku-20240307-v1:0")

    if provider == "bedrock":
        from langchain_aws import ChatBedrock
        return ChatBedrock(
            model_id=model_name,
            model_kwargs={"temperature": temperature},
        )
        
    elif provider in ("openai", "openrouter"):
        from langchain_openai import ChatOpenAI
        api_base = os.getenv("OPENAI_API_BASE")
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            base_url=api_base,
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )
        
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")