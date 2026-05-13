"""
Per-domain sliding-window rate limiter.

One ``TokenBucket`` per platform key. ``acquire(domain)`` blocks until the
caller is allowed to make a request, so all scheduler/burst workers share
the same budget against each upstream API. Prevents IP-level bans when many
watches fan out to the same platform.
"""

from __future__ import annotations

import threading
import time as _time
from collections import deque


class TokenBucket:
    """
    Sliding-window limiter: at most ``rate`` events per ``per`` seconds.
    ``acquire()`` blocks (no drop) — callers always proceed eventually.
    """

    def __init__(self, rate: int, per: float):
        self.rate = rate
        self.per = per
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = _time.time()
                while self._times and now - self._times[0] >= self.per:
                    self._times.popleft()
                if len(self._times) < self.rate:
                    self._times.append(now)
                    return
                wait = self.per - (now - self._times[0])
            _time.sleep(max(wait, 0.01))


# Conservative defaults — override per platform if upstream is more tolerant.
DEFAULTS: dict[str, tuple[int, float]] = {
    "resy":      (10, 1.0),  # 10 req/sec sustained
    "opentable": (5,  1.0),
    "yelp":      (3,  1.0),
    "generic":   (2,  1.0),
}

_buckets: dict[str, TokenBucket] = {}
_buckets_lock = threading.Lock()


def acquire(domain: str) -> None:
    """Block until a request to ``domain`` is permitted."""
    with _buckets_lock:
        bucket = _buckets.get(domain)
        if bucket is None:
            rate, per = DEFAULTS.get(domain, DEFAULTS["generic"])
            bucket = TokenBucket(rate, per)
            _buckets[domain] = bucket
    t0 = _time.perf_counter()
    bucket.acquire()
    wait_ms = round((_time.perf_counter() - t0) * 1000, 2)
    if wait_ms > 50:  # only log notable waits
        import metrics
        metrics.log("rate_limit_wait", domain=domain, wait_ms=wait_ms)
