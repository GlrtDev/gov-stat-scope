"""Liveness and readiness health checks for ECS target groups and monitoring."""

from __future__ import annotations

import logging
import os

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", include_in_schema=True)
@router.get("/live", include_in_schema=True)
async def liveness_probe() -> dict[str, str]:
    """Fast liveness probe confirming process execution for ALB/ECS target groups."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness_probe() -> JSONResponse:
    """Deep readiness probe asserting thread-pooled connectivity to AWS dependencies."""
    region_name = os.getenv("AWS_REGION", "us-east-1")
    table_name = os.getenv("DYNAMODB_TABLE_NAME", "govdata-sessions")
    endpoint_url = os.getenv("DYNAMODB_ENDPOINT")

    checks: dict[str, str] = {"dynamodb": "unknown", "bedrock": "unknown"}
    status = "ready"

    def check_dynamodb() -> None:
        client = boto3.client("dynamodb", region_name=region_name, endpoint_url=endpoint_url)
        client.describe_table(TableName=table_name)

    def check_bedrock() -> None:
        client = boto3.client("bedrock-runtime", region_name=region_name)
        client.converse(
            modelId="anthropic.claude-3-haiku-20240307-v1:0",
            messages=[{"role": "user", "content": [{"text": "ping"}]}],
            inferenceConfig={"maxTokens": 1},
        )

    try:
        await run_in_threadpool(check_dynamodb)
        checks["dynamodb"] = "ok"
    except Exception as e:
        logger.error(f"DynamoDB readiness check failed: {e}")
        checks["dynamodb"] = "failed"
        status = "unavailable"

    try:
        await run_in_threadpool(check_bedrock)
        checks["bedrock"] = "ok"
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("ThrottlingException", "ValidationException", "AccessDeniedException"):
            checks["bedrock"] = "ok"
        else:
            logger.error(f"Bedrock readiness check failed: {e}")
            checks["bedrock"] = "failed"
            status = "unavailable"
    except Exception as e:
        logger.error(f"Bedrock readiness check failed: {e}")
        checks["bedrock"] = "failed"
        status = "unavailable"

    status_code = 200 if status == "ready" else 503
    return JSONResponse(status_code=status_code, content={"status": status, "checks": checks})