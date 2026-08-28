"""Thread-pooled AWS Secrets Manager client with in-memory caching."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import boto3
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


class AsyncSecretsClient:
    """Asynchronous client for AWS Secrets Manager using standard boto3 in thread pools."""

    def __init__(self, region_name: str = "us-east-1") -> None:
        self.region_name = region_name
        self._cache: Dict[str, Dict[str, Any]] = {}

    async def get_secret(self, secret_name: str) -> Dict[str, Any]:
        """Retrieves a JSON-formatted secret from AWS Secrets Manager or the local cache."""
        if secret_name in self._cache:
            return self._cache[secret_name]

        def _sync_fetch() -> Dict[str, Any]:
            client = boto3.client("secretsmanager", region_name=self.region_name)
            response = client.get_secret_value(SecretId=secret_name)
            if "SecretString" not in response:
                raise ValueError(f"Secret '{secret_name}' does not contain a SecretString payload.")
            return json.loads(response["SecretString"])

        try:
            secret_data = await run_in_threadpool(_sync_fetch)
            self._cache[secret_name] = secret_data
            return secret_data
        except Exception as e:
            logger.error(f"Failed to retrieve secret '{secret_name}' from AWS: {str(e)}")
            raise