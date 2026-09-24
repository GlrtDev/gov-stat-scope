import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel



def get_llm(temperature: float = 0.0) -> BaseChatModel:
    """
    Instantiates the LLM based on environment configuration.
    Defaults to Amazon Bedrock (Nova Lite via EU inference profile).

    Environment Variables:
    - LLM_PROVIDER: 'bedrock', 'openai', 'openrouter', or 'gemini'
    - BEDROCK_MODEL_ID: Bedrock model / inference-profile ID (preferred)
    - LLM_MODEL: Generic model identifier override
    - OPENAI_API_KEY: Required for openai/openrouter
    - OPENAI_API_BASE: Required for openrouter (e.g., https://openrouter.ai/api/v1)
    - GOOGLE_API_KEY: Required for gemini
    """
    provider = os.getenv("LLM_PROVIDER", "bedrock").lower()
    model_name = (
        os.getenv("BEDROCK_MODEL_ID")
        or os.getenv("LLM_MODEL")
        or DEFAULT_BEDROCK_MODEL_ID
    )

    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(
            model=model_name,
            temperature=temperature,
            region_name=os.getenv("AWS_REGION", "eu-north-1"),
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