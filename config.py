"""
Configuration for Restaurant Booking Tracker.
Copy .env.example to .env and fill in your values.
"""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

# ── MongoDB ───────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "restaurant_tracker")

# ── Telegram ───────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

if not TELEGRAM_BOT_TOKEN:
    logger.warning(
        "TELEGRAM_BOT_TOKEN is not set — alerts and bot commands will not work. "
        "Set it in .env to enable Telegram notifications."
    )

# ── Scraping behaviour ────────────────────────────────────────────────
REQUEST_DELAY_SECONDS = int(os.getenv("REQUEST_DELAY_SECONDS", "5"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
USER_AGENT = (
    "RestaurantBookingTracker/1.0 "
    "(+https://github.com/yourusername/restaurant-booking-tracker; "
    "educational project; respects robots.txt)"
)
MAX_RETRIES = 2

# ── Scheduler ─────────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "15"))

# ── Alerts ────────────────────────────────────────────────────────────
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "60"))

# Quiet hours: don't send alerts between these hours (24h local time).
# Set both to same value to disable. Example: 23 → 7 silences 11pm-7am.
QUIET_HOURS_START = int(os.getenv("QUIET_HOURS_START", "0"))
QUIET_HOURS_END   = int(os.getenv("QUIET_HOURS_END",   "0"))
QUIET_HOURS_TZ    = os.getenv("QUIET_HOURS_TZ", "America/New_York")

# ── Auto-Booking ──────────────────────────────────────────────────────
AUTO_BOOK_ENABLED = os.getenv("AUTO_BOOK_ENABLED", "false").lower() == "true"

# Reservation contact details (used when filling booking forms)
BOOKING_NAME  = os.getenv("BOOKING_NAME", "")
BOOKING_EMAIL = os.getenv("BOOKING_EMAIL", "")
BOOKING_PHONE = os.getenv("BOOKING_PHONE", "")

# Platform credentials
RESY_EMAIL        = os.getenv("RESY_EMAIL", "")
RESY_PASSWORD     = os.getenv("RESY_PASSWORD", "")
OPENTABLE_EMAIL   = os.getenv("OPENTABLE_EMAIL", "")
OPENTABLE_PASSWORD = os.getenv("OPENTABLE_PASSWORD", "")

# ── Burst mode & release learning ────────────────────────────────────
BURST_CHECK_INTERVAL_SECONDS = int(os.getenv("BURST_CHECK_INTERVAL_SECONDS", "10"))
BURST_WINDOW_MINUTES         = int(os.getenv("BURST_WINDOW_MINUTES", "15"))
RELEASE_LEARN_ENABLED        = os.getenv("RELEASE_LEARN_ENABLED", "true").lower() == "true"

# ── Platform API keys ─────────────────────────────────────────────────
# Resy's web API key. Lifted from devtools on resy.com — public/non-secret per se
# but bots that share one key get fingerprinted, so set your own in .env to be safe.
RESY_API_KEY = os.getenv("RESY_API_KEY", "")
if not RESY_API_KEY:
    logger.warning("RESY_API_KEY not set in .env — Resy API path disabled. "
                   "Grab the value from resy.com devtools (Network tab, Authorization: ResyAPI api_key=...).")

# Manual Resy auth_token — extract from logged-in browser cookies (resy_token).
# Use this to skip the auto-login flow (which gets 419-rate-limited heavily).
RESY_AUTH_TOKEN = os.getenv("RESY_AUTH_TOKEN", "")

# Optional outbound proxy — useful when an IP gets banned by Resy.
# Example: HTTPS_PROXY=http://user:pass@host:port
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "") or os.getenv("HTTP_PROXY", "")
