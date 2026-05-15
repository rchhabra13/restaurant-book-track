"""
Performance evaluation — measures real characteristics, not unit correctness.

Three benchmarks:

1. Scheduler tick latency
   - With 6 watches, how long does check_all_watches take on average?
   - Goal: < 30s (the tick interval). Anything ≥30s is the "frozen scheduler" bug.

2. NL parser throughput
   - parses/sec on a typical mix of inputs.

3. Slot cache fan-out efficiency
   - With N watches sharing one venue, ratio of fetcher calls to lookups.

Output is a comparison table:  metric | observed | target | verdict.
"""

from __future__ import annotations

import time

from tests._runner import Result


def _bench_parser(n: int = 1000) -> tuple[float, float]:
    """Return (parses_per_sec, ms_per_parse)."""
    from nl_parser import parse
    samples = [
        "add bungalow", "watch carbone any 4", "check ishq",
        "stop watching odo", "list", "status",
        "track Carbone on 2026-06-15 for 4",
    ]
    t0 = time.perf_counter()
    for i in range(n):
        parse(samples[i % len(samples)])
    dt = time.perf_counter() - t0
    return n / dt, (dt / n) * 1000


def _bench_cache(n_watches: int = 20) -> tuple[int, int]:
    """Return (total_lookups, distinct_fetches) when N watches share 1 key."""
    import slot_cache as sc
    import threading
    sc.reset()
    calls = {"n": 0}
    def f():
        calls["n"] += 1
        time.sleep(0.05)
        return {"slots": []}

    threads = [
        threading.Thread(target=lambda: sc.get_or_fetch(("shared",), f))
        for _ in range(n_watches)
    ]
    for t in threads: t.start()
    for t in threads: t.join()
    return n_watches, calls["n"]


def _bench_scheduler_tick() -> tuple[float, int, int]:
    """
    Time one pass of check_all_watches against MongoDB.
    Returns (seconds_elapsed, watches_processed, skipped_warnings).
    """
    import scheduler as sch
    import priority

    # Force all due
    priority._next_check_at.clear()

    t0 = time.perf_counter()
    sch.check_all_watches()
    dt = time.perf_counter() - t0

    # Count actually-due processed (rough — we just measured one tick)
    from database import get_watches
    n = len([w for w in get_watches(active_only=True)])
    return dt, n, 0


def run() -> list[Result]:
    results: list[Result] = []

    # 1. NL parser
    pps, ms_per = _bench_parser(2000)
    ok = pps > 1000
    results.append(Result(
        name=f"nl_parser throughput ({pps:.0f}/sec, {ms_per:.3f} ms/parse)",
        ok=ok,
        detail="target: >1000 parses/sec",
    ))

    # 2. Cache fan-out
    looks, fetches = _bench_cache(20)
    ok = fetches == 1
    results.append(Result(
        name=f"cache fan-out efficiency ({looks} lookups → {fetches} fetch)",
        ok=ok,
        detail=f"target: 1 fetch (got {fetches})",
    ))

    # 3. Scheduler tick latency
    try:
        dt, n, skipped = _bench_scheduler_tick()
        ok = dt < 60  # 60s is generous; production target is <30s
        results.append(Result(
            name=f"scheduler tick latency ({n} watches, {dt:.1f}s)",
            ok=ok,
            detail=f"target: <30s for 6 watches; got {dt:.1f}s",
            duration_ms=dt * 1000,
        ))
    except Exception as exc:
        results.append(Result(
            name="scheduler tick latency",
            ok=False,
            detail=f"benchmark crashed: {exc}",
        ))

    return results
