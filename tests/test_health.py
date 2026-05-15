"""Test health module: status builder + record_tick."""

import time

from tests._runner import run_test


def run():
    import health

    def t_build_status_keys():
        s = health._build_status()
        for key in ("ok", "uptime_s", "last_tick_age_s", "mongo_ok",
                    "active_watches", "ts"):
            assert key in s, f"missing key: {key}"

    def t_record_tick_updates():
        health._last_tick_at = 0
        before = health._build_status()["last_tick_age_s"]
        health.record_tick()
        time.sleep(0.05)
        after = health._build_status()["last_tick_age_s"]
        assert before is None, f"before should be None, got {before}"
        assert after is not None and after < 1, f"after should be ~0, got {after}"

    def t_stale_tick_flips_ok():
        """Last tick > 5min ago → ok=False (assuming mongo is ok)."""
        original = health._last_tick_at
        health._last_tick_at = time.time() - 400  # 400s ago
        s = health._build_status()
        health._last_tick_at = original
        assert s["last_tick_age_s"] >= 400
        # ok depends on mongo too — only assert if mongo is up
        if s["mongo_ok"]:
            assert s["ok"] is False, "stale tick should flip ok=False"

    return [
        run_test("status has all keys",    t_build_status_keys),
        run_test("record_tick updates age", t_record_tick_updates),
        run_test("stale tick flips ok",     t_stale_tick_flips_ok),
    ]
