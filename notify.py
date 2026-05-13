"""
Multi-channel notifier for Restaurant Booking Tracker.

Channels
────────
  telegram  – existing Telegram Bot API (HTML parse mode)
  whatsapp  – Twilio WhatsApp (plain text, WhatsApp markdown)

Usage
─────
    from notify import send_alert, send_burst_warning, send_text

    send_alert(watch, slots)            # availability alert
    send_burst_warning(watch, mins)     # release window approaching
    send_text("any message", chat_id)   # raw message

Configuration (.env)
────────────────────
  # Telegram (existing)
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...

  # WhatsApp via Twilio (add to enable)
  TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_AUTH_TOKEN=your_auth_token
  TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # Twilio sandbox number
  TWILIO_WHATSAPP_TO=whatsapp:+1XXXXXXXXXX     # your WhatsApp number

Sandbox setup (2 min, no approval needed for testing)
──────────────────────────────────────────────────────
  1. Sign up at twilio.com (free)
  2. Go to Messaging → Try it out → Send a WhatsApp message
  3. Send the join code from your phone to activate sandbox
  4. Copy TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and the "From" sandbox number
  5. Set TWILIO_WHATSAPP_TO to your WhatsApp number with country code
"""

from __future__ import annotations

import html as _html
import logging
import os
import time as _time
from datetime import datetime, timezone
from typing import Optional

import requests as _requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

logger = logging.getLogger(__name__)

# ── WhatsApp / Twilio config ──────────────────────────────────────────

TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM       = os.getenv("TWILIO_WHATSAPP_FROM", "")  # e.g. whatsapp:+14155238886
TWILIO_WA_TO         = os.getenv("TWILIO_WHATSAPP_TO", "")    # e.g. whatsapp:+12125551234

TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
TWILIO_SEND_URL   = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

# ── Shared HTTP session ───────────────────────────────────────────────

_session = _requests.Session()


# ══════════════════════════════════════════════════════════════════════
# Escaping helpers
# ══════════════════════════════════════════════════════════════════════

def _tg_esc(text) -> str:
    """Escape text for Telegram HTML parse mode body (not href attributes)."""
    return _html.escape(str(text), quote=False)


def _tg_esc_attr(text) -> str:
    """Escape for use inside HTML attribute values (e.g. href)."""
    return _html.escape(str(text), quote=True)


def _wa_bold(text: str) -> str:
    """WhatsApp bold: *text*"""
    return f"*{text}*"


def _wa_esc(text) -> str:
    """Strip HTML-unsafe chars for WhatsApp plain text."""
    return str(text).replace("<", "").replace(">", "").replace("&", "and")


# ══════════════════════════════════════════════════════════════════════
# Message builders — Telegram (HTML)
# ══════════════════════════════════════════════════════════════════════

def _tg_alert(watch: dict, slots: list) -> str:
    name     = _tg_esc(watch.get("restaurant_name", "?"))
    url      = _tg_esc(watch.get("restaurant_url", ""))
    date_str = _tg_esc(watch.get("target_date", "?"))
    party    = watch.get("party_size", 2)

    mode = watch.get("date_mode", "single")
    if mode == "any":
        date_line = "📅 Any available date"
    elif mode == "range":
        date_line = f"📅 {_tg_esc(watch.get('date_from', ''))} → {_tg_esc(watch.get('date_to', ''))}"
    else:
        date_line = f"📅 {date_str}"

    slot_lines = "\n".join(
        f"  🕐 <b>{_tg_esc(s.get('time', '?'))}</b>"
        + (f"  <i>{_tg_esc(s.get('extra', '')[:40])}</i>" if s.get("extra") else "")
        for s in slots[:8]
    )
    more = f"\n  <i>+{len(slots) - 8} more slots</i>" if len(slots) > 8 else ""

    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")

    url_attr = _tg_esc_attr(watch.get("restaurant_url", ""))
    msg = (
        f"🍽 <b>{name}</b> — {len(slots)} slot{'s' if len(slots) != 1 else ''} open!\n\n"
        f"{date_line}  👥 {party}\n\n"
        f"{slot_lines}{more}\n\n"
        f"<i>⚡ Detected at {ts}</i>"
    )
    if url_attr:
        msg += f"\n\n<a href=\"{url_attr}\">Book now →</a>"
    return msg


_MAX_DATES_SHOWN = 5  # max individual dates before collapsing to summary


def _tg_range_alert(watch: dict, dates_with_slots: dict) -> str:
    name  = _tg_esc(watch.get("restaurant_name", "?"))
    party = watch.get("party_size", 2)
    ts    = datetime.now(timezone.utc).strftime("%H:%M UTC")
    url_attr = _tg_esc_attr(watch.get("restaurant_url", ""))

    sorted_dates = sorted(dates_with_slots)
    total_dates  = len(sorted_dates)

    # Detect repeating pattern: all dates share same slot times
    def _slot_key(slots):
        return tuple(sorted(s.get("time", "") for s in slots))

    all_keys = [_slot_key(dates_with_slots[d]) for d in sorted_dates]
    is_repeating = len(set(all_keys)) == 1 and total_dates > _MAX_DATES_SHOWN

    lines = [f"🍽 <b>{name}</b> — {total_dates} date{'s' if total_dates != 1 else ''} available!  👥 {party}\n"]

    if is_repeating:
        # Collapsed summary: same slot every day across range
        sample_slots = dates_with_slots[sorted_dates[0]]
        slot_times = "  ".join(
            f"🕐 <b>{_tg_esc(s.get('time', '?'))}</b>"
            + (f" <i>{_tg_esc(s.get('extra','')[:25])}</i>" if s.get("extra") else "")
            for s in sample_slots[:3]
        )
        lines.append(f"{slot_times}")
        lines.append(f"📅 {_tg_esc(sorted_dates[0])} → {_tg_esc(sorted_dates[-1])}")
        lines.append(f"<i>Same availability across all {total_dates} dates</i>")
    else:
        # Show up to _MAX_DATES_SHOWN individual dates
        for date_str in sorted_dates[:_MAX_DATES_SHOWN]:
            slots = dates_with_slots[date_str]
            slot_times = "  ".join(
                f"🕐 <b>{_tg_esc(s.get('time', '?'))}</b>" for s in slots[:3]
            )
            more = f" +{len(slots)-3}more" if len(slots) > 3 else ""
            lines.append(f"📅 <b>{_tg_esc(date_str)}</b>  {slot_times}{more}")
        if total_dates > _MAX_DATES_SHOWN:
            lines.append(f"<i>…and {total_dates - _MAX_DATES_SHOWN} more dates</i>")

    lines.append(f"\n<i>⚡ Detected at {ts}</i>")
    if url_attr:
        lines.append(f'<a href="{url_attr}">Book now →</a>')
    return "\n".join(lines)


def _tg_burst_warning(watch: dict, minutes_away: float) -> str:
    name   = _tg_esc(watch.get("restaurant_name", "?"))
    target = _tg_esc(watch.get("target_date", "?"))
    from config import BURST_CHECK_INTERVAL_SECONDS
    return (
        f"⏰ <b>Slots dropping in ~{minutes_away:.0f} min</b>\n\n"
        f"🏪 <b>{name}</b>\n"
        f"📅 {target}\n\n"
        f"🔥 Switched to burst mode — checking every {BURST_CHECK_INTERVAL_SECONDS}s"
    )


# ══════════════════════════════════════════════════════════════════════
# Message builders — WhatsApp (plain text)
# ══════════════════════════════════════════════════════════════════════

def _wa_alert(watch: dict, slots: list) -> str:
    name  = _wa_esc(watch.get("restaurant_name", "?"))
    url   = watch.get("restaurant_url", "")
    party = watch.get("party_size", 2)
    ts    = datetime.now(timezone.utc).strftime("%H:%M UTC")

    mode = watch.get("date_mode", "single")
    if mode == "any":
        date_line = "Any available date"
    elif mode == "range":
        date_line = f"{watch.get('date_from', '')} to {watch.get('date_to', '')}"
    else:
        date_line = watch.get("target_date", "?")

    slot_lines = "\n".join(
        f"  {s.get('time', '?')}" + (f"  {_wa_esc(s.get('extra', '')[:40])}" if s.get("extra") else "")
        for s in slots[:8]
    )
    more = f"\n  +{len(slots) - 8} more slots" if len(slots) > 8 else ""

    msg = (
        f"🍽 *{name}* — {len(slots)} slot{'s' if len(slots) != 1 else ''} open!\n\n"
        f"📅 {date_line}  👥 {party}\n\n"
        f"{slot_lines}{more}\n\n"
        f"⚡ Detected at {ts}"
    )
    if url:
        msg += f"\n\nBook now → {url}"
    return msg


def _wa_burst_warning(watch: dict, minutes_away: float) -> str:
    name   = _wa_esc(watch.get("restaurant_name", "?"))
    target = watch.get("target_date", "?")
    from config import BURST_CHECK_INTERVAL_SECONDS
    return (
        f"⏰ *Slots dropping in ~{minutes_away:.0f} min*\n\n"
        f"🏪 *{name}*\n"
        f"📅 {target}\n\n"
        f"🔥 Burst mode ON — checking every {BURST_CHECK_INTERVAL_SECONDS}s"
    )


# ══════════════════════════════════════════════════════════════════════
# Channel senders
# ══════════════════════════════════════════════════════════════════════

def _send_telegram(text: str, chat_id: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        return False
    try:
        from metrics import log as _mlog
        t0 = _time.perf_counter()
        resp = _session.post(
            TELEGRAM_SEND_URL.format(token=TELEGRAM_BOT_TOKEN),
            json={"chat_id": target, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
        _mlog("alert_sent", channel="telegram", chat_id=str(target),
              duration_ms=round((_time.perf_counter() - t0) * 1000, 2))
        return True
    except Exception as exc:
        logger.error("Telegram send failed → %s: %s", chat_id, exc)
        try:
            from metrics import log as _mlog
            _mlog("alert_failed", channel="telegram", chat_id=str(target), error=repr(exc))
        except Exception:
            pass
        return False


def _send_whatsapp(text: str, to: Optional[str] = None) -> bool:
    """
    Send via Twilio WhatsApp. ``to`` defaults to TWILIO_WHATSAPP_TO.
    Requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM set.
    """
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WA_FROM]):
        logger.debug("WhatsApp not configured — skipping.")
        return False

    recipient = to or TWILIO_WA_TO
    if not recipient:
        logger.debug("No WhatsApp recipient configured.")
        return False

    try:
        from metrics import log as _mlog
        t0 = _time.perf_counter()
        resp = _session.post(
            TWILIO_SEND_URL.format(sid=TWILIO_ACCOUNT_SID),
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"From": TWILIO_WA_FROM, "To": recipient, "Body": text},
            timeout=10,
        )
        resp.raise_for_status()
        _mlog("alert_sent", channel="whatsapp", to=str(recipient),
              duration_ms=round((_time.perf_counter() - t0) * 1000, 2))
        logger.info("WhatsApp sent → %s", recipient)
        return True
    except Exception as exc:
        logger.error("WhatsApp send failed → %s: %s", recipient, exc)
        try:
            from metrics import log as _mlog
            _mlog("alert_failed", channel="whatsapp", to=str(recipient), error=repr(exc))
        except Exception:
            pass
        return False


# ══════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════

def send_alert(watch: dict, slots: list,
               tg_chat_id: str = "", wa_to: Optional[str] = None) -> bool:
    """Send slot-available alert on all configured channels."""
    tg_ok = _send_telegram(_tg_alert(watch, slots), tg_chat_id or watch.get("chat_id", ""))
    wa_ok = _send_whatsapp(_wa_alert(watch, slots), wa_to)
    return tg_ok or wa_ok


def _wa_range_alert(watch: dict, dates_with_slots: dict) -> str:
    """Compact WhatsApp version of range alert."""
    name  = _wa_esc(watch.get("restaurant_name", "?"))
    party = watch.get("party_size", 2)
    url   = watch.get("restaurant_url", "")
    ts    = datetime.now(timezone.utc).strftime("%H:%M UTC")

    sorted_dates = sorted(dates_with_slots)
    total_dates  = len(sorted_dates)

    def _slot_key(slots):
        return tuple(sorted(s.get("time", "") for s in slots))

    all_keys = [_slot_key(dates_with_slots[d]) for d in sorted_dates]
    is_repeating = len(set(all_keys)) == 1 and total_dates > _MAX_DATES_SHOWN

    lines = [f"🍽 *{name}* — {total_dates} date{'s' if total_dates != 1 else ''} open!  👥 {party}\n"]

    if is_repeating:
        sample_slots = dates_with_slots[sorted_dates[0]]
        slot_times = "  ".join(s.get("time", "?") for s in sample_slots[:3])
        lines.append(f"{slot_times}")
        lines.append(f"📅 {sorted_dates[0]} → {sorted_dates[-1]}")
        lines.append(f"Same slot across all {total_dates} dates")
    else:
        for date_str in sorted_dates[:_MAX_DATES_SHOWN]:
            slots = dates_with_slots[date_str]
            slot_times = "  ".join(s.get("time", "?") for s in slots[:3])
            lines.append(f"📅 {date_str}  {slot_times}")
        if total_dates > _MAX_DATES_SHOWN:
            lines.append(f"...and {total_dates - _MAX_DATES_SHOWN} more dates")

    lines.append(f"\n⚡ Detected at {ts}")
    if url:
        lines.append(f"Book now → {url}")
    return "\n".join(lines)


def send_range_alert(watch: dict, dates_with_slots: dict,
                     tg_chat_id: str = "", wa_to: Optional[str] = None) -> bool:
    """Send multi-date alert on all configured channels."""
    tg_ok = _send_telegram(_tg_range_alert(watch, dates_with_slots),
                           tg_chat_id or watch.get("chat_id", ""))
    wa_ok = _send_whatsapp(_wa_range_alert(watch, dates_with_slots), wa_to)
    return tg_ok or wa_ok


def send_burst_warning(watch: dict, minutes_away: float,
                       tg_chat_id: str = "", wa_to: Optional[str] = None) -> bool:
    """Send burst-mode entry warning on all configured channels."""
    tg_ok = _send_telegram(_tg_burst_warning(watch, minutes_away),
                           tg_chat_id or watch.get("chat_id", ""))
    wa_ok = _send_whatsapp(_wa_burst_warning(watch, minutes_away), wa_to)
    return tg_ok or wa_ok


def send_text(text: str, tg_chat_id: str = "", wa_to: Optional[str] = None) -> bool:
    """Send raw text on all configured channels (Telegram gets HTML-escaped)."""
    tg_ok = _send_telegram(_tg_esc(text), tg_chat_id)
    wa_ok = _send_whatsapp(_wa_esc(text), wa_to)
    return tg_ok or wa_ok


def whatsapp_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WA_FROM)


def telegram_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN)
