"""Shared request dependencies: database access, profile thresholds,
rate limiting."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from jobseeker import profile as profile_mod
from jobseeker.database import get_engine


def engine():
    return get_engine()


def thresholds(conn, account: str) -> dict:
    record = profile_mod.get_or_seed(conn, account)
    t = record["data"]["thresholds"]
    return {"review": float(t["review"]), "high_priority": float(t["high_priority"]), "stale_days": int(t["stale_days"])}


class RateLimiter:
    """Sliding-window limit per (account, action) for endpoints that cost
    money or hit third parties (LLM calls, Gmail, Drive, live checks).
    In-process is enough: the free tier runs a single instance."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, account: str, action: str, limit: int, per_seconds: int = 60) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[(account, action)]
            while hits and now - hits[0] > per_seconds:
                hits.popleft()
            if len(hits) >= limit:
                raise HTTPException(429, f"Too many {action} requests; wait a minute and try again.")
            hits.append(now)


limiter = RateLimiter()
