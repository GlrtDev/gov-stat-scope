"""DDoS mitigation middleware: request validation, rate limiting, security headers."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import time
from collections import defaultdict, deque
from typing import Any, Deque, Optional

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Configuration from environment with secure defaults
MAX_BODY_SIZE = int(os.getenv("MAX_BODY_SIZE_BYTES", "1048576"))          # 1 MiB
MAX_QUERY_STRING_LENGTH = int(os.getenv("MAX_QUERY_STRING_LENGTH", "2048"))
MAX_URI_LENGTH = int(os.getenv("MAX_URI_LENGTH", "8192"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
RATE_LIMIT_PER_IP = int(os.getenv("RATE_LIMIT_PER_IP", "100"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
BURST_LIMIT_PER_IP = int(os.getenv("BURST_LIMIT_PER_IP", "20"))
BURST_WINDOW_SECONDS = int(os.getenv("BURST_WINDOW_SECONDS", "1"))

BLOCKED_COUNTRIES = {
    country.strip().upper()
    for country in os.getenv("BLOCKED_COUNTRIES", "KP,IR,CU,SY,VE").split(",")
    if country.strip()
}

ALLOWED_HTTP_METHODS = {"GET", "POST", "OPTIONS", "HEAD", "DELETE", "PATCH"}

BLOCKED_USER_AGENT_PATTERNS = [
    re.compile(r"(curl|wget|python-requests|go-http-client|scrapy|nikto)", re.IGNORECASE),
    re.compile(r"(sqlmap|masscan|nmap|nessus|xray|acunetix|fuzz)", re.IGNORECASE),
]


class _BodyTooLargeError(Exception):
    """Internal exception raised when a request exceeds the body limit."""


class SlidingWindowRateLimiter:
    """Asynchronous sliding-window rate limiter."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: defaultdict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            timestamps = self._hits[key]
            while timestamps and now - timestamps[0] > self.window_seconds:
                timestamps.popleft()
            if len(timestamps) >= self.limit:
                return False
            timestamps.append(now)
            return True


class DDoSPreventionMiddleware:
    """
    Application-level DDoS guard: IP validation, request size caps,
    method allowlist, User-Agent filtering, and sliding-window rate limits.

    For multi-instance deployments, inject a Redis-compatible client.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        blocked_ips: Optional[set[str]] = None,
        allowed_ips: Optional[set[str]] = None,
        redis: Optional[Any] = None,
    ) -> None:
        self.app = app
        self._redis = redis

        self._blocked_ips: set[str] = set()
        self._blocked_networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
        for ip in blocked_ips or set():
            ip = ip.strip()
            if not ip:
                continue
            if "/" in ip:
                try:
                    self._blocked_networks.add(ipaddress.ip_network(ip))
                except ValueError:
                    continue
            else:
                self._blocked_ips.add(ip)

        self._allowed_ips = {ip.strip() for ip in allowed_ips or set() if ip.strip()}

        self._burst_limiter = SlidingWindowRateLimiter(
            limit=BURST_LIMIT_PER_IP,
            window_seconds=BURST_WINDOW_SECONDS,
        )
        self._sustained_limiter = SlidingWindowRateLimiter(
            limit=RATE_LIMIT_PER_IP,
            window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client_ip = self._get_client_ip(scope)

        if self._is_blocked_ip(client_ip):
            await self._deny_request(send, 403, "Blocked IP")
            return

        if self._allowed_ips and client_ip not in self._allowed_ips:
            await self._deny_request(send, 403, "IP not allowed")
            return

        method = scope.get("method", "GET").upper()
        if method not in ALLOWED_HTTP_METHODS:
            await self._deny_request(send, 405, "Method not allowed")
            return

        raw_path = scope.get("raw_path", b"")
        if len(raw_path) > MAX_URI_LENGTH:
            await self._deny_request(send, 414, "URI too long")
            return

        query_string = scope.get("query_string", b"")
        if len(query_string) > MAX_QUERY_STRING_LENGTH:
            await self._deny_request(send, 414, "Query string too long")
            return

        headers = Headers(scope=scope)

        content_length = headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_BODY_SIZE:
                    await self._deny_request(send, 413, "Body too large")
                    return
            except ValueError:
                await self._deny_request(send, 400, "Invalid Content-Length")
                return

        user_agent = headers.get("user-agent", "")
        if any(pattern.search(user_agent) for pattern in BLOCKED_USER_AGENT_PATTERNS):
            await self._deny_request(send, 403, "Blocked User-Agent")
            return

        country = (
            headers.get("cloudfront-viewer-country")
            or headers.get("cf-ipcountry")
            or ""
        ).upper()
        if country in BLOCKED_COUNTRIES:
            await self._deny_request(send, 403, "Country blocked")
            return

        if not await self._is_rate_allowed(client_ip):
            await self._deny_request(send, 429, "Too Many Requests")
            return

        receive = self._wrap_receive(receive, send)

        try:
            await asyncio.wait_for(
                self.app(scope, receive, send),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await self._deny_request(send, 408, "Request timeout")
        except _BodyTooLargeError:
            # Response already sent; abort processing
            return

    def _get_client_ip(self, scope: Scope) -> str:
        headers = Headers(scope=scope)
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            # Use leftmost untrusted IP (closest to client)
            return forwarded.split(",")[0].strip()
        if scope.get("client"):
            return scope["client"][0]
        return "0.0.0.0"

    def _is_blocked_ip(self, ip: str) -> bool:
        if ip in self._blocked_ips:
            return True
        try:
            addr = ipaddress.ip_address(ip)
            return any(addr in network for network in self._blocked_networks)
        except ValueError:
            return False

    async def _is_rate_allowed(self, client_ip: str) -> bool:
        if self._redis is not None:
            return await self._redis_rate_limit(client_ip)
        if not await self._burst_limiter.allow(client_ip):
            return False
        return await self._sustained_limiter.allow(client_ip)

    async def _redis_rate_limit(self, key: str) -> bool:
        try:
            now = int(time.time())
            burst_key = f"ddos:burst:{key}"
            sustained_key = f"ddos:sustained:{key}"

            async def check_and_incr(redis_key: str, limit: int, window: int) -> bool:
                pipe = self._redis.pipeline()
                pipe.zremrangebyscore(redis_key, 0, now - window)
                pipe.zcard(redis_key)
                pipe.zadd(redis_key, {now: now})
                pipe.expire(redis_key, window)
                results = await pipe.execute()
                return results[1] < limit

            if not await check_and_incr(burst_key, BURST_LIMIT_PER_IP, BURST_WINDOW_SECONDS):
                return False
            return await check_and_incr(sustained_key, RATE_LIMIT_PER_IP, RATE_LIMIT_WINDOW_SECONDS)
        except Exception:
            # Fail open on Redis outage; adjust per your operational policy
            return True

    def _wrap_receive(self, receive: Receive, send: Send) -> Receive:
        """Enforce body size limit for chunked transfer encoding."""
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                received += len(body)
                if received > MAX_BODY_SIZE:
                    await self._deny_request(send, 413, "Body too large")
                    raise _BodyTooLargeError
            return message

        return limited_receive

    @staticmethod
    async def _deny_request(
        send: Send,
        status_code: int,
        reason: str,
        retry_after: Optional[int] = None,
    ) -> None:
        headers = [
            (b"content-type", b"application/problem+json"),
            (b"x-ddos-guard", b"blocked"),
        ]
        if retry_after:
            headers.append((b"retry-after", str(retry_after).encode()))

        problem_type = {
            400: "urn:govdata:error:bad-request",
            403: "urn:govdata:error:forbidden",
            405: "urn:govdata:error:method-not-allowed",
            408: "urn:govdata:error:timeout",
            413: "urn:govdata:error:payload-too-large",
            414: "urn:govdata:error:uri-too-long",
            429: "urn:govdata:error:rate-limit",
        }.get(status_code, "urn:govdata:error:blocked")

        response_body = json.dumps(
            {
                "type": problem_type,
                "title": reason,
                "status": status_code,
                "detail": reason,
            }
        ).encode()

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": response_body,
                "more_body": False,
            }
        )


class SecurityHeadersMiddleware:
    """Adds hardened HTTP response headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
                headers["X-XSS-Protection"] = "1; mode=block"
                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
                headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                message["headers"] = headers.raw
            await send(message)

        await self.app(scope, receive, send_wrapper)