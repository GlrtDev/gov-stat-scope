import json
import logging
from typing import Any, Dict

import aioboto3

logger = logging.getLogger(__name__)


class AsyncSecretsClient:
    """
    Asynchronous client for AWS Secrets Manager with in-memory caching.
    """
    def __init__(self, region_name: str = "us-east-1") -> None:
        self.region_name = region_name
        self._cache: Dict[str, Dict[str, Any]] = {}

    async def get_secret(self, secret_name: str) -> Dict[str, Any]:
        """
        Retrieves a JSON-formatted secret from AWS Secrets Manager or the local cache.
        """
        if secret_name in self._cache:
            return self._cache[secret_name]

        session = aioboto3.Session(region_name=self.region_name)
        try:
            async with session.client("secretsmanager") as client:
                response = await client.get_secret_value(SecretId=secret_name)
                
                if "SecretString" not in response:
                    raise ValueError(f"Secret '{secret_name}' does not contain a SecretString payload.")
                
                secret_data = json.loads(response["SecretString"])
                self._cache[secret_name] = secret_data
                return secret_data
                
        except Exception as e:
            logger.error(f"Failed to retrieve secret '{secret_name}' from AWS: {str(e)}")
            raise