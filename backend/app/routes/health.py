"""Liveness and readiness health checks for ECS target groups and monitoring."""

from __future__ import annotations

import logging
import os
from typing import Any

import aioboto3
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", include_in_schema=True)
@router.get("/live", include_in_schema=True)
async def liveness_probe() -> dict[str, str]:
    """Fast liveness probe confirming process execution for ALB/ECS target groups."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness_probe() -> JSONResponse:
    """Deep readiness probe asserting connectivity to external AWS dependencies."""
    region_name = os.getenv("AWS_REGION", "us-east-1")
    table_name = os.getenv("DYNAMODB_TABLE_NAME", "govdata-sessions")
    
    checks: dict[str, str] = {"dynamodb": "unknown", "bedrock": "unknown"}
    status = "ready"
    
    session = aioboto3.Session(region_name=region_name)
    
    try:
        async with session.client("dynamodb", endpoint_url=os.getenv("DYNAMODB_ENDPOINT")) as ddb_client:
            await ddb_client.describe_table(TableName=table_name)
            checks["dynamodb"] = "ok"
    except Exception as e:
        logger.error(f"DynamoDB readiness check failed: {e}")
        checks["dynamodb"] = "failed"
        status = "unavailable"

    try:
        async with session.client("bedrock-runtime") as bedrock_client:
            await bedrock_client.converse(
                modelId="anthropic.claude-3-haiku-20240307-v1:0",
                messages=[{"role": "user", "content": [{"text": "ping"}]}],
                inferenceConfig={"maxTokens": 1}
            )
            checks["bedrock"] = "ok"
    except Exception as e:
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if error_code in ("ThrottlingException", "ValidationException", "AccessDeniedException"):
            checks["bedrock"] = "ok"
        else:
            logger.error(f"Bedrock readiness check failed: {e}")
            checks["bedrock"] = "failed"
            status = "unavailable"

    status_code = 200 if status == "ready" else 503
    return JSONResponse(status_code=status_code, content={"status": status, "checks": checks})