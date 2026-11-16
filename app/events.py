"""Process-local SSE fan-out."""

from __future__ import annotations

import asyncio
import threading


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[tuple[asyncio.Queue[str], asyncio.AbstractEventLoop]] = []
        self._lock = threading.Lock()

    async def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        entry = (queue, asyncio.get_running_loop())
        with self._lock:
            self._subscribers.append(entry)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        with self._lock:
            self._subscribers = [item for item in self._subscribers if item[0] is not queue]

    def publish(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue)
            except RuntimeError:
                # The event loop closed between the snapshot and this call.
                continue

    @staticmethod
    def _offer(queue: asyncio.Queue[str]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait("refresh")
        except asyncio.QueueFull:
            pass
