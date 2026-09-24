"""Thread-pooled DynamoDB Checkpoint Saver for LangGraph."""

from __future__ import annotations

import os
import pickle
import time
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence, Tuple
import logging
import boto3
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

def _dynamodb_client(region_name: str, endpoint_url: Optional[str]) -> Any:
    """Create a DynamoDB client.

    For local endpoints (DynamoDB Local/LocalStack), inject dummy credentials
    and clear the session token. Production uses the default AWS credential chain.
    """
    kwargs: Dict[str, Any] = {"region_name": region_name}
    if endpoint_url:
        kwargs.update(
            endpoint_url=endpoint_url,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "dummy"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "dummy"),
            aws_session_token=None,
        )
    return boto3.client("dynamodb", **kwargs)

class DynamoDBSaver(BaseCheckpointSaver):
    """Asynchronous DynamoDB Checkpoint Saver supporting custom endpoints for testing."""

    def __init__(self, table_name: str, region_name: str = "eu-north-1", endpoint_url: Optional[str] = None) -> None:
        super().__init__()
        self.table_name = table_name
        self.region_name = region_name
        self.endpoint_url = endpoint_url or os.getenv("DYNAMODB_ENDPOINT")

    def _dumps(self, obj: Any) -> bytes:
        """Version-agnostic serialization to handle LangGraph API changes."""
        if hasattr(self, "serde") and self.serde and hasattr(self.serde, "dumps"):
            return self.serde.dumps(obj)
        return pickle.dumps(obj)

    def _loads(self, data: bytes) -> Any:
        """Version-agnostic deserialization to handle LangGraph API changes."""
        if hasattr(self, "serde") and self.serde and hasattr(self.serde, "loads"):
            return self.serde.loads(data)
        return pickle.loads(data)

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        raise NotImplementedError("Synchronous execution is not supported. Use aget_tuple.")

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: Any) -> RunnableConfig:
        raise NotImplementedError("Synchronous execution is not supported. Use aput.")

    def list(self, config: Optional[RunnableConfig], *, filter: Optional[Dict[str, Any]] = None, before: Optional[RunnableConfig] = None, limit: Optional[int] = None) -> Iterable[CheckpointTuple]:
        raise NotImplementedError("Synchronous execution is not supported. Use alist.")

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        session_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id")

        def _sync_get() -> Optional[Dict[str, Any]]:
            client = _dynamodb_client(self.region_name, self.endpoint_url)
            if checkpoint_id:
                response = client.get_item(
                    TableName=self.table_name,
                    Key={"session_id": {"S": session_id}, "checkpoint_id": {"S": checkpoint_id}},
                )
                item = response.get("Item")
                if item and "checkpoint" in item and "metadata" in item:
                    return item
                return None
            else:
                response = client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="session_id = :sid",
                    ExpressionAttributeValues={":sid": {"S": session_id}},
                    ScanIndexForward=False,
                    Limit=10,
                )
                # Skip context rows (like "__context__") that have no checkpoint blob
                for item in response.get("Items", []):
                    if "checkpoint" in item and "metadata" in item:
                        return item
                return None

        item = await run_in_threadpool(_sync_get)
        if not item:
            return None

        checkpoint = self._loads(item["checkpoint"]["B"])
        metadata = self._loads(item["metadata"]["B"])
        parent_id = item.get("parent_checkpoint_id", {}).get("S", "")

        parent_config = (
            {"configurable": {"thread_id": session_id, "checkpoint_id": parent_id}}
            if parent_id else None
        )

        return CheckpointTuple(
            config={"configurable": {"thread_id": session_id, "checkpoint_id": item["checkpoint_id"]["S"]}},
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=[],
        )

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: Any) -> RunnableConfig:
        session_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")
        ttl = int(time.time()) + (7 * 24 * 60 * 60)

        item: Dict[str, Any] = {
            "session_id": {"S": session_id},
            "checkpoint_id": {"S": checkpoint_id},
            "checkpoint": {"B": self._dumps(checkpoint)},
            "metadata": {"B": self._dumps(metadata)},
            "ttl": {"N": str(ttl)},
        }
        if parent_id:
            item["parent_checkpoint_id"] = {"S": parent_id}

        def _sync_put() -> None:
            client = _dynamodb_client(self.region_name, self.endpoint_url)
            client.put_item(TableName=self.table_name, Item=item)

        await run_in_threadpool(_sync_put)
        return {"configurable": {"thread_id": session_id, "checkpoint_id": checkpoint_id}}

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[Tuple[str, Any]], task_id: str) -> None:
        pass

    async def alist(self, config: Optional[RunnableConfig], *, filter: Optional[Dict[str, Any]] = None, before: Optional[RunnableConfig] = None, limit: Optional[int] = None) -> AsyncIterator[CheckpointTuple]:
        if not config:
            return
        session_id = config["configurable"]["thread_id"]

        def _sync_list() -> List[Dict[str, Any]]:
            client = _dynamodb_client(self.region_name, self.endpoint_url)
            kwargs: Dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": "session_id = :sid",
                "ExpressionAttributeValues": {":sid": {"S": session_id}},
                "ScanIndexForward": False,
            }
            if limit:
                kwargs["Limit"] = limit
            response = client.query(**kwargs)
            return response.get("Items", [])

        items = await run_in_threadpool(_sync_list)
        for item in items:
            checkpoint = self._loads(item["checkpoint"]["B"])
            metadata = self._loads(item["metadata"]["B"])
            parent_id = item.get("parent_checkpoint_id", {}).get("S", "")
            parent_config = {"configurable": {"thread_id": session_id, "checkpoint_id": parent_id}} if parent_id else None
            yield CheckpointTuple(
                config={"configurable": {"thread_id": session_id, "checkpoint_id": item["checkpoint_id"]["S"]}},
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
                pending_writes=[],
            )


async def init_dynamodb_tables(table_name: str, region_name: str = "eu-north-1", endpoint_url: Optional[str] = None) -> None:
    """Idempotently creates the DynamoDB sessions table and configures the TTL policy."""
    resolved_endpoint = endpoint_url or os.getenv("DYNAMODB_ENDPOINT")

    def _sync_init() -> None:
        client = _dynamodb_client(region_name, resolved_endpoint)
        try:
            try:
                client.describe_table(TableName=table_name)
                logger.info("DynamoDB table '%s' already provisioned (IaC) — skipping create.", table_name)
                return
            except client.exceptions.ResourceNotFoundException:
                pass

            if os.getenv("ENVIRONMENT") == "production":
                raise RuntimeError(
                    f"DynamoDB table '{table_name}' is not provisioned. "
                    "Create it via IaC (CDK) — the runtime role intentionally lacks dynamodb:CreateTable."
                )

            client.create_table(
                TableName=table_name,
                KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}, {"AttributeName": "checkpoint_id", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}, {"AttributeName": "checkpoint_id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            client.get_waiter("table_exists").wait(TableName=table_name)
            client.update_time_to_live(TableName=table_name, TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"})
        except client.exceptions.ResourceInUseException:
            pass

    await run_in_threadpool(_sync_init)