"""Synchronous Boto3 Bedrock client wrapped for non-blocking execution via thread pools."""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable, Dict, List, Tuple, Type, TypeVar

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field, ValidationError, create_model
from starlette.concurrency import run_in_threadpool
from tenacity import retry, retry_if_exception_type, stop_after_attempt

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class BedrockStructuredOutputError(Exception):
    """Raised when the LLM fails to return valid JSON matching the schema after retries."""
    pass


class TokenUsage(BaseModel):
    input_tokens: int = Field(default=0, description="Number of tokens in the prompt")
    output_tokens: int = Field(default=0, description="Number of tokens generated")
    estimated_cost: float = Field(default=0.0, description="Estimated cost in USD")


class AsyncBedrockClient:
    """
    Async-compatible Bedrock client wrapping boto3 calls in Starlette thread pools.
    """

    PRICING_MAP = {
        "anthropic.claude-3-haiku-20240307-v1:0": {"input": 0.25, "output": 1.25},
        "gemma-4-31b": {"input": 0.10, "output": 0.20},
        "default": {"input": 0.0, "output": 0.0},
    }

    def __init__(self, region_name: str = "us-east-1") -> None:
        self.region_name = region_name

    async def _invoke_converse(self, **kwargs: Any) -> Dict[str, Any]:
        """Wraps the Boto3 Bedrock Converse API with outage and throttling fallbacks."""
        def _sync_call() -> Dict[str, Any]:
            client = boto3.client("bedrock-runtime", region_name=self.region_name)
            return client.converse(**kwargs)

        try:
            return await run_in_threadpool(_sync_call)  # type: ignore
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code in ("ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException"):
                logger.critical(f"Bedrock outage detected ({error_code}). Initiating deterministic fallback.")
                return {
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [{"text": '{"error": "AI service temporarily unavailable due to capacity constraints."}'}],
                        }
                    },
                    "usage": {"inputTokens": 0, "outputTokens": 0},
                    "stopReason": "fallback",
                }
            raise e

    def _generate_tool_config(self, tools: List[Callable[..., Any]]) -> Dict[str, Any]:
        bedrock_tools = []
        for tool in tools:
            if hasattr(tool, "args_schema") and tool.args_schema:
                schema = tool.args_schema.model_json_schema()
                name = getattr(tool, "name", tool.__name__)
                desc = getattr(tool, "description", inspect.getdoc(tool) or f"Execute {name}")
            else:
                name = tool.__name__
                desc = inspect.getdoc(tool) or f"Execute {name}"
                sig = inspect.signature(tool)

                fields: Dict[str, Any] = {}
                for param_name, param in sig.parameters.items():
                    if param_name == "self":
                        continue
                    annotation = param.annotation if param.annotation != inspect.Parameter.empty else Any
                    default = param.default if param.default != inspect.Parameter.empty else ...
                    fields[param_name] = (annotation, default)

                dynamic_model = create_model(f"{name}Args", **fields)  # type: ignore
                schema = dynamic_model.model_json_schema()

            input_schema: Dict[str, Any] = {
                "type": "object",
                "properties": schema.get("properties", {}),
            }
            if "required" in schema:
                input_schema["required"] = schema["required"]

            bedrock_tools.append({
                "toolSpec": {
                    "name": name,
                    "description": desc,
                    "inputSchema": {"json": input_schema},
                }
            })
        return {"tools": bedrock_tools}

    async def invoke_model(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        model_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    ) -> Tuple[str, TokenUsage]:
        pricing = self.PRICING_MAP.get(model_id, self.PRICING_MAP["default"])
        system_prompts = [{"text": system}] if system else []

        response = await self._invoke_converse(
            modelId=model_id,
            messages=messages,
            system=system_prompts,
        )

        content = response["output"]["message"]["content"][0].get("text", "")
        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)
        estimated_cost = (input_tokens / 1_000_000 * pricing["input"]) + \
                         (output_tokens / 1_000_000 * pricing["output"])

        token_usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
        )
        return content, token_usage

    async def invoke_structured(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        response_model: Type[T],
        model_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    ) -> Tuple[T, TokenUsage]:
        schema_json = json.dumps(response_model.model_json_schema())
        enhanced_system = (
            f"{system}\n\n"
            "You must respond ONLY with a raw, valid JSON object matching the following JSON schema. "
            "Do not include markdown formatting, markdown code blocks, or conversational text.\n"
            f"Schema:\n{schema_json}"
        )

        @retry(
            retry=retry_if_exception_type((json.JSONDecodeError, ValidationError)),
            stop=stop_after_attempt(3),
            reraise=True,
        )
        async def _attempt() -> Tuple[T, TokenUsage]:
            content, usage = await self.invoke_model(messages, enhanced_system, model_id)
            clean_content = content.strip()
            if clean_content.startswith("```json"):
                clean_content = clean_content[7:]
            if clean_content.startswith("```"):
                clean_content = clean_content[3:]
            if clean_content.endswith("```"):
                clean_content = clean_content[:-3]

            parsed = response_model.model_validate_json(clean_content.strip())
            return parsed, usage

        try:
            return await _attempt()
        except (json.JSONDecodeError, ValidationError) as e:
            raise BedrockStructuredOutputError(
                f"Failed to parse structured output from Bedrock after retries. Error: {str(e)}"
            ) from e

    async def invoke_with_tools(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Callable[..., Any]],
        model_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    ) -> Tuple[Dict[str, Any], TokenUsage]:
        pricing = self.PRICING_MAP.get(model_id, self.PRICING_MAP["default"])
        system_prompts = [{"text": system}] if system else []
        tool_config = self._generate_tool_config(tools)

        response = await self._invoke_converse(
            modelId=model_id,
            messages=messages,
            system=system_prompts,
            toolConfig=tool_config,
        )

        if response.get("stopReason") == "fallback":
            return {"name": "system_outage", "args": {"reason": "AWS Bedrock Unavailable"}}, TokenUsage()

        content_blocks = response["output"]["message"].get("content", [])
        tool_call_result: Dict[str, Any] = {}
        for block in content_blocks:
            if "toolUse" in block:
                tool_use = block["toolUse"]
                tool_call_result = {
                    "name": tool_use["name"],
                    "args": tool_use["input"],
                }
                break

        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)
        estimated_cost = (input_tokens / 1_000_000 * pricing["input"]) + \
                         (output_tokens / 1_000_000 * pricing["output"])

        token_usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
        )
        return tool_call_result, token_usage