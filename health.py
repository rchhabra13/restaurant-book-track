"""
Health + observability for the Restaurant Booking Tracker.

Exposes:
  * Sentry integration (optional; activated when ``SENTRY_DSN`` is set in .env).
  * /healthz HTTP endpoint that returns liveness + key operational metrics
    so external uptime monitors can flag a stuck scheduler.

The HTTP server is a tiny stdlib BaseHTTPRequestHandler running in a
background thread — no Flask/FastAPI dependency.

Usage
─────
Call ``init_observability()`` once at startup (scheduler + app.py both do this).
Set ``SENTRY_DSN=...`` and ``HEALTHZ_PORT=...`` in .env.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

logger = logging.getLogger(__name__)

SENTRY_DSN     = os.getenv("SENTRY_DSN", "")
HEALTHZ_PORT   = int(os.getenv("HEALTHZ_PORT", "0"))  # 0 disables the server
HEALTHZ_BIND   = os.getenv("HEALTHZ_BIND", "127.0.0.1")

_started_at      = time.time()
_last_tick_at    = 0.0
_last_tick_age_warn_s = 300  # warn if no tick in > 5 min


def record_tick() -> None:
    """Called by scheduler each successful tick. Updates liveness gauge."""
    global _last_tick_at
    _last_tick_at = time.time()


def _build_status() -> dict:
    try:
        from database import check_connection, get_watches
        mongo_ok = check_connection()
        watch_count = len(get_watches(active_only=True)) if mongo_ok else None
    except Exception:
        mongo_ok = False
        watch_count = None

    last_tick_age = (time.time() - _last_tick_at) if _last_tick_at else None
    healthy = (
        mongo_ok
        and (last_tick_age is None or last_tick_age < _last_tick_age_warn_s)
    )

    return {
        "ok":               healthy,
        "uptime_s":         round(time.time() - _started_at, 1),
        "last_tick_age_s":  round(last_tick_age, 1) if last_tick_age else None,
        "mongo_ok":         mongo_ok,
        "active_watches":   watch_count,
        "ts":               datetime.now(timezone.utc).isoformat(),
    }


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(_build_status()).encode()
        code = 200 if json.loads(body)["ok"] else 503
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # silence default access log
        return


_server_thread: Optional[threading.Thread] = None


class _ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


def _serve():
    try:
        httpd = _ReusableHTTPServer((HEALTHZ_BIND, HEALTHZ_PORT), _HealthHandler)
    except OSError as exc:
        logger.warning("Health server failed to bind %s:%d — %s (port likely in use)",
                       HEALTHZ_BIND, HEALTHZ_PORT, exc)
        return
    logger.info("Health server listening on http://%s:%d/healthz",
                HEALTHZ_BIND, HEALTHZ_PORT)
    httpd.serve_forever()


def init_observability() -> None:
    """Start Sentry + /healthz. Idempotent."""
    global _server_thread

    if SENTRY_DSN:
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.0,
                            environment=os.getenv("ENV", "dev"))
            logger.info("Sentry initialized")
        except ImportError:
            logger.warning("SENTRY_DSN set but `sentry-sdk` not installed — skip")
        except Exception as exc:
            logger.warning("Sentry init failed: %s", exc)

    if HEALTHZ_PORT > 0 and _server_thread is None:
        _server_thread = threading.Thread(target=_serve, daemon=True,
                                          name="healthz")
        _server_thread.start()
