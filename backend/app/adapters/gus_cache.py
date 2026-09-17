"""DynamoDB response cache decorator for GUS API low-level requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

_client: Any = None


_client_cache: Dict[Tuple[Optional[str], Optional[str]], Any] = {}

def _get_client(
    region_name: Optional[str],
    endpoint_url: Optional[str] = None,
) -> Any:
    resolved_endpoint = endpoint_url or os.getenv("DYNAMODB_ENDPOINT")
    resolved_region = (
        region_name
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    key = (resolved_region, resolved_endpoint)
    client = _client_cache.get(key)
    if client is None:
        kwargs: Dict[str, Any] = {"region_name": resolved_region}
        if resolved_endpoint:
            kwargs.update(
                endpoint_url=resolved_endpoint,
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "dummy"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "dummy"),
                aws_session_token=None,
            )
        client = boto3.client("dynamodb", **kwargs)
        _client_cache[key] = client
    return client


def _sort_dict(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _sort_dict(v)
            for k, v in sorted(value.items())
        }
    if isinstance(value, list):
        return [_sort_dict(v) for v in value]
    return value


def _request_key(method: str, path: str, kwargs: Dict[str, Any]) -> str:
    params = kwargs.get("params") or {}
    body = kwargs.get("json") or kwargs.get("data") or {}

    if isinstance(params, dict):
        params = _sort_dict(params)

    payload = {
        "method": method.upper(),
        "path": path,
        "params": params,
        "body": _sort_dict(body) if isinstance(body, dict) else body,
    }

    raw = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_cached(
    table_name: str,
    region_name: Optional[str],
    endpoint_url: Optional[str],
    key: str,
) -> Optional[Any]:
    if not table_name:
        return None

    client = _get_client(region_name, endpoint_url)
    response = client.get_item(
        TableName=table_name,
        Key={"pk": {"S": f"GUS#{key}"}},
        ConsistentRead=False,
    )
    item = response.get("Item")
    if not item:
        return None

    ttl = int(item.get("ttl", {}).get("N", "0"))
    if ttl and ttl < int(time.time()):
        return None

    body = item.get("body", {}).get("S")
    if not body:
        return None

    return json.loads(body)


def _put_cached(
    table_name: str,
    region_name: Optional[str],
    endpoint_url: Optional[str],
    key: str,
    method: str,
    path: str,
    kwargs: Dict[str, Any],
    response: Any,
    ttl_days: int,
) -> None:
    if not table_name:
        return

    body_str = json.dumps(response, ensure_ascii=False, default=str)
    body_bytes = body_str.encode("utf-8")

    # DynamoDB item size limit is 400 KB; conservatively skip larger bodies.
    if len(body_bytes) > 350_000:
        logger.warning(
            "GUS response for %s %s too large for DynamoDB cache; skipping",
            method.upper(),
            path,
        )
        return

    if isinstance(kwargs.get("params"), dict):
        params_str = json.dumps(
            _sort_dict(kwargs["params"]),
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    else:
        params_str = json.dumps(
            kwargs.get("params", {}),
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )

    client = _get_client(region_name, endpoint_url)
    client.put_item(
        TableName=table_name,
        Item={
            "pk": {"S": f"GUS#{key}"},
            "ttl": {"N": str(int(time.time()) + ttl_days * 86400)},
            "method": {"S": method.upper()},
            "path": {"S": path},
            "params": {"S": params_str},
            "body": {"S": body_str},
            "cached_at": {"N": str(int(time.time()))},
        },
    )


def dynamodb_cache(
    ttl_days: int = 7,
    table_name: Optional[str] = None,
    region_name: Optional[str] = None,
    endpoint_url: Optional[str] = None,
) -> Callable[[F], F]:
    """Cache async method responses in DynamoDB with a TTL.

    Usage:
        @dynamodb_cache(ttl_days=7)
        async def _request(self, method, path, **kwargs):
            ...
    """

def dynamodb_cache(
    ttl_days: int = 7,
    table_name: Optional[str] = None,
    region_name: Optional[str] = None,
    endpoint_url: Optional[str] = None,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(
            self: Any,
            method: str,
            path: str,
            **kwargs: Any,
        ) -> Any:
            cache_table = (
                table_name
                or os.getenv("GUS_CACHE_TABLE")
                or "govstat-gus-cache"
            )
            resolved_endpoint = endpoint_url or os.getenv("DYNAMODB_ENDPOINT")

            if os.getenv("GUS_CACHE_DISABLED", "").lower() in {"1", "true", "yes"}:
                return await func(self, method, path, **kwargs)

            cache_key = _request_key(method, path, kwargs)

            try:
                cached = await asyncio.get_running_loop().run_in_executor(
                    None,
                    _get_cached,
                    cache_table,
                    region_name,
                    resolved_endpoint,
                    cache_key,
                )
                if cached is not None:
                    return cached
            except (BotoCoreError, ClientError, ValueError, TypeError):
                logger.exception("GUS DynamoDB cache read failed; falling back to network")

            response = await func(self, method, path, **kwargs)

            try:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    _put_cached,
                    cache_table,
                    region_name,
                    resolved_endpoint,
                    cache_key,
                    method,
                    path,
                    kwargs,
                    response,
                    ttl_days,
                )
            except (BotoCoreError, ClientError, ValueError, TypeError):
                logger.exception("GUS DynamoDB cache write failed; ignoring")

            return response

        return wrapper  # type: ignore[return-value]

    return decorator