"""Small in-process sliding-window limiter.

The deployment uses one Uvicorn worker because SQLite and the SSE fan-out are
process-local. At event scale (<=70 phones) this is sufficient and avoids an
extra Redis dependency.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Record a hit and return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            queue = self._hits[key]
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if len(queue) >= limit:
                retry_after = max(1, int(window_seconds - (now - queue[0])) + 1)
                return False, retry_after
            queue.append(now)
            return True, 0

    def count(self, key: str, *, window_seconds: int) -> int:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            queue = self._hits.get(key)
            if not queue:
                return 0
            while queue and queue[0] <= cutoff:
                queue.popleft()
            return len(queue)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
