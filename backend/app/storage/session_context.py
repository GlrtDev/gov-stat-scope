"""Persist lightweight session context dicts in DynamoDB for follow-up queries."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import boto3
from starlette.concurrency import run_in_threadpool

CONTEXT_CHECKPOINT_ID = "__context__"


def _get_client(region_name: str, endpoint_url: Optional[str]) -> Any:
    resolved_endpoint = endpoint_url or os.getenv("DYNAMODB_ENDPOINT")
    return boto3.client("dynamodb", region_name=region_name, endpoint_url=resolved_endpoint)


async def load_session_context(
    table_name: str,
    session_id: str,
    region_name: str = "us-east-1",
    endpoint_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Load the persisted context dict for a session (empty dict if none)."""

    def _sync_load() -> Optional[Dict[str, Any]]:
        client = _get_client(region_name, endpoint_url)
        response = client.get_item(
            TableName=table_name,
            Key={
                "session_id": {"S": session_id},
                "checkpoint_id": {"S": CONTEXT_CHECKPOINT_ID},
            },
        )
        item = response.get("Item")
        if not item:
            return None
        raw = item.get("context", {}).get("S", "{}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    result = await run_in_threadpool(_sync_load)
    return result or {}


async def save_session_context(
    table_name: str,
    session_id: str,
    context: Dict[str, Any],
    region_name: str = "us-east-1",
    endpoint_url: Optional[str] = None,
) -> None:
    """Persist the context dict for a session, overwriting any previous value."""

    def _sync_save() -> None:
        client = _get_client(region_name, endpoint_url)
        client.put_item(
            TableName=table_name,
            Item={
                "session_id": {"S": session_id},
                "checkpoint_id": {"S": CONTEXT_CHECKPOINT_ID},
                "context": {"S": json.dumps(context, ensure_ascii=False)},
            },
        )

    await run_in_threadpool(_sync_save)