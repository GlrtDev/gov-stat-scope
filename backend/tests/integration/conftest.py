from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any, Dict, List

import boto3
import pytest_asyncio

from app.storage.dynamodb_saver import init_dynamodb_tables

DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://dynamodb-local:8000")
SESSION_TABLE = "govdata-sessions-test"
CACHE_TABLE = os.getenv("GUS_CACHE_TABLE", "govstat-gus-cache")


def _make_client() -> Any:
    kwargs: Dict[str, Any] = {
        "region_name": "us-east-1",
    }
    if DYNAMODB_ENDPOINT:
        # Use dummy creds + clear session token for DynamoDB Local/LocalStack.
        kwargs.update(
            endpoint_url=DYNAMODB_ENDPOINT,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "dummy"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "dummy"),
            aws_session_token=None,
        )
    return boto3.client("dynamodb", **kwargs)


def _create_table_if_missing(
    client: Any,
    table_name: str,
    key_schema: List[Dict[str, str]],
    attr_defs: List[Dict[str, str]],
) -> None:
    existing = client.list_tables().get("TableNames", [])
    if table_name in existing:
        return
    client.create_table(
        TableName=table_name,
        KeySchema=key_schema,
        AttributeDefinitions=attr_defs,
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=table_name)


@pytest_asyncio.fixture(autouse=True)
async def ensure_dynamodb_tables() -> AsyncGenerator[None, None]:
    """Ensure all DynamoDB tables needed by integration tests exist before running."""
    client = _make_client()

    # GUS cache table: single-attribute primary key "pk"
    _create_table_if_missing(
        client,
        CACHE_TABLE,
        key_schema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        attr_defs=[{"AttributeName": "pk", "AttributeType": "S"}],
    )

    # Sessions/checkpoint table: composite key used by DynamoDBSaver
    await init_dynamodb_tables(
        table_name=SESSION_TABLE,
        region_name="us-east-1",
        endpoint_url=DYNAMODB_ENDPOINT,
    )

    yield