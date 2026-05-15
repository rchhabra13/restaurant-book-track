"""
Centralised logging config.

Three sinks:
  1. Console  — INFO+ in compact format, one line per event.
  2. logs/app.log — full DEBUG+ in JSON-ish structured format, rotated daily.
  3. logs/api.log — only api_call events (network round trips), so you can
                    grep for slow/failing fetches without scrolling through
                    scheduler chatter.

Call ``configure(level="INFO")`` once at the entrypoint (bot.py / scheduler.py /
app.py all do this). Subsequent calls are no-ops.

Per-event structure (in files):
    2026-05-15 10:30:12,345 INFO  api_client          [resy/get_slots venue=12345 date=2026-05-20] ok=True dur=312ms
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

# ── Config knobs (env-overridable) ────────────────────────────────────
LOG_DIR        = Path(os.getenv("LOG_DIR", "logs"))
LOG_LEVEL      = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_RETENTION  = int(os.getenv("LOG_RETENTION_DAYS", "7"))
LOG_TO_FILE    = os.getenv("LOG_TO_FILE", "true").lower() == "true"


# ── Filters ───────────────────────────────────────────────────────────

class _ApiCallOnly(logging.Filter):
    """Pass only records that originated from the api_call metrics context."""
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "is_api_call", False)


# ── Formatters ────────────────────────────────────────────────────────

class _CompactFormatter(logging.Formatter):
    """One-line console format with consistent column widths."""

    def format(self, record: logging.LogRecord) -> str:
        ts   = self.formatTime(record, "%H:%M:%S")
        lvl  = record.levelname.ljust(5)
        mod  = record.name.ljust(20)[:20]
        msg  = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return f"{ts} {lvl} {mod} {msg}"


_configured = False


def configure(level: str | None = None) -> None:
    """Idempotently configure root logging. Safe to call multiple times."""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO))

    # Clear pre-existing handlers (Streamlit and python-telegram-bot install their own)
    root.handlers.clear()

    # 1. Console — compact, INFO+
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(root.level)
    console.setFormatter(_CompactFormatter())
    root.addHandler(console)

    # 2. Rotating file — full detail, DEBUG+
    if LOG_TO_FILE:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)

            app_file = logging.handlers.TimedRotatingFileHandler(
                LOG_DIR / "app.log",
                when="midnight",
                backupCount=LOG_RETENTION,
                encoding="utf-8",
            )
            app_file.setLevel(logging.DEBUG)
            app_file.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-5s %(name)-20s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            root.addHandler(app_file)

            # 3. API-only file
            api_file = logging.handlers.TimedRotatingFileHandler(
                LOG_DIR / "api.log",
                when="midnight",
                backupCount=LOG_RETENTION,
                encoding="utf-8",
            )
            api_file.setLevel(logging.DEBUG)
            api_file.addFilter(_ApiCallOnly())
            api_file.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-5s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            root.addHandler(api_file)
        except Exception as exc:
            # File logging is best-effort; never let it crash the app
            root.warning("File logging unavailable: %s", exc)

    # Tame noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio",
                  "telegram.ext.Application", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
