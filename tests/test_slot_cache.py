"""Test slot_cache fan-out + diff tracking + concurrency."""

import threading
import time

from tests._runner import run_test


def run():
    import slot_cache as sc
    sc.reset()

    def t_basic_hit_miss():
        calls = {"n": 0}
        def f():
            calls["n"] += 1
            return {"slots": [1, 2, 3]}
        a = sc.get_or_fetch(("k1",), f)
        b = sc.get_or_fetch(("k1",), f)
        assert a is b, "cache should return same object"
        assert calls["n"] == 1, f"expected 1 fetch, got {calls['n']}"

    def t_ttl_expiry():
        sc.reset()
        calls = {"n": 0}
        def f():
            calls["n"] += 1
            return {"slots": []}
        sc.get_or_fetch(("k2",), f, ttl=0.05)
        time.sleep(0.1)
        sc.get_or_fetch(("k2",), f, ttl=0.05)
        assert calls["n"] == 2, f"TTL expired key should refetch, got {calls['n']}"

    def t_concurrent_coalesce():
        """N threads hitting same key simultaneously should call fetcher exactly once."""
        sc.reset()
        calls = {"n": 0}
        barrier = threading.Barrier(8)
        def f():
            barrier.wait()  # synchronize so all hit at once
            calls["n"] += 1
            time.sleep(0.05)
            return {"slots": []}

        # Need a slower fetcher so threads queue at the per-key lock
        def slow():
            time.sleep(0.05)
            calls["n"] += 1
            return {"slots": []}

        threads = [
            threading.Thread(target=lambda: sc.get_or_fetch(("k3",), slow))
            for _ in range(8)
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        assert calls["n"] == 1, f"coalesced should be 1, got {calls['n']}"

    def t_diff_first_observation():
        sc.reset()
        assert sc.record_and_diff(("d1",), 5) == 0, "first observation = 0 delta"

    def t_diff_positive():
        sc.reset()
        sc.record_and_diff(("d2",), 5)
        assert sc.record_and_diff(("d2",), 8) == 3, "5 → 8 should be +3"

    def t_diff_negative():
        sc.reset()
        sc.record_and_diff(("d3",), 5)
        assert sc.record_and_diff(("d3",), 2) == -3, "5 → 2 should be -3"

    def t_diff_zero():
        sc.reset()
        sc.record_and_diff(("d4",), 5)
        assert sc.record_and_diff(("d4",), 5) == 0, "no change = 0"

    return [
        run_test("basic hit/miss",         t_basic_hit_miss),
        run_test("TTL expiry",             t_ttl_expiry),
        run_test("concurrent coalesce",    t_concurrent_coalesce),
        run_test("diff first observation", t_diff_first_observation),
        run_test("diff positive",          t_diff_positive),
        run_test("diff negative",          t_diff_negative),
        run_test("diff zero (no change)",  t_diff_zero),
    ]
