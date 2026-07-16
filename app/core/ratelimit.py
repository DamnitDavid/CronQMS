"""Lightweight in-process rate limiting for abuse-prone endpoints.

A best-effort, per-process limiter backed by an in-memory sliding window keyed
by client IP. It exists to blunt online password brute-force and setup spam.

Limitations (read before relying on it): the counters live in this process's
memory, so they are **not shared across multiple workers/replicas** and reset on
restart. For a multi-worker or multi-host deployment, front the app with an edge
rate limiter (nginx, Cloudflare, an API gateway) or swap the store here for a
shared backend such as Redis. This is defense-in-depth, not the only line.
"""

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.config import get_settings

# Environments where the limiter is a no-op (keeps the test suite, which drives
# many logins from a single client, from tripping the limit).
_EXEMPT_ENVIRONMENTS = {"development", "test", "testing", "local"}


class SlidingWindowLimiter:
    """Fixed-count sliding-window counter, keyed by an arbitrary string."""

    def __init__(self, max_hits: int, window_seconds: float) -> None:
        self.max_hits = max_hits
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str) -> int | None:
        """Record an attempt for ``key``.

        Returns ``None`` if the attempt is within the allowed rate, or the
        number of seconds the caller should wait before retrying if the limit
        has been reached (in which case the attempt is *not* recorded).
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.max_hits:
                return int(hits[0] + self.window_seconds - now) + 1
            hits.append(now)
            return None


def rate_limit(max_hits: int, window_seconds: float, name: str):
    """Build a FastAPI dependency enforcing ``max_hits`` per ``window_seconds``.

    Keyed by client IP + ``name`` so different endpoints have independent
    budgets. Raises HTTP 429 with a ``Retry-After`` header when exceeded. The
    limiter is inert in development/test environments (see module docstring).
    """
    limiter = SlidingWindowLimiter(max_hits, window_seconds)

    async def dependency(request: Request) -> None:
        if get_settings().environment.strip().lower() in _EXEMPT_ENVIRONMENTS:
            return
        client_ip = request.client.host if request.client else "unknown"
        retry_after = limiter.hit(f"{name}:{client_ip}")
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait and try again.",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency
