"""In-process sliding-window rate limiter for the retrieval surface.

First line of defence against a single client burning the embedding
budget on the public ``/v1/search``. Per-process only: with N replicas
the effective limit is N× the configured value — point a real limiter
(ingress / API gateway) at serious traffic and treat this as a fuse,
not a meter.
"""

from __future__ import annotations

import time
from collections import deque

WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    """Per-key sliding window over the last 60 seconds."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str, limit: int) -> bool:
        """Record one event for ``key`` and return whether it fits the
        per-minute ``limit``. ``limit <= 0`` disables limiting."""
        if limit <= 0:
            return True
        now = time.monotonic()
        q = self._events.setdefault(key, deque())
        cutoff = now - WINDOW_SECONDS
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        # Opportunistic GC so abandoned keys don't accumulate forever.
        if len(self._events) > 10_000:
            for k in [k for k, v in self._events.items() if not v]:
                del self._events[k]
        return True
