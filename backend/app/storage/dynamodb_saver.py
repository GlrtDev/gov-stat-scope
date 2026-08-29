"""Thread-pooled DynamoDB Checkpoint Saver for LangGraph."""

from __future__ import annotations

import os
import pickle
import time
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence, Tuple

import boto3
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from starlette.concurrency import run_in_threadpool


class DynamoDBSaver(BaseCheckpointSaver):
    """Asynchronous DynamoDB Checkpoint Saver supporting custom endpoints for testing."""

    def __init__(self, table_name: str, region_name: str = "us-east-1", endpoint_url: Optional[str] = None) -> None:
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
            client = boto3.client("dynamodb", region_name=self.region_name, endpoint_url=self.endpoint_url)
            if checkpoint_id:
                response = client.get_item(
                    TableName=self.table_name,
                    Key={"session_id": {"S": session_id}, "checkpoint_id": {"S": checkpoint_id}},
                )
                return response.get("Item")
            else:
                response = client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="session_id = :sid",
                    ExpressionAttributeValues={":sid": {"S": session_id}},
                    ScanIndexForward=False,
                    Limit=1,
                )
                items = response.get("Items", [])
                return items[0] if items else None

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
            client = boto3.client("dynamodb", region_name=self.region_name, endpoint_url=self.endpoint_url)
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
            client = boto3.client("dynamodb", region_name=self.region_name, endpoint_url=self.endpoint_url)
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


async def init_dynamodb_tables(table_name: str, region_name: str = "us-east-1", endpoint_url: Optional[str] = None) -> None:
    """Idempotently creates the DynamoDB sessions table and configures the TTL policy."""
    resolved_endpoint = endpoint_url or os.getenv("DYNAMODB_ENDPOINT")

    def _sync_init() -> None:
        client = boto3.client("dynamodb", region_name=region_name, endpoint_url=resolved_endpoint)
        try:
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