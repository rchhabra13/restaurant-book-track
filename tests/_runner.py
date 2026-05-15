"""
Minimal test harness — no pytest dependency.

Each test module exposes a top-level ``run() -> list[Result]`` function.
``Result`` is a small dataclass; this module renders a results table.

Usage:
    python -m tests
    python -m tests.unit.slot_cache   # individual module
"""

from __future__ import annotations

import importlib
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class Result:
    name:     str
    ok:       bool
    detail:   str = ""
    duration_ms: float = 0.0
    expected: object = None
    actual:   object = None


@dataclass
class Suite:
    module:  str
    results: List[Result] = field(default_factory=list)

    @property
    def passed(self) -> int: return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int: return sum(1 for r in self.results if not r.ok)

    @property
    def total(self) -> int: return len(self.results)


def check(name: str, expected, actual, *, eq: Optional[Callable] = None) -> Result:
    """Build a Result by comparing expected vs actual. Default == comparison."""
    cmp = eq or (lambda a, b: a == b)
    ok = bool(cmp(expected, actual))
    detail = "" if ok else f"expected={expected!r} got={actual!r}"
    return Result(name=name, ok=ok, detail=detail, expected=expected, actual=actual)


def run_test(name: str, fn: Callable[[], None]) -> Result:
    """Wrap a callable: catches exceptions, times it, returns Result."""
    t0 = time.perf_counter()
    try:
        fn()
        ok, detail = True, ""
    except AssertionError as exc:
        ok, detail = False, f"AssertionError: {exc}"
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}"
    dur = (time.perf_counter() - t0) * 1000
    return Result(name=name, ok=ok, detail=detail, duration_ms=round(dur, 2))


def print_suite(suite: Suite) -> None:
    print(f"\n── {suite.module} " + "─" * (60 - len(suite.module)))
    for r in suite.results:
        icon = "✅" if r.ok else "❌"
        line = f"  {icon} {r.name}"
        if r.duration_ms:
            line += f"  ({r.duration_ms:.1f}ms)"
        print(line)
        if not r.ok and r.detail:
            for det_line in r.detail.split("\n"):
                if det_line.strip():
                    print(f"      {det_line}")


def run_all(modules: List[str]) -> int:
    """Run a list of test modules; return non-zero exit code on any failure."""
    suites: List[Suite] = []
    for mod_name in modules:
        try:
            mod = importlib.import_module(mod_name)
            results = mod.run()
            suites.append(Suite(module=mod_name, results=results))
        except Exception as exc:
            suites.append(Suite(module=mod_name, results=[
                Result(name="<import>", ok=False, detail=f"{type(exc).__name__}: {exc}"),
            ]))

    total_ok = total_fail = 0
    for s in suites:
        print_suite(s)
        total_ok   += s.passed
        total_fail += s.failed

    print("\n" + "═" * 60)
    icon = "✅" if total_fail == 0 else "❌"
    print(f"{icon} Total: {total_ok + total_fail}   Passed: {total_ok}   Failed: {total_fail}")
    print("═" * 60)
    return 0 if total_fail == 0 else 1
