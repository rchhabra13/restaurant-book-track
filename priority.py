"""
Per-watch adaptive polling interval.

Replaces a single global ``CHECK_INTERVAL_MINUTES`` with a per-watch cadence
that scales by days-to-target. Hot watches (target within a few days) poll
faster than cold ones (>2 weeks out) to catch cancellation slots quickly
without burning request budget on long-horizon watches.

Scheduler usage:
    if is_due(watch):
        check_single_watch(watch)
        mark_checked(watch["id"], compute_interval_seconds(watch))
"""

from __future__ import annotations

import threading
import time as _time
from datetime import date as _date

# Thresholds (days_until_target → seconds between checks).
INTERVAL_HOT_SECONDS    = 60     # target ≤ 3 days out
INTERVAL_WARM_SECONDS   = 300    # target ≤ 14 days out
INTERVAL_COLD_SECONDS   = 900    # target > 14 days out
INTERVAL_EXPIRED_SECONDS = 3600  # target in past — slow-poll until cleanup

_next_check_at: dict[str, float] = {}
_lock = threading.Lock()


def compute_interval_seconds(watch: dict) -> int:
    """
    Pick base interval from days-to-target. Range/any watches use the
    earliest in-window date (most urgent end of the range).
    """
    try:
        if watch.get("date_mode") in ("range", "any"):
            target = watch.get("date_from") or watch.get("target_date")
        else:
            target = watch["target_date"]
        d = _date.fromisoformat(target)
        days = (d - _date.today()).days
    except Exception:
        return INTERVAL_WARM_SECONDS

    if days < 0:
        return INTERVAL_EXPIRED_SECONDS
    if days <= 3:
        return INTERVAL_HOT_SECONDS
    if days <= 14:
        return INTERVAL_WARM_SECONDS
    return INTERVAL_COLD_SECONDS


def is_due(watch: dict) -> bool:
    """Return True if the watch's next scheduled check time has arrived."""
    wid = watch["id"]
    with _lock:
        return _time.time() >= _next_check_at.get(wid, 0.0)


def mark_checked(watch_id: str, interval_seconds: int) -> None:
    """Record that ``watch_id`` was just checked; next check in ``interval_seconds``."""
    with _lock:
        _next_check_at[watch_id] = _time.time() + interval_seconds


def forget(watch_id: str) -> None:
    """Drop scheduling state for a deactivated watch."""
    with _lock:
        _next_check_at.pop(watch_id, None)
