"""Test api_call + log_fallback emit to both metrics DB and the api logger."""

import logging
import time

from tests._runner import run_test


def run():
    from metrics import api_call, log_fallback
    from database import get_db

    def t_api_call_logs_success():
        """api_call ctx manager should emit a metrics doc with ok=True."""
        db = get_db()
        before = db.metrics.count_documents({"event_type": "api_call",
                                             "name": "__test_ok"})
        with api_call("__test_ok", k="v") as call:
            call["extra"] = 42
            time.sleep(0.01)
        after = db.metrics.count_documents({"event_type": "api_call",
                                            "name": "__test_ok"})
        assert after == before + 1, "should have logged exactly one doc"

        doc = db.metrics.find_one({"event_type": "api_call", "name": "__test_ok"},
                                  sort=[("ts", -1)])
        assert doc["ok"] is True
        assert doc["k"] == "v"
        assert doc["extra"] == 42
        assert doc["duration_ms"] >= 10, f"duration should be ≥10ms, got {doc['duration_ms']}"
        db.metrics.delete_many({"name": "__test_ok"})

    def t_api_call_logs_failure():
        """Raised exceptions should be logged with ok=False and error set."""
        db = get_db()
        try:
            with api_call("__test_fail", x=1):
                raise ValueError("boom")
        except ValueError:
            pass
        doc = db.metrics.find_one({"event_type": "api_call", "name": "__test_fail"},
                                  sort=[("ts", -1)])
        assert doc is not None
        assert doc["ok"] is False
        assert "ValueError" in doc["error"]
        db.metrics.delete_many({"name": "__test_fail"})

    def t_log_fallback_emits():
        """log_fallback should write one doc with from/to/reason."""
        db = get_db()
        log_fallback("resy.api", "scraper", reason="test_reason", venue_id=999)
        doc = db.metrics.find_one({"event_type": "fallback", "venue_id": 999},
                                  sort=[("ts", -1)])
        assert doc is not None
        assert doc["from_path"] == "resy.api"
        assert doc["to_path"] == "scraper"
        assert doc["reason"] == "test_reason"
        db.metrics.delete_many({"event_type": "fallback", "venue_id": 999})

    def t_api_logger_receives_flag():
        """api_call should attach is_api_call=True to the LogRecord."""
        captured: list = []

        class _Probe(logging.Handler):
            def emit(self, record):
                if record.name == "api":
                    captured.append(record)

        probe = _Probe()
        logging.getLogger("api").addHandler(probe)
        try:
            with api_call("__test_flag"):
                pass
        finally:
            logging.getLogger("api").removeHandler(probe)

        assert any(getattr(r, "is_api_call", False) for r in captured), \
            "no record received the is_api_call flag"
        get_db().metrics.delete_many({"name": "__test_flag"})

    def t_logging_config_idempotent():
        """configure() must be safe to call multiple times."""
        from logging_config import configure
        configure()
        configure()
        configure()
        root_handlers = len(logging.getLogger().handlers)
        # Should be a fixed small number — no doubling
        assert root_handlers <= 4, f"handlers ballooned: {root_handlers}"

    return [
        run_test("api_call success path",        t_api_call_logs_success),
        run_test("api_call failure path",        t_api_call_logs_failure),
        run_test("log_fallback writes metric",   t_log_fallback_emits),
        run_test("api logger gets is_api_call",  t_api_logger_receives_flag),
        run_test("configure() idempotent",       t_logging_config_idempotent),
    ]
