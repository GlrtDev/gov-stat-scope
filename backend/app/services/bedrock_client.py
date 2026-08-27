import logging
from typing import Any, Dict, List, Tuple

import aioboto3
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TokenUsage(BaseModel):
    input_tokens: int = Field(default=0, description="Number of tokens in the prompt")
    output_tokens: int = Field(default=0, description="Number of tokens generated")
    estimated_cost: float = Field(default=0.0, description="Estimated cost in USD")


class AsyncBedrockClient:
    """
    Native async client for Amazon Bedrock using the Converse API.
    Supports dynamic pricing based on model_id.
    """
    
    PRICING_MAP = {
        "anthropic.claude-3-haiku-20240307-v1:0": {"input": 0.25, "output": 1.25},
        "gemma-4-31b": {"input": 0.10, "output": 0.20},  # Estimated Bedrock pricing
        "default": {"input": 0.0, "output": 0.0}         # Local/Free fallback
    }

    def __init__(self, region_name: str = "us-east-1") -> None:
        self.session = aioboto3.Session(region_name=region_name)

    async def invoke_model(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        model_id: str = "gemma-4-31b"
    ) -> Tuple[str, TokenUsage]:
        """
        Invokes a Bedrock model, calculates usage costs dynamically, and returns the text response.
        """
        pricing = self.PRICING_MAP.get(model_id, self.PRICING_MAP["default"])
        input_price = pricing["input"]
        output_price = pricing["output"]

        system_prompts = [{"text": system}] if system else []

        async with self.session.client("bedrock-runtime") as client:
            response = await client.converse(
                modelId=model_id,
                messages=messages,
                system=system_prompts
            )

        output_message = response["output"]["message"]
        content = output_message["content"][0]["text"]

        usage = response["usage"]
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        estimated_cost = (input_tokens / 1_000_000 * input_price) + \
                         (output_tokens / 1_000_000 * output_price)

        token_usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost
        )

        logger.info(
            f"Bedrock invocation complete | Model: {model_id} | "
            f"Input: {input_tokens} | Output: {output_tokens} | Cost: ${estimated_cost:.6f}"
        )

        return content, token_usage