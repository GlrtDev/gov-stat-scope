import os
import time
from typing import Any, AsyncIterator, Dict, Iterable, Optional, Sequence, Tuple

import aioboto3
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class DynamoDBSaver(BaseCheckpointSaver):
    """
    Asynchronous DynamoDB Checkpoint Saver for LangGraph.
    Stores conversational state with a 7-day Time-To-Live (TTL) for automatic cleanup.
    """

    def __init__(self, table_name: str, region_name: str = "us-east-1") -> None:
        super().__init__(serde=JsonPlusSerializer())
        self.table_name = table_name
        self.region_name = region_name
        self.endpoint_url = os.getenv("DYNAMODB_ENDPOINT")
        self.session = aioboto3.Session(region_name=self.region_name)

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        raise NotImplementedError("Synchronous execution is not supported. Use aget_tuple.")

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any
    ) -> RunnableConfig:
        raise NotImplementedError("Synchronous execution is not supported. Use aput.")

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None
    ) -> Iterable[CheckpointTuple]:
        raise NotImplementedError("Synchronous execution is not supported. Use alist.")

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        session_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id")

        async with self.session.client("dynamodb", endpoint_url=self.endpoint_url) as client:
            if checkpoint_id:
                response = await client.get_item(
                    TableName=self.table_name,
                    Key={
                        "session_id": {"S": session_id},
                        "checkpoint_id": {"S": checkpoint_id}
                    }
                )
                item = response.get("Item")
            else:
                response = await client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="session_id = :sid",
                    ExpressionAttributeValues={":sid": {"S": session_id}},
                    ScanIndexForward=False,
                    Limit=1
                )
                item = response["Items"][0] if response.get("Items") else None

            if not item:
                return None

            checkpoint = self.serde.loads(item["checkpoint"]["B"])
            metadata = self.serde.loads(item["metadata"]["B"])
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
                pending_writes=[]
            )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any
    ) -> RunnableConfig:
        session_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")

        # Set TTL to 7 days from now
        ttl = int(time.time()) + (7 * 24 * 60 * 60)
        
        item: Dict[str, Any] = {
            "session_id": {"S": session_id},
            "checkpoint_id": {"S": checkpoint_id},
            "checkpoint": {"B": self.serde.dumps(checkpoint)},
            "metadata": {"B": self.serde.dumps(metadata)},
            "ttl": {"N": str(ttl)}
        }
        
        if parent_id:
            item["parent_checkpoint_id"] = {"S": parent_id}

        async with self.session.client("dynamodb", endpoint_url=self.endpoint_url) as client:
            await client.put_item(TableName=self.table_name, Item=item)

        return {
            "configurable": {
                "thread_id": session_id,
                "checkpoint_id": checkpoint_id
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str
    ) -> None:
        """Stub for LangGraph intermediate state writes. Not strictly required for top-level memory."""
        pass

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None
    ) -> AsyncIterator[CheckpointTuple]:
        if not config:
            return
            
        session_id = config["configurable"]["thread_id"]

        async with self.session.client("dynamodb", endpoint_url=self.endpoint_url) as client:
            kwargs: Dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": "session_id = :sid",
                "ExpressionAttributeValues": {":sid": {"S": session_id}},
                "ScanIndexForward": False
            }
            if limit:
                kwargs["Limit"] = limit

            response = await client.query(**kwargs)

            for item in response.get("Items", []):
                checkpoint = self.serde.loads(item["checkpoint"]["B"])
                metadata = self.serde.loads(item["metadata"]["B"])
                parent_id = item.get("parent_checkpoint_id", {}).get("S", "")
                
                parent_config = (
                    {"configurable": {"thread_id": session_id, "checkpoint_id": parent_id}}
                    if parent_id else None
                )
                
                yield CheckpointTuple(
                    config={"configurable": {"thread_id": session_id, "checkpoint_id": item["checkpoint_id"]["S"]}},
                    checkpoint=checkpoint,
                    metadata=metadata,
                    parent_config=parent_config,
                    pending_writes=[]
                )


async def init_dynamodb_tables(table_name: str, region_name: str = "us-east-1") -> None:
    """
    Idempotently creates the DynamoDB sessions table and configures the TTL policy.
    """
    endpoint_url = os.getenv("DYNAMODB_ENDPOINT")
    session = aioboto3.Session(region_name=region_name)
    
    async with session.client("dynamodb", endpoint_url=endpoint_url) as client:
        try:
            await client.create_table(
                TableName=table_name,
                KeySchema=[
                    {"AttributeName": "session_id", "KeyType": "HASH"},
                    {"AttributeName": "checkpoint_id", "KeyType": "RANGE"}
                ],
                AttributeDefinitions=[
                    {"AttributeName": "session_id", "AttributeType": "S"},
                    {"AttributeName": "checkpoint_id", "AttributeType": "S"}
                ],
                BillingMode="PAY_PER_REQUEST"
            )

            waiter = client.get_waiter("table_exists")
            await waiter.wait(TableName=table_name)
            
            await client.update_time_to_live(
                TableName=table_name,
                TimeToLiveSpecification={
                    "Enabled": True,
                    "AttributeName": "ttl"
                }
            )
            
        except client.exceptions.ResourceInUseException:
            pass  # Table already exists