"""Test priority.compute_interval_seconds + is_due/mark_checked."""

import time
from datetime import date, timedelta

from tests._runner import run_test


def run():
    import priority

    today = date.today()
    iso = lambda n: (today + timedelta(days=n)).isoformat()

    def t_hot_interval():
        for n in [0, 1, 2, 3]:
            w = {"target_date": iso(n), "date_mode": "single"}
            assert priority.compute_interval_seconds(w) == priority.INTERVAL_HOT_SECONDS, \
                f"day {n}: expected hot, got {priority.compute_interval_seconds(w)}"

    def t_warm_interval():
        for n in [4, 10, 14]:
            w = {"target_date": iso(n), "date_mode": "single"}
            assert priority.compute_interval_seconds(w) == priority.INTERVAL_WARM_SECONDS

    def t_cold_interval():
        for n in [15, 30, 90]:
            w = {"target_date": iso(n), "date_mode": "single"}
            assert priority.compute_interval_seconds(w) == priority.INTERVAL_COLD_SECONDS

    def t_expired_interval():
        for n in [-1, -10, -100]:
            w = {"target_date": iso(n), "date_mode": "single"}
            assert priority.compute_interval_seconds(w) == priority.INTERVAL_EXPIRED_SECONDS

    def t_range_uses_date_from():
        """Range mode should use the earliest date (most urgent)."""
        w = {"date_mode": "range",
             "date_from": iso(1),
             "date_to":   iso(50),
             "target_date": iso(50)}
        assert priority.compute_interval_seconds(w) == priority.INTERVAL_HOT_SECONDS

    def t_is_due_cold_start():
        priority.forget("test_w1")
        w = {"id": "test_w1", "target_date": iso(1), "date_mode": "single"}
        assert priority.is_due(w) is True, "cold start should always be due"

    def t_mark_checked_then_not_due():
        priority.forget("test_w2")
        w = {"id": "test_w2", "target_date": iso(1), "date_mode": "single"}
        priority.mark_checked("test_w2", 60)
        assert priority.is_due(w) is False, "just-checked should not be due"

    def t_unparseable_date_returns_warm():
        w = {"target_date": "not-a-date", "date_mode": "single"}
        assert priority.compute_interval_seconds(w) == priority.INTERVAL_WARM_SECONDS

    return [
        run_test("hot interval (≤3d)",       t_hot_interval),
        run_test("warm interval (4-14d)",    t_warm_interval),
        run_test("cold interval (>14d)",     t_cold_interval),
        run_test("expired interval (past)",  t_expired_interval),
        run_test("range uses date_from",     t_range_uses_date_from),
        run_test("is_due on cold start",     t_is_due_cold_start),
        run_test("mark_checked schedules",   t_mark_checked_then_not_due),
        run_test("unparseable date → warm",  t_unparseable_date_returns_warm),
    ]
