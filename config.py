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
# Resy's public web API key (visible in browser devtools; not a secret)
RESY_API_KEY = os.getenv("RESY_API_KEY", "VbWk7s3L4KiK5fzlO7JD3Q5EYolEVsC")
