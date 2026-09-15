"""Global per-session progress queues for SSE streaming."""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

_progress_queues: Dict[str, asyncio.Queue] = {}


def register_progress_queue(session_id: str) -> asyncio.Queue:
    queue = asyncio.Queue()
    _progress_queues[session_id] = queue
    return queue


def get_progress_queue(session_id: str) -> Optional[asyncio.Queue]:
    return _progress_queues.get(session_id)


def unregister_progress_queue(session_id: str) -> None:
    _progress_queues.pop(session_id, None)


async def push_progress(session_id: str, event: dict) -> None:
    queue = get_progress_queue(session_id)
    if queue:
        await queue.put(event)