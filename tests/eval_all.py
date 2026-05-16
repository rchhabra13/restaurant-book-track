"""Run BOTH unit suites AND evaluation phases — full report."""

import sys
from tests._runner import run_all

ALL_MODULES = [
    # Unit
    "tests.test_nl_parser",
    "tests.test_slot_cache",
    "tests.test_rate_limiter",
    "tests.test_priority",
    "tests.test_notify",
    "tests.test_database",
    "tests.test_scheduler",
    "tests.test_health",
    "tests.test_logging",
    "tests.test_ingest",
    # Evaluation phase
    "tests.eval_performance",
    "tests.eval_bot_dispatch",
]


if __name__ == "__main__":
    sys.exit(run_all(ALL_MODULES))
