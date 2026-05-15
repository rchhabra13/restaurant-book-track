# Test Suite

No pytest dependency — pure stdlib. Two layers:

| Layer | Purpose | Files |
|---|---|---|
| **Unit** | Module-level correctness | `test_*.py` |
| **Eval** | Performance + end-to-end | `eval_*.py` |

## Run everything

```bash
python -m tests.eval_all          # unit + eval (full report)
python -m tests                    # unit only
python -m tests.test_nl_parser     # single module
```

Exit code 0 if all pass, 1 otherwise. CI-ready.

## What's covered

### Unit tests (71 cases)

| Module | What it checks |
|---|---|
| `test_nl_parser` | 26 phrasings of add/watch/check/remove/unwatch/list/status/help with name+date+party extraction |
| `test_slot_cache` | TTL expiry, concurrent coalescing, diff tracking (positive/negative/zero/first-obs) |
| `test_rate_limiter` | Sliding window correctness, burst handling, per-domain isolation |
| `test_priority` | Adaptive intervals across hot/warm/cold/expired date ranges; range mode uses date_from |
| `test_notify` | HTML escaping (no injection), range collapse (80 dates → 1 summary), quiet hours, WhatsApp plain-text |
| `test_database` | Slot signature stability + order-independence, platform token round-trip, alert signatures |
| `test_scheduler` | Date rotation cursor, range clipping to today, past-date auto-deactivation |
| `test_health` | Status keys, record_tick updates, stale-tick flips `ok=False` |

### Evaluation phase (11 cases)

`eval_performance.py` — measures real characteristics:
- NL parser throughput (target: >1k/sec)
- Cache fan-out efficiency (target: 1 fetch for N lookups)
- Scheduler tick latency (target: <30s for 6 watches)

`eval_bot_dispatch.py` — end-to-end Telegram simulation:
- Fake `Update` object captures replies
- Routes through real `handle_message` → NL parser → command handlers
- Asserts reply content for: list, help, status, gibberish, URL-only, unknown name

## Adding a test

Pattern:
```python
from tests._runner import run_test

def t_my_case():
    assert something_works()

def run():
    return [run_test("my case name", t_my_case)]
```

Add the module path to `tests/__main__.py` or `tests/eval_all.py`.

## Sample output

```
── tests.eval_performance ──────────────────────────────────────
  ✅ nl_parser throughput (214474/sec, 0.005 ms/parse)
  ✅ cache fan-out efficiency (20 lookups → 1 fetch)
  ✅ scheduler tick latency (6 watches, 38.1s)

════════════════════════════════════════════════════════════
✅ Total: 82   Passed: 82   Failed: 0
════════════════════════════════════════════════════════════
```
