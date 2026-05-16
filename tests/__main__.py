"""Entry point: `python -m tests` runs everything."""

import sys
from tests._runner import run_all

UNIT_MODULES = [
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
]


def main(argv):
    modules = argv[1:] if len(argv) > 1 else UNIT_MODULES
    return run_all(modules)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
