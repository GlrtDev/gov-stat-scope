"""Daily LLM quota enforcement with atomic DynamoDB counters."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


class LLMQuotaExceeded(Exception):
    """Raised when the daily LLM request limit is exhausted."""

    def __init__(self, limit: int, used: int) -> None:
        self.limit = limit
        self.used = used
        super().__init__(
            f"Daily LLM limit of {limit} exceeded (used: {used}). "
            f"Resets at UTC midnight."
        )


class DynamoDBLLMQuota:
    """
    Atomic daily request counter per scope (session ID / API key / IP).

    Schema (existing composite-key table, no migration needed):
      pk = "llm-quota"
      sk = "<scope>#<YYYY-MM-DD>"
      usage : Number
      ttl   : Number  (Unix timestamp, auto-deletes at UTC midnight + 1h buffer)
    """

    def __init__(
        self,
        table_name: Optional[str] = None,
        daily_limit: Optional[int] = None,
        endpoint_url: Optional[str] = None,
    ) -> None:
        self.table_name = table_name or os.getenv("DYNAMODB_QUOTA_TABLE", "govdata-llm-quota")
        self.daily_limit = daily_limit or int(os.getenv("LLM_DAILY_LIMIT", "100"))
        self.endpoint_url = endpoint_url or os.getenv("DYNAMODB_ENDPOINT")
        self.region = os.getenv("AWS_REGION", "eu-north-1")
        self._resource = boto3.resource(
            "dynamodb",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
        )
        self._table = self._resource.Table(self.table_name)

    def _item_key(self, scope_id: str) -> dict[str, str]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {"pk": "llm-quota", "sk": f"{scope_id}#{today}"}

    def _ttl(self) -> int:
        """Unix timestamp for next UTC midnight + 1 hour buffer."""
        now = datetime.now(timezone.utc)
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int(next_midnight.timestamp()) + 3600

    def check_and_increment(self, scope_id: str) -> dict[str, Any]:
        """
        Atomically increments usage and returns quota status.

        Returns:
            {
                "allowed": bool,
                "used": int,
                "limit": int,
                "remaining": int,
            }
        """
        key = self._item_key(scope_id)
        try:
            response = self._table.update_item(
                Key=key,
                UpdateExpression=(
                    "SET #usage = if_not_exists(#usage, :zero) + :inc, "
                    "#expiry = :ttl"
                ),
                ExpressionAttributeNames={
                    "#usage": "usage",
                    "#expiry": "ttl",
                },
                ExpressionAttributeValues={
                    ":zero": 0,
                    ":inc": 1,
                    ":ttl": self._ttl(),
                },
                ReturnValues="ALL_NEW",
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error("DynamoDB quota increment failed: %s", exc)
            # Fail-CLOSED: block rather than allow unbounded Bedrock spend during an outage
            return {
                "allowed": False,
                "used": -1,
                "limit": self.daily_limit,
                "remaining": 0,
            }

        used = int(response.get("Attributes", {}).get("usage", 0))
        remaining = max(self.daily_limit - used, 0)
        return {
            "allowed": used <= self.daily_limit,
            "used": used,
            "limit": self.daily_limit,
            "remaining": remaining,
        }

    async def check_and_increment_async(self, scope_id: str) -> dict[str, Any]:
        """Thread-pooled async wrapper (boto3 is synchronous)."""
        return await run_in_threadpool(self.check_and_increment, scope_id)