"""
Direct API clients for restaurant booking platforms.

Instead of rendering pages with Playwright (slow, fragile),
these clients speak directly to the platform's internal JSON APIs —
the same endpoints the browser uses. This gives:

  - Sub-second response times
  - Structured slot data including booking tokens
  - Reliable slot detection even as HTML layout changes
  - API-based booking (no clicking required)

Supported
─────────
  ResyClient      — resy.com  (login → venue lookup → slots → book)
  OpenTableClient — opentable.com (session-based availability)

Usage
─────
  from api_client import get_client

  client = get_client("resy", email="you@example.com", password="secret")
  venue_id = client.get_venue_id("https://resy.com/cities/ny/semma")
  slots    = client.get_slots(venue_id, "2026-05-10", party_size=2)
  result   = client.book_slot(slots[0], party_size=2, date="2026-05-10")
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, urlencode

import requests

logger = logging.getLogger(__name__)


# ── Slot container (API version carries a booking token) ──────────────

@dataclass
class ApiSlot:
    time_str: str
    party_size: int
    extra: str = ""
    # Resy: needed to call /3/book
    config_id: Optional[int] = None
    config_token: Optional[str] = None
    # OpenTable: needed for their checkout
    slot_hash: Optional[str] = None
    platform: str = "generic"

    def to_dict(self) -> dict:
        return {
            "time": self.time_str,
            "party_size": self.party_size,
            "extra": self.extra,
            "config_id": self.config_id,
            "config_token": self.config_token,
            "slot_hash": self.slot_hash,
            "platform": self.platform,
        }

    def __repr__(self):
        return f"ApiSlot({self.time_str}, party={self.party_size})"


# ══════════════════════════════════════════════════════════════════════
# Resy Client
# ══════════════════════════════════════════════════════════════════════

from config import RESY_API_KEY

RESY_BASE = "https://api.resy.com"


class ResyClient:
    """
    Client for the Resy internal API.

    The API key is Resy's own public web key (visible in browser devtools).
    The user token is obtained by logging in with email + password.
    """

    def __init__(self, email: str = "", password: str = "",
                 auth_token: str = ""):
        self.email    = email
        self.password = password
        # Prefer a manually-supplied token (extracted from logged-in browser).
        # Bypasses the auto-login flow that is heavily 419-rate-limited by Resy.
        self.token: Optional[str] = auth_token or None

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization":        f'ResyAPI api_key="{RESY_API_KEY}"',
            "X-Origin":             "https://resy.com",
            "Referer":              "https://resy.com/",
            "Origin":               "https://resy.com",
            "Content-Type":         "application/json",
            "Accept":               "application/json, text/plain, */*",
            "User-Agent":           (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        })

        # Apply outbound proxy if configured (per-request override; respects HTTPS_PROXY env)
        from config import HTTPS_PROXY
        if HTTPS_PROXY:
            self.session.proxies = {"http": HTTPS_PROXY, "https": HTTPS_PROXY}
            logger.info("Resy client using proxy %s", HTTPS_PROXY)

        if self.token:
            self.session.headers["X-Resy-Auth-Token"] = self.token
            logger.info("Resy: using manual auth token (%d chars) — skipping login", len(self.token))
        elif email and password:
            self._login()

    # ── Auth ──────────────────────────────────────────────────────────

    def _login(self) -> bool:
        """Log in and store auth token for booking calls."""
        try:
            resp = self.session.post(
                f"{RESY_BASE}/3/auth/password",
                json={"email": self.email, "password": self.password},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self.token = data.get("token") or data.get("auth_token")
            if self.token:
                self.session.headers["X-Resy-Auth-Token"] = self.token
                logger.info("Resy: logged in as %s", self.email)
                return True
            logger.warning("Resy login: no token in response")
        except Exception as exc:
            logger.error("Resy login failed: %s", exc)
        return False

    # ── Venue lookup ──────────────────────────────────────────────────

    def get_venue_id(self, url: str) -> Optional[int]:
        """
        Resolve a Resy restaurant URL to its numeric venue_id.

        URL formats handled:
          https://resy.com/cities/ny/semma
          https://resy.com/cities/ny/semma?seats=2&date=2026-05-10
        """
        city, slug = parse_resy_url(url)
        if not city or not slug:
            logger.warning("Resy: could not parse URL %s", url)
            return None

        try:
            resp = self.session.get(
                f"{RESY_BASE}/3/venue/find",
                params={"url_slug": slug, "location": city},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            # Response shape: {"id": {"resy": 12345}, ...} or {"venue": {"id": ...}}
            venue_id = (
                data.get("id", {}).get("resy")
                or data.get("venue", {}).get("id")
                or data.get("id")
            )
            if venue_id:
                logger.info("Resy: resolved %s → venue_id=%s", slug, venue_id)
                return int(venue_id)
        except Exception as exc:
            logger.error("Resy venue lookup failed for %s: %s", url, exc)
        return None

    # ── Availability ──────────────────────────────────────────────────

    def _refresh_token_if_needed(self, response) -> bool:
        """Re-login on 401 and return True if token was refreshed."""
        if response.status_code == 401:
            logger.warning("Resy: auth token expired, re-logging in…")
            self.token = None
            return self._login()
        return False

    def get_slots(
        self,
        venue_id: int,
        date: str,          # YYYY-MM-DD
        party_size: int,
    ) -> List[ApiSlot]:
        """
        Fetch available slots from the Resy /4/find endpoint.
        Returns a list of ApiSlot objects ready for display or booking.
        """
        params = {
            "lat":        0,
            "long":       0,
            "day":        date,
            "party_size": party_size,
            "venue_id":   venue_id,
        }
        data = None
        for attempt in range(3):
            try:
                resp = self.session.get(f"{RESY_BASE}/4/find", params=params, timeout=10)
                if resp.status_code in (429, 503):
                    wait = 2 ** attempt
                    logger.warning("Resy rate-limited (attempt %d) — waiting %ds", attempt + 1, wait)
                    time.sleep(wait)
                    continue
                if self._refresh_token_if_needed(resp):
                    resp = self.session.get(f"{RESY_BASE}/4/find", params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:
                logger.error("Resy get_slots attempt %d failed: %s", attempt + 1, exc)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if data is None:
            return []

        slots: List[ApiSlot] = []
        venues = data.get("results", {}).get("venues", [])
        for v in venues:
            for raw in v.get("slots", []):
                start      = raw.get("date", {}).get("start", "")
                time_str   = _parse_resy_time(start)
                config     = raw.get("config", {})
                config_id  = config.get("id")
                config_tok = config.get("token")
                extra      = config.get("type", "")   # e.g. "Dining Room"

                if time_str:
                    slots.append(ApiSlot(
                        time_str    = time_str,
                        party_size  = party_size,
                        extra       = extra,
                        config_id   = config_id,
                        config_token= config_tok,
                        platform    = "resy",
                    ))

        logger.info("Resy: found %d slot(s) for venue %s on %s", len(slots), venue_id, date)
        return slots

    # ── Booking ───────────────────────────────────────────────────────

    def book_slot(self, slot: ApiSlot, party_size: int, date: str) -> dict:
        """
        Book a slot using its config_token (no Playwright needed).

        Requires the client to be logged in (token set).
        Returns {"success": bool, "confirmation": str, "message": str}.
        """
        if not self.token:
            if not self._login():
                return {"success": False, "message": "Not authenticated — check RESY_EMAIL/PASSWORD"}

        if not slot.config_token:
            return {"success": False, "message": "Slot has no booking token — cannot book via API"}

        try:
            # Step 1: get a book_token from the config token
            details_resp = self.session.get(
                f"{RESY_BASE}/3/details",
                params={
                    "config_id":  slot.config_id,
                    "day":        date,
                    "party_size": party_size,
                },
                timeout=10,
            )
            details_resp.raise_for_status()
            book_token = details_resp.json().get("book_token", {}).get("value")

            if not book_token:
                # Fall back: use config token directly
                book_token = slot.config_token

            # Step 2: confirm booking
            book_resp = self.session.post(
                f"{RESY_BASE}/3/book",
                json={
                    "book_token":             book_token,
                    "struct_payment_method":  '{"id":0}',
                    "source_id":              "resy.com-venue-details",
                },
                timeout=15,
            )
            book_resp.raise_for_status()
            result = book_resp.json()

            resy_id = (
                result.get("resy_token")
                or result.get("reservation_id")
                or result.get("id", "")
            )
            return {
                "success":      True,
                "confirmation": str(resy_id),
                "message":      f"Booked via Resy API — ref {resy_id}",
            }

        except requests.HTTPError as exc:
            msg = ""
            try:
                msg = exc.response.json().get("message", "")
            except Exception:
                pass
            logger.error("Resy booking HTTP error: %s %s", exc, msg)
            return {"success": False, "message": f"Resy API error: {msg or exc}"}
        except Exception as exc:
            logger.exception("Resy booking exception: %s", exc)
            return {"success": False, "message": str(exc)}


# ══════════════════════════════════════════════════════════════════════
# OpenTable Client
# ══════════════════════════════════════════════════════════════════════

OT_BASE = "https://www.opentable.com"


class OpenTableClient:
    """
    Client for OpenTable's availability API.

    OpenTable exposes a semi-public availability endpoint used by
    their embedded widget. We call it directly with a restaurant ID
    (rid) extracted from the URL or page.
    """

    def __init__(self, email: str = "", password: str = ""):
        self.email    = email
        self.password = password

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://www.opentable.com/",
        })

    # ── Venue lookup ──────────────────────────────────────────────────

    def get_restaurant_id(self, url: str) -> Optional[int]:
        """
        Extract the OpenTable restaurant ID (rid) from a URL or page.

        URL format: https://www.opentable.com/r/restaurant-name
        The rid is embedded in the page HTML.
        """
        # Try extracting from URL directly (/restaurant/12345 format)
        m = re.search(r"/(?:r|restaurant)/(\d+)", url)
        if m:
            return int(m.group(1))

        # Fetch the page and look for "rid" in the HTML
        try:
            resp = self.session.get(url, timeout=15)
            m = re.search(r'"rid"\s*:\s*(\d+)', resp.text)
            if m:
                return int(m.group(1))
            m = re.search(r'restaurant_id["\s:]+(\d+)', resp.text)
            if m:
                return int(m.group(1))
        except Exception as exc:
            logger.error("OpenTable rid extraction failed: %s", exc)
        return None

    # ── Availability ──────────────────────────────────────────────────

    def get_slots(
        self,
        restaurant_id: int,
        date: str,
        party_size: int,
    ) -> List[ApiSlot]:
        """
        Fetch slots from OpenTable's availability widget API.
        """
        try:
            resp = self.session.get(
                f"{OT_BASE}/dapi/fe/gql",
                params={
                    "query": "Availability",
                    "variables": json.dumps({
                        "restaurantId": restaurant_id,
                        "date": date,
                        "partySize": party_size,
                        "isRequiredConsumerPage": False,
                    }),
                },
                timeout=10,
            )
            resp.raise_for_status()
        except Exception:
            # Fall back to widget endpoint
            return self._get_slots_widget(restaurant_id, date, party_size)

        slots: List[ApiSlot] = []
        try:
            data = resp.json()
            times = (
                data.get("data", {})
                    .get("availability", {})
                    .get("times", [])
            )
            for t in times:
                time_str  = t.get("timeOffsetISO8601") or t.get("time") or ""
                slot_hash = t.get("slotHash") or t.get("hash") or ""
                if time_str:
                    slots.append(ApiSlot(
                        time_str   = _parse_ot_time(time_str),
                        party_size = party_size,
                        slot_hash  = slot_hash,
                        platform   = "opentable",
                    ))
        except Exception as exc:
            logger.warning("OpenTable response parse error: %s", exc)

        if not slots:
            return self._get_slots_widget(restaurant_id, date, party_size)
        return slots

    def _get_slots_widget(
        self,
        restaurant_id: int,
        date: str,
        party_size: int,
    ) -> List[ApiSlot]:
        """Fallback: OpenTable embedded widget endpoint."""
        try:
            resp = self.session.get(
                f"{OT_BASE}/widget/reservation/counts",
                params={
                    "rid":        restaurant_id,
                    "datetime":   f"{date}T19:00",
                    "party_size": party_size,
                    "lang":       "en-US",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("OpenTable widget fallback failed: %s", exc)
            return []

        slots: List[ApiSlot] = []
        for item in data.get("availability", []):
            time_str = item.get("dateTime") or item.get("time") or ""
            if time_str:
                slots.append(ApiSlot(
                    time_str   = _parse_ot_time(time_str),
                    party_size = party_size,
                    platform   = "opentable",
                ))
        return slots


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def parse_resy_url(url: str):
    """
    Extract (city, slug) from a Resy URL.

    Examples
    ────────
    https://resy.com/cities/ny/semma                    → ("ny", "semma")
    https://resy.com/cities/chicago/alinea              → ("chicago", "alinea")
    https://resy.com/cities/new-york-ny/venues/carbone  → ("new-york-ny", "carbone")
    """
    # Optional /venues/ segment between the city and the restaurant slug
    m = re.search(r"/cities/([^/]+)/(?:venues/)?([^/?#]+)", url)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def _parse_resy_time(iso_str: str) -> str:
    """
    Convert Resy ISO datetime "2026-05-10 19:30:00" → "7:30 PM".
    """
    if not iso_str:
        return ""
    try:
        from datetime import datetime
        dt = datetime.strptime(iso_str[:19], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%-I:%M %p")
    except Exception:
        # Return the raw string sliced to just the time part
        return iso_str[11:16] if len(iso_str) > 16 else iso_str


def _parse_ot_time(time_str: str) -> str:
    """
    Normalise an OpenTable time string to HH:MM AM/PM.
    Handles ISO 8601 offset and plain time strings.
    """
    if not time_str:
        return ""
    try:
        from datetime import datetime
        # Try ISO with offset: 2026-05-10T19:30:00-04:00
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%H:%M"):
            try:
                dt = datetime.strptime(time_str[:19], fmt[:len(fmt)])
                return dt.strftime("%-I:%M %p")
            except ValueError:
                continue
    except Exception:
        pass
    return time_str


# ══════════════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════════════

def get_client(platform: str, email: str = "", password: str = "",
               auth_token: str = ""):
    """
    Return the appropriate API client for *platform*.

    Parameters
    ----------
    platform   : "resy" | "opentable"
    email      : account email (required for booking; optional for slot checks)
    password   : account password
    auth_token : manually-supplied auth token (Resy only) — bypasses login flow.
    """
    if platform == "resy":
        return ResyClient(email=email, password=password, auth_token=auth_token)
    elif platform == "opentable":
        return OpenTableClient(email=email, password=password)
    else:
        return None
