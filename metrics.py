"""
Lightweight event metrics for the Restaurant Booking Tracker.

Single ``log(event_type, **fields)`` entry point writes one document to the
``metrics`` Mongo collection. Writes are best-effort: any failure is swallowed
and logged at debug — metrics must never break the hot path.

Event types (free-form, but conventional):
    check_start, check_done
    fetch_api, fetch_scraper
    cache_hit, cache_miss
    slot_delta
    rate_limit_wait
    burst_release_on, burst_diff_on, burst_off
    alert_sent, alert_failed
    login_attempt, login_success, login_failed
"""

from __future__ import annotations

import logging
import threading
import time as _time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# api_call records flow through this logger. Force DEBUG so probe handlers
# attached to it always receive emissions regardless of root level.
_api_logger = logging.getLogger("api")
_api_logger.setLevel(logging.DEBUG)

_indexes_ready = False
_idx_lock = threading.Lock()


def _ensure_indexes() -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    with _idx_lock:
        if _indexes_ready:
            return
        try:
            from database import get_db
            db = get_db()
            db.metrics.create_index([("event_type", 1), ("ts", -1)])
            db.metrics.create_index([("watch_id", 1), ("ts", -1)])
            db.metrics.create_index([("ts", -1)])
            _indexes_ready = True
        except Exception as exc:
            logger.debug("metrics: index create deferred: %s", exc)


def log(event_type: str, **fields: Any) -> None:
    """Insert a metrics event. Never raises."""
    try:
        _ensure_indexes()
        from database import get_db
        doc = {"event_type": event_type, "ts": datetime.now(timezone.utc), **fields}
        get_db().metrics.insert_one(doc)
    except Exception as exc:
        logger.debug("metrics.log(%s) failed: %s", event_type, exc)


class Timer:
    """Context manager: emit one event with ``duration_ms`` on exit."""

    def __init__(self, event_type: str, **fields: Any):
        self.event_type = event_type
        self.fields = fields
        self._t0 = 0.0

    def __enter__(self) -> "Timer":
        self._t0 = _time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        duration_ms = (_time.perf_counter() - self._t0) * 1000
        fields = dict(self.fields)
        fields["duration_ms"] = round(duration_ms, 2)
        if exc_type is not None:
            fields["error"] = repr(exc)
        log(self.event_type, **fields)


class api_call:  # noqa: N801 — used as a context manager, lowercase reads nicer
    """
    Context manager that logs an outbound API/scraper call with consistent
    structure to:
      1. the metrics MongoDB collection (via ``log()``)
      2. the stdlib logger named ``api`` (which a dedicated handler routes to
         ``logs/api.log`` via the ``is_api_call`` flag on each LogRecord).

    Usage::

        with api_call("resy.get_slots", venue=v, date=d, party=p) as call:
            response = http.get(...)
            call["slots"] = len(response)

    On exit, the call's duration and outcome (success/error) are recorded.
    Any field added to ``call[...]`` is included in both sinks.
    """

    def __init__(self, name: str, **fields: Any):
        self.name   = name
        self.fields = dict(fields)
        self._t0    = 0.0

    def __setitem__(self, key: str, value: Any) -> None:
        self.fields[key] = value

    def update(self, **kw: Any) -> None:
        self.fields.update(kw)

    def __enter__(self) -> "api_call":
        self._t0 = _time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        dur_ms = round((_time.perf_counter() - self._t0) * 1000, 2)
        ok     = exc_type is None
        rec    = dict(self.fields)
        rec.update(name=self.name, ok=ok, duration_ms=dur_ms)
        if exc is not None:
            rec["error"] = repr(exc)

        # 1. Persist to metrics collection
        log("api_call", **rec)

        # 2. Emit to stdlib logger with flag so a dedicated handler can pick it up
        try:
            kv = " ".join(f"{k}={v!r}" for k, v in self.fields.items() if k != "name")
            line = f"[{self.name}] ok={ok} dur={dur_ms}ms"
            if kv:
                line += f"  {kv}"
            if not ok:
                line += f"  err={rec.get('error')}"
            level = logging.INFO if ok else logging.WARNING
            _api_logger.log(level, line, extra={"is_api_call": True})
        except Exception:
            pass


def log_fallback(from_path: str, to_path: str, reason: str, **fields: Any) -> None:
    """
    Log a fallback transition (e.g. Resy API → Playwright scraper).
    Emits to both metrics collection and the API logger so fallback patterns
    are easy to grep.
    """
    log("fallback", from_path=from_path, to_path=to_path, reason=reason, **fields)
    try:
        kv = " ".join(f"{k}={v!r}" for k, v in fields.items())
        logging.getLogger("api").warning(
            "[fallback] %s → %s  reason=%s %s",
            from_path, to_path, reason, kv,
            extra={"is_api_call": True},
        )
    except Exception:
        pass


def cleanup_old_metrics(days: int = 14) -> int:
    """Prune metrics older than ``days`` days. Returns count deleted."""
    try:
        from database import get_db
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        n = get_db().metrics.delete_many({"ts": {"$lt": cutoff}}).deleted_count
        if n:
            logger.info("metrics: pruned %d events older than %dd", n, days)
        return n
    except Exception as exc:
        logger.debug("metrics cleanup failed: %s", exc)
        return 0
