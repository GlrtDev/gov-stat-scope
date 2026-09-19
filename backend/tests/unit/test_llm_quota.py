"""Unit tests for DynamoDB-backed LLM quota enforcement (uses local DynamoDB)."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError as BotoClientError

from app.services.llm_quota import DynamoDBLLMQuota, LLMQuotaExceeded

ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8000")
REGION = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME = "test-llm-quota"


@pytest.fixture(scope="module")
def dynamodb_resource():
    """Create/delete a dedicated test table on the local DynamoDB."""
    resource = boto3.resource("dynamodb", region_name=REGION, endpoint_url=ENDPOINT_URL)
    table = resource.Table(TABLE_NAME)

    # Delete stale table if present
    try:
        table.delete()
        table.wait_until_not_exists()
    except BotoClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    # Create table with composite-key schema matching DynamoDBLLMQuota
    resource.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        ProvisionedThroughput={
            "ReadCapacityUnits": 5,
            "WriteCapacityUnits": 5,
        },
    )
    table.wait_until_exists()

    yield resource

    # Cleanup
    try:
        table.delete()
        table.wait_until_not_exists()
    except BotoClientError:
        pass


def test_check_and_increment_success(dynamodb_resource) -> None:
    quota = DynamoDBLLMQuota(table_name=TABLE_NAME, daily_limit=5, endpoint_url=ENDPOINT_URL)

    result = quota.check_and_increment("session-quota-test")

    assert result == {
        "allowed": True,
        "used": 1,
        "limit": 5,
        "remaining": 4,
    }

    table = dynamodb_resource.Table(TABLE_NAME)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    item = table.get_item(
        Key={"pk": "llm-quota", "sk": f"session-quota-test#{today}"}
    ).get("Item", {})
    assert item["usage"] == 1
    assert "ttl" in item


def test_check_and_increment_limit_reached(dynamodb_resource) -> None:
    quota = DynamoDBLLMQuota(table_name=TABLE_NAME, daily_limit=2, endpoint_url=ENDPOINT_URL)
    scope = "session-quota-limit"

    first = quota.check_and_increment(scope)
    assert first["allowed"] is True
    assert first["used"] == 1

    second = quota.check_and_increment(scope)
    assert second["allowed"] is True
    assert second["used"] == 2

    third = quota.check_and_increment(scope)
    assert third["allowed"] is False
    assert third["used"] == 3
    assert third["remaining"] == 0


def test_fail_open_when_table_missing(dynamodb_resource) -> None:
    quota = DynamoDBLLMQuota(
        table_name=f"definitely-not-existing-{uuid.uuid4().hex}",
        daily_limit=5,
        endpoint_url=ENDPOINT_URL,
    )

    result = quota.check_and_increment("any-scope")

    assert result["allowed"] is True
    assert result["used"] == 0
    assert result["remaining"] == 5


@pytest.mark.asyncio
async def test_check_and_increment_async(dynamodb_resource) -> None:
    quota = DynamoDBLLMQuota(table_name=TABLE_NAME, daily_limit=5, endpoint_url=ENDPOINT_URL)

    result = await quota.check_and_increment_async("session-quota-async")

    assert result["allowed"] is True
    assert result["used"] == 1


def test_ttl_expiry_is_after_next_midnight() -> None:
    quota = DynamoDBLLMQuota(table_name=TABLE_NAME, daily_limit=5, endpoint_url=ENDPOINT_URL)

    now = datetime.now(timezone.utc)
    ttl = quota._ttl()
    next_midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    assert ttl >= int(next_midnight.timestamp())
    assert ttl <= int(next_midnight.timestamp()) + 3600


def test_llm_quota_exceeded_message() -> None:
    exc = LLMQuotaExceeded(limit=10, used=12)

    assert "Daily LLM limit of 10 exceeded (used: 12)" in str(exc)