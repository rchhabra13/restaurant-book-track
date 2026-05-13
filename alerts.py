"""
Telegram alert module for Restaurant Booking Tracker.

Setup
─────
1. Message @BotFather on Telegram → /newbot → save the token.
2. Message your bot, then visit:
   https://api.telegram.org/bot<TOKEN>/getUpdates
   to find your chat_id.
3. Put both in your .env file.
"""

import html
import logging
from typing import List

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured — skipping alert.")
        return False

    url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram alert sent successfully.")
        return True
    except requests.RequestException as exc:
        logger.error("Failed to send Telegram alert: %s", exc)
        return False


def send_availability_alert(
    restaurant_name: str,
    target_date: str,
    party_size: int,
    slots: List[dict],
    url: str = "",
) -> bool:
    """Format and send an availability alert."""
    _e = lambda v: html.escape(str(v), quote=True)
    slot_lines = "\n".join(
        f"  • {_e(s.get('time', '?'))}" + (f" ({_e(s.get('extra', ''))})" if s.get("extra") else "")
        for s in slots[:10]
    )
    more = f"\n  … and {len(slots) - 10} more" if len(slots) > 10 else ""

    text = (
        f"🍽 <b>Reservation Alert</b>\n\n"
        f"<b>{_e(restaurant_name)}</b>\n"
        f"📅 {_e(target_date)}  👥 Party of {_e(party_size)}\n\n"
        f"<b>Available slots:</b>\n{slot_lines}{more}\n\n"
    )
    if url:
        text += f'<a href="{_e(url)}">Book now →</a>'

    return _send_telegram(text)


def send_test_message() -> bool:
    """Send a quick test ping so the user can verify setup."""
    return _send_telegram("✅ Restaurant Booking Tracker is connected!")


def send_custom_message(text: str) -> bool:
    """Send an arbitrary message (used for reminders, errors, etc.)."""
    return _send_telegram(text)
