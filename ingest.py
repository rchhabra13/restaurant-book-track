"""
HTTP ingest endpoint for the TableWatch Bridge browser extension.

Listens on INGEST_PORT (default 8091) and accepts POSTs from the extension:

    POST /ingest
    Content-Type: application/json
    {
      "source": "extension",
      "ts":     1715829600000,
      "results": [
        {
          "venue_id": 5340, "slug": "carbone",
          "date": "2026-06-15", "party_size": 2,
          "slots": [{"time":"7:00 PM","extra":"Dining Room",...}],
          "ok": true
        },
        ...
      ]
    }

For each result we look up the matching active watch(es) in MongoDB, run
the existing diff/dedup logic, and trigger ``notify.send_alert`` so the
extension piggybacks the entire existing pipeline (Telegram + WhatsApp +
metrics + cooldown + signature dedup).

Wire-up: ``init_ingest_server()`` is called from ``scheduler.start_scheduler()``
so it boots alongside the scheduler.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

logger = logging.getLogger(__name__)

INGEST_PORT = int(os.getenv("INGEST_PORT", "8091"))
INGEST_BIND = os.getenv("INGEST_BIND", "127.0.0.1")
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")   # optional shared secret

_server_thread: Optional[threading.Thread] = None


# ──────────────────────────────────────────────────────────────────────
# Per-result handler — reuses the existing alert pipeline
# ──────────────────────────────────────────────────────────────────────

def _handle_result(item: dict) -> dict:
    """
    Match an ingested result against active MongoDB watches and trigger
    alerts if new slots are present. Returns a small summary dict that the
    HTTP layer echoes back so the extension can log success/failure.
    """
    from database import (
        get_watches, save_availability, was_recently_alerted,
        log_alert_with_signature, get_previous_slot_signature,
        _slot_signature,
    )
    from notify import send_alert

    venue_id   = item.get("venue_id")
    date       = item.get("date")
    party_size = item.get("party_size")
    slots      = item.get("slots", []) or []
    if venue_id is None or not date or party_size is None:
        return {"matched": 0, "alerted": 0, "reason": "missing fields"}

    # Find the active watch(es) for this venue/date/party
    matched: list[dict] = []
    for w in get_watches(active_only=True):
        if w.get("restaurant_venue_id") != venue_id:
            continue
        if w.get("party_size") != party_size:
            continue
        if w.get("date_mode", "single") == "single":
            if w.get("target_date") == date:
                matched.append(w)
        else:
            df = w.get("date_from", "")
            dt = w.get("date_to",   "")
            if df <= date <= dt:
                matched.append(w)

    alerted = 0
    for w in matched:
        save_availability(w["id"], slots, "")
        if not slots:
            continue
        new_sig  = _slot_signature(slots)
        prev_sig = get_previous_slot_signature(w["id"])
        if new_sig == prev_sig:
            continue
        if was_recently_alerted(w["id"]):
            continue
        if send_alert(w, slots, tg_chat_id=w.get("chat_id", "")):
            log_alert_with_signature(w["id"],
                                     f"Extension found {len(slots)} slots on {date}",
                                     slots)
            alerted += 1

    return {"matched": len(matched), "alerted": alerted, "slots": len(slots)}


# ──────────────────────────────────────────────────────────────────────
# HTTP server
# ──────────────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        # CORS so the browser extension can POST cross-origin
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        self._send_json(200, {"ok": True})

    def do_POST(self):  # noqa: N802
        if self.path != "/ingest":
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        if INGEST_TOKEN and self.headers.get("X-Token", "") != INGEST_TOKEN:
            self._send_json(401, {"ok": False, "error": "bad token"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body   = self.rfile.read(length) if length else b""
            payload = json.loads(body or b"{}")
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": f"bad json: {exc}"})
            return

        results = payload.get("results", [])
        if not isinstance(results, list):
            self._send_json(400, {"ok": False, "error": "results must be a list"})
            return

        summary = []
        for item in results:
            try:
                summary.append(_handle_result(item))
            except Exception as exc:
                logger.exception("ingest: per-item handler failed")
                summary.append({"error": str(exc)})

        from metrics import log as _mlog
        _mlog("ingest", source=payload.get("source", "?"),
              items=len(results),
              alerted=sum(s.get("alerted", 0) for s in summary if isinstance(s, dict)))

        self._send_json(200, {"ok": True, "summary": summary,
                              "ts": datetime.now(timezone.utc).isoformat()})

    def do_GET(self):  # noqa: N802
        # Tiny status page for sanity check from a browser
        if self.path == "/ingest/status":
            self._send_json(200, {"ok": True, "endpoint": "/ingest",
                                  "auth_required": bool(INGEST_TOKEN)})
            return
        self._send_json(404, {"ok": False})

    def log_message(self, fmt, *args):  # quiet default access log
        return


class _ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _serve():
    try:
        httpd = _ReusableHTTPServer((INGEST_BIND, INGEST_PORT), _Handler)
    except OSError as exc:
        logger.warning("Ingest server failed to bind %s:%d — %s",
                       INGEST_BIND, INGEST_PORT, exc)
        return
    logger.info("Ingest server listening on http://%s:%d/ingest",
                INGEST_BIND, INGEST_PORT)
    httpd.serve_forever()


def init_ingest_server() -> None:
    """Start the ingest HTTP server in a background thread. Idempotent."""
    global _server_thread
    if _server_thread is not None and _server_thread.is_alive():
        return
    _server_thread = threading.Thread(target=_serve, daemon=True, name="ingest")
    _server_thread.start()
