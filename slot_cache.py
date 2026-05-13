"""
In-process slot cache + diff tracker.

Two purposes:
1. Fan-out cache — multiple watches on the same (platform, venue, date, party_size)
   share a single API call within a short TTL. Cuts request volume linearly with
   watch overlap and coalesces concurrent fetches via a per-key lock.

2. Diff tracker — records last-seen slot count per key. record_and_diff() returns
   the delta vs. previous observation so the scheduler can accelerate polling on
   positive deltas (cancellation cascades).

Thread-safe. No external dependencies.
"""

from __future__ import annotations

import threading
import time as _time
from typing import Any, Callable, Hashable

DEFAULT_TTL_SECONDS = 5.0

_cache: dict[Hashable, tuple[float, Any]] = {}
_locks: dict[Hashable, threading.Lock] = {}
_meta_lock = threading.Lock()

_last_count: dict[Hashable, int] = {}
_diff_lock = threading.Lock()


def get_or_fetch(key: Hashable, fetcher: Callable[[], Any], ttl: float = DEFAULT_TTL_SECONDS) -> Any:
    """
    Return cached value if fresh; otherwise call ``fetcher`` under a per-key
    lock so concurrent callers for the same key share one network round-trip.
    """
    import metrics  # local import to avoid circular at module load

    now = _time.time()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        metrics.log("cache_hit", key=str(key))
        return hit[1]

    with _meta_lock:
        lock = _locks.setdefault(key, threading.Lock())

    with lock:
        hit = _cache.get(key)
        if hit is not None and _time.time() - hit[0] < ttl:
            metrics.log("cache_hit", key=str(key), coalesced=True)
            return hit[1]
        metrics.log("cache_miss", key=str(key))
        t0 = _time.perf_counter()
        value = fetcher()
        dur = round((_time.perf_counter() - t0) * 1000, 2)
        _cache[key] = (_time.time(), value)
        metrics.log("fetch_done", key=str(key), duration_ms=dur,
                    slots=len(value.get("slots", [])) if isinstance(value, dict) else 0)
        return value


def record_and_diff(key: Hashable, count: int) -> int:
    """
    Update last-seen count for ``key`` and return ``count - previous``.
    First observation returns 0 (no baseline).
    """
    import metrics

    with _diff_lock:
        prev = _last_count.get(key)
        _last_count[key] = count
    delta = 0 if prev is None else count - prev
    if delta != 0:
        metrics.log("slot_delta", key=str(key), prev=prev, new=count, delta=delta)
    return delta


def reset() -> None:
    """Clear all cache + diff state. For tests."""
    with _meta_lock:
        _cache.clear()
        _locks.clear()
    with _diff_lock:
        _last_count.clear()
