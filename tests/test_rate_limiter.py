"""Test rate_limiter.TokenBucket sliding window correctness."""

import threading
import time

from tests._runner import run_test


def run():
    from rate_limiter import TokenBucket, acquire as global_acquire, _buckets

    def t_rate_not_exceeded():
        """10 tokens/sec → 11th call must wait at least ~1s."""
        b = TokenBucket(rate=5, per=0.5)  # 5 per 500ms
        t0 = time.perf_counter()
        for _ in range(5):
            b.acquire()
        dt_after_burst = time.perf_counter() - t0
        assert dt_after_burst < 0.1, f"burst should be instant, took {dt_after_burst:.2f}s"
        b.acquire()  # 6th must wait ~0.5s
        dt_total = time.perf_counter() - t0
        assert dt_total >= 0.45, f"6th token should wait ~0.5s, took {dt_total:.2f}s"

    def t_sliding_window():
        """After waiting past `per`, fresh budget available."""
        b = TokenBucket(rate=3, per=0.3)
        for _ in range(3):
            b.acquire()
        time.sleep(0.35)
        t0 = time.perf_counter()
        for _ in range(3):
            b.acquire()
        dt = time.perf_counter() - t0
        assert dt < 0.05, f"fresh window should be instant, took {dt:.2f}s"

    def t_concurrent_acquire():
        """N threads all hit the same bucket → total time covers extra windows."""
        b = TokenBucket(rate=5, per=0.5)
        threads = [threading.Thread(target=b.acquire) for _ in range(11)]
        t0 = time.perf_counter()
        for t in threads: t.start()
        for t in threads: t.join()
        total = time.perf_counter() - t0
        # 11 tokens: first 5 instant; next 5 wait ~0.5s; 11th waits another window.
        # So at least 1.0s total.
        assert total >= 0.9, f"11 tokens at 5/0.5s should take ≥0.9s, got {total:.2f}s"
        # And not absurdly slow either
        assert total < 2.0, f"should finish in ≤2s, took {total:.2f}s"

    def t_global_acquire_per_domain():
        """Separate domains have separate buckets."""
        _buckets.clear()
        t0 = time.perf_counter()
        # Resy bucket is 10/sec — 5 acquires should be instant
        for _ in range(5):
            global_acquire("resy")
        dt = time.perf_counter() - t0
        assert dt < 0.1, f"5 resy tokens should be instant, took {dt:.2f}s"

    return [
        run_test("rate not exceeded",       t_rate_not_exceeded),
        run_test("sliding window resets",   t_sliding_window),
        run_test("concurrent acquire",      t_concurrent_acquire),
        run_test("global per-domain bucket", t_global_acquire_per_domain),
    ]
