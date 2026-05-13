"""
Ethical web-scraping framework for restaurant availability.

Design principles
─────────────────
1. Respects robots.txt before every new domain.
2. Polite User-Agent that identifies the project.
3. Minimum delay between requests (configurable, default 5 s).
4. Caches robots.txt per session to avoid repeat fetches.
5. Pluggable per-platform parsers via SCRAPERS registry.
"""

import hashlib
import logging
import re
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from config import (
    MAX_RETRIES,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

# ── Robots.txt cache ──────────────────────────────────────────────────

_robots_cache: dict[str, RobotFileParser] = {}
_robots_cache_time: dict[str, float] = {}
_ROBOTS_CACHE_TTL = 3600  # re-fetch robots.txt once per hour


def _get_robots(url: str) -> RobotFileParser:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    now = time.time()
    if origin not in _robots_cache or (now - _robots_cache_time.get(origin, 0)) > _ROBOTS_CACHE_TTL:
        rp = RobotFileParser()
        rp.set_url(f"{origin}/robots.txt")
        try:
            rp.read()
        except Exception:
            # Transient error — deny by default (fail-closed); will retry next hour
            logger.warning("Could not fetch robots.txt for %s — denying access by default", origin)
            rp.allow_all = False
        _robots_cache[origin] = rp
        _robots_cache_time[origin] = now
    return _robots_cache[origin]


def is_allowed(url: str) -> bool:
    """Return True if our User-Agent is allowed to fetch *url*."""
    rp = _get_robots(url)
    return rp.can_fetch(USER_AGENT, url)


# ── Rate limiter ──────────────────────────────────────────────────────

_last_request_time: dict[str, float] = {}


def _rate_limit(url: str):
    domain = urlparse(url).netloc
    last = _last_request_time.get(domain, 0)
    elapsed = time.time() - last
    if elapsed < REQUEST_DELAY_SECONDS:
        time.sleep(REQUEST_DELAY_SECONDS - elapsed)
    _last_request_time[domain] = time.time()


# ── Generic fetcher ───────────────────────────────────────────────────

def fetch_page(url: str, use_browser: bool = False) -> Optional[str]:
    """
    Fetch a page respecting robots.txt and rate limits.
    Returns HTML string or None on failure.
    use_browser: if True, use Playwright for JS-rendered pages (Resy, etc.)
    """
    if not is_allowed(url):
        logger.warning("Blocked by robots.txt: %s", url)
        return None

    _rate_limit(url)

    if use_browser:
        return _fetch_with_playwright(url)
    return _fetch_with_requests(url)


def _fetch_with_requests(url: str) -> Optional[str]:
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            logger.warning("Attempt %d failed for %s: %s", attempt, url, exc)
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_DELAY_SECONDS)
    return None


def _fetch_with_playwright(url: str) -> Optional[str]:
    """Fetch JS-rendered page using Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_extra_http_headers({"User-Agent": USER_AGENT})
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(3000)  # extra wait for Resy slots to render
                html = page.content()
                browser.close()
                return html
        except Exception as exc:
            logger.warning("Playwright attempt %d failed for %s: %s", attempt, url, exc)
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_DELAY_SECONDS)
    return None


def html_hash(html: str) -> str:
    return hashlib.md5(html.encode()).hexdigest()


# ── Slot data class ───────────────────────────────────────────────────

class Slot:
    """Represents a single available booking slot."""

    def __init__(self, time_str: str, party_size: int = 0, extra: str = ""):
        self.time_str = time_str
        self.party_size = party_size
        self.extra = extra

    def to_dict(self) -> dict:
        return {
            "time": self.time_str,
            "party_size": self.party_size,
            "extra": self.extra,
        }

    def __repr__(self):
        return f"Slot({self.time_str}, party={self.party_size})"


# ══════════════════════════════════════════════════════════════════════
# Per-platform scrapers
# ══════════════════════════════════════════════════════════════════════
#
# Each scraper is a function:
#   (html: str, target_date: str, party_size: int) -> List[Slot]
#
# Register new scrapers in SCRAPERS dict at the bottom.


def _parse_resy(html: str, target_date: str, party_size: int) -> List[Slot]:
    """
    Parse Resy-style pages.

    Resy is JS-rendered; use fetch_page(..., use_browser=True) to get HTML.
    Looks for slot patterns like "8:30 PM Dining Room", "10:00 PM", etc.
    """
    soup = BeautifulSoup(html, "html.parser")
    slots: List[Slot] = []
    seen = set()

    # Resy slots: "8:30 PM Dining Room", "10:00 PM", buttons/links with times
    time_re = re.compile(r"(\d{1,2}:\d{2}\s*(?:AM|PM)?)", re.I)
    for el in soup.find_all(["button", "a", "span"]):
        text = el.get_text(strip=True)
        if len(text) > 80:
            continue
        match = time_re.search(text)
        if match:
            time_str = match.group(1).strip()
            if time_str not in seen:
                seen.add(time_str)
                extra = text[match.end():].strip()
                slots.append(Slot(time_str=time_str, party_size=party_size, extra=extra[:50] if extra else ""))

    # data-time attributes
    for el in soup.find_all(attrs={"data-time": True}):
        t = el["data-time"].strip()
        if t and t not in seen:
            seen.add(t)
            slots.append(Slot(time_str=t, party_size=party_size))

    if not slots:
        return _parse_generic(html, target_date, party_size)
    return slots


def _parse_opentable(html: str, target_date: str, party_size: int) -> List[Slot]:
    """
    Parse OpenTable-style pages.
    Looks for their typical availability slot markup.
    """
    soup = BeautifulSoup(html, "html.parser")
    slots: List[Slot] = []

    # OpenTable uses data attributes and specific class patterns
    slot_elements = (
        soup.find_all(attrs={"data-test": re.compile(r"time-slot", re.I)})
        or soup.find_all(class_=re.compile(r"(timeslot|time-slot|availability)", re.I))
        or soup.find_all("button", string=re.compile(r"\d{1,2}:\d{2}\s*(AM|PM)", re.I))
    )

    for el in slot_elements:
        time_text = el.get_text(strip=True)
        if re.search(r"\d{1,2}:\d{2}", time_text):
            slots.append(Slot(time_str=time_text, party_size=party_size))

    if not slots:
        return _parse_generic(html, target_date, party_size)
    return slots


def _parse_yelp(html: str, target_date: str, party_size: int) -> List[Slot]:
    """Parse Yelp reservation pages."""
    soup = BeautifulSoup(html, "html.parser")
    slots: List[Slot] = []

    slot_elements = soup.find_all(
        class_=re.compile(r"(reservation|time.?slot|booking)", re.I)
    )
    for el in slot_elements:
        time_text = el.get_text(strip=True)
        if re.search(r"\d{1,2}:\d{2}", time_text):
            slots.append(Slot(time_str=time_text, party_size=party_size))

    if not slots:
        return _parse_generic(html, target_date, party_size)
    return slots


def _parse_generic(html: str, target_date: str, party_size: int) -> List[Slot]:
    """
    Best-effort generic parser.
    Scans for anything that looks like a bookable time slot.
    """
    soup = BeautifulSoup(html, "html.parser")
    slots: List[Slot] = []
    seen = set()

    # Strategy 1: buttons / links with time-like text
    for el in soup.find_all(["button", "a", "span", "div"]):
        text = el.get_text(strip=True)
        if re.match(r"^\d{1,2}:\d{2}\s*(AM|PM)?$", text, re.I):
            if text not in seen:
                seen.add(text)
                slots.append(Slot(time_str=text, party_size=party_size))

    # Strategy 2: data attributes
    for el in soup.find_all(attrs={"data-time": True}):
        t = el["data-time"]
        if t not in seen:
            seen.add(t)
            slots.append(Slot(time_str=t, party_size=party_size))

    # Strategy 3: elements whose class names suggest availability
    for el in soup.find_all(class_=re.compile(r"(slot|avail|book|reserv)", re.I)):
        text = el.get_text(strip=True)
        match = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)?)", text, re.I)
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            slots.append(Slot(time_str=match.group(1), party_size=party_size))

    return slots


# ── Scraper registry ─────────────────────────────────────────────────

SCRAPERS = {
    "resy": _parse_resy,
    "opentable": _parse_opentable,
    "yelp": _parse_yelp,
    "generic": _parse_generic,
}


def detect_platform(url: str) -> str:
    """Auto-detect the booking platform from the URL."""
    domain = urlparse(url).netloc.lower()
    if "resy" in domain:
        return "resy"
    if "opentable" in domain:
        return "opentable"
    if "yelp" in domain:
        return "yelp"
    return "generic"


# ── Public API ────────────────────────────────────────────────────────

def check_availability(
    url: str,
    target_date: str,
    party_size: int = 2,
    platform: str = "auto",
) -> dict:
    """
    Check availability for a restaurant.

    Returns
    -------
    dict with keys:
        success : bool
        slots   : list[dict]
        message : str
        html_hash : str
    """
    if platform == "auto":
        platform = detect_platform(url)

    # Resy is JS-rendered; use Playwright to get slot data
    use_browser = platform == "resy"
    html = fetch_page(url, use_browser=use_browser)
    if html is None:
        return {
            "success": False,
            "slots": [],
            "message": "Failed to fetch page (blocked by robots.txt or network error).",
            "html_hash": "",
        }

    parser = SCRAPERS.get(platform, _parse_generic)
    try:
        raw_slots = parser(html, target_date, party_size)
    except Exception as exc:
        logger.exception("Parser error for %s: %s", url, exc)
        raw_slots = []

    return {
        "success": True,
        "slots": [s.to_dict() for s in raw_slots],
        "message": f"Found {len(raw_slots)} slot(s) via '{platform}' parser.",
        "html_hash": html_hash(html),
    }
