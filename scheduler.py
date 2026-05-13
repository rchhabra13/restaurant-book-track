"""
Background scheduler for Restaurant Booking Tracker.

Jobs
────
1. check_all_watches()       – normal poll, every CHECK_INTERVAL_MINUTES (default 15)
2. _monitor_release_windows() – every 5 min: detect upcoming releases,
                                activate burst mode for those watches
3. _burst_check()            – every BURST_CHECK_INTERVAL_SECONDS (default 10)
                                only runs when burst_watches set is non-empty
4. _nightly_learn()          – 02:00 ET daily: run release pattern learner

Slot-detection pipeline (per watch)
────────────────────────────────────
  If restaurant has venue_id → use api_client (fast, structured JSON)
  Otherwise                  → use scraper (Playwright/requests HTML parse)

On slots found:
  → Save to availability collection
  → Send Telegram alert (with cooldown)
  → If auto_book=on AND AUTO_BOOK_ENABLED → call booker, log result, deactivate watch
"""

from __future__ import annotations

import html as _html
import logging
import threading
import time as _time
from datetime import date as _date, datetime, timezone, timedelta
from typing import Optional, Set

import requests as http_requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    CHECK_INTERVAL_MINUTES,
    REQUEST_DELAY_SECONDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    AUTO_BOOK_ENABLED,
    BURST_CHECK_INTERVAL_SECONDS,
    BURST_WINDOW_MINUTES,
    RESY_EMAIL,
    RESY_PASSWORD,
    OPENTABLE_EMAIL,
    OPENTABLE_PASSWORD,
)
from database import (
    get_watches,
    save_availability,
    get_latest_availability,
    was_recently_alerted,
    log_alert,
    was_recently_booked,
    log_booking,
    deactivate_watch,
    update_restaurant_venue_id,
    cleanup_past_watches,
    cleanup_old_alerts,
)
from scraper import check_availability

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None

# watches whose release window is imminent — checked every BURST_CHECK_INTERVAL_SECONDS
_burst_watch_ids: Set[str] = set()
_burst_lock = threading.Lock()

# Diff-driven burst entries carry an expiry timestamp (epoch seconds).
# Release-driven entries are absent here and only expire when watch deactivates.
_burst_expiry: dict[str, float] = {}
DIFF_BURST_DURATION_SECONDS = 300  # 5 min of accelerated checks after a positive delta


def _esc(text) -> str:
    """Escape user-controlled text for Telegram HTML parse mode."""
    return _html.escape(str(text))

# ── Singleton API clients (login once, reuse session for all checks) ──
_resy_client = None
_resy_login_last_attempt: float = 0
RESY_LOGIN_COOLDOWN_SECONDS = 300  # 5 minutes between login retries

_opentable_client = None
_opentable_login_last_attempt: float = 0
OPENTABLE_LOGIN_COOLDOWN_SECONDS = 60


def _get_resy_client():
    """
    Return a logged-in ResyClient.

    Login is attempted at most once per RESY_LOGIN_COOLDOWN_SECONDS to avoid
    Resy's HTTP 419 rate-limiting when many watches fire in the same cycle.
    """
    global _resy_client, _resy_login_last_attempt

    # Already have a working client
    if _resy_client is not None and _resy_client.token:
        return _resy_client

    if not (RESY_EMAIL and RESY_PASSWORD):
        return None

    now = _time.time()
    if now - _resy_login_last_attempt < RESY_LOGIN_COOLDOWN_SECONDS:
        # Still in cooldown — don't hammer Resy with login attempts
        return None

    # Attempt login (record time regardless of outcome so failures are throttled too)
    _resy_login_last_attempt = now
    logger.info("Attempting Resy login…")
    import metrics
    metrics.log("login_attempt", platform="resy")
    from api_client import get_client
    client = get_client("resy", email=RESY_EMAIL, password=RESY_PASSWORD)
    if client and client.token:
        _resy_client = client
        logger.info("Resy login succeeded.")
        metrics.log("login_success", platform="resy")
        return _resy_client

    logger.warning(
        "Resy login failed — will retry in %ds.", RESY_LOGIN_COOLDOWN_SECONDS
    )
    metrics.log("login_failed", platform="resy")
    _resy_client = None  # ensure stale object not reused
    return None


def _get_opentable_client():
    """Return an OpenTableClient, creating one lazily on first call."""
    global _opentable_client, _opentable_login_last_attempt

    if _opentable_client is not None:
        return _opentable_client

    now = _time.time()
    if now - _opentable_login_last_attempt < OPENTABLE_LOGIN_COOLDOWN_SECONDS:
        return None

    _opentable_login_last_attempt = now
    from api_client import get_client
    _opentable_client = get_client("opentable", email=OPENTABLE_EMAIL, password=OPENTABLE_PASSWORD)
    return _opentable_client

# ══════════════════════════════════════════════════════════════════════
# Notification helpers — delegates to notify.py (multi-channel)
# ══════════════════════════════════════════════════════════════════════

def _send_telegram_alert(chat_id: str, text: str) -> bool:
    """
    Low-level Telegram send (used for booking/error/custom messages that
    already have pre-built HTML). Slot alerts go through notify.send_alert().
    """
    from notify import _send_telegram  # noqa: PLC0415
    return _send_telegram(text, chat_id)


ANY_DATE_WINDOW_DAYS = 30  # how far ahead "any" mode looks


def _dates_for_watch(watch: dict) -> list[str]:
    """Return the ordered list of dates to check for a watch."""
    mode = watch.get("date_mode", "single")
    today = _date.today()

    if mode == "single":
        return [watch["target_date"]]

    if mode == "any":
        start = today
        end   = today + timedelta(days=ANY_DATE_WINDOW_DAYS)
    else:  # range
        start = _date.fromisoformat(watch.get("date_from") or watch["target_date"])
        end   = _date.fromisoformat(watch.get("date_to")   or watch["target_date"])
        start = max(start, today)

    dates, current = [], start
    while current <= end:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def _format_alert(watch: dict, slots: list) -> str:
    slot_lines = "\n".join(
        f"  🕐 {_esc(s.get('time', '?'))}"
        + (f" — {_esc(s.get('extra', ''))}" if s.get("extra") else "")
        for s in slots[:10]
    )
    more  = f"\n  … and {len(slots) - 10} more" if len(slots) > 10 else ""
    name  = _esc(watch.get("restaurant_name", "Unknown"))
    url   = _esc(watch.get("restaurant_url",  ""))
    text  = (
        f"🍽 <b>Reservation Alert!</b>\n\n"
        f"<b>{name}</b>\n"
        f"📅 {watch['target_date']}  👥 Party of {watch['party_size']}\n\n"
        f"<b>Available slots:</b>\n{slot_lines}{more}\n\n"
    )
    if url:
        text += f'<a href="{url}">Book now →</a>'
    return text


def _format_range_alert(watch: dict, dates_with_slots: dict) -> str:
    name  = _esc(watch.get("restaurant_name", "Unknown"))
    url   = _esc(watch.get("restaurant_url",  ""))
    lines = [
        f"🍽 <b>Reservation Alert!</b>\n\n"
        f"<b>{name}</b>  👥 Party of {watch['party_size']}\n"
    ]
    for date_str, slots in sorted(dates_with_slots.items()):
        slot_lines = "\n".join(
            f"    🕐 {_esc(s.get('time', '?'))}"
            + (f" — {_esc(s.get('extra', ''))}" if s.get("extra") else "")
            for s in slots[:5]
        )
        more = f"\n    … and {len(slots) - 5} more" if len(slots) > 5 else ""
        lines.append(f"📅 <b>{_esc(date_str)}</b>\n{slot_lines}{more}")
    if url:
        lines.append(f'<a href="{url}">Book now →</a>')
    return "\n\n".join(lines)


def _format_booking_alert(watch: dict, booking_result) -> str:
    name  = _esc(watch.get("restaurant_name", "Unknown"))
    date  = _esc(watch.get("target_date", "?"))
    party = _esc(watch.get("party_size", "?"))
    url   = _esc(watch.get("restaurant_url", ""))
    if booking_result.success:
        conf = (
            f"\n🔖 Confirmation: <b>{_esc(booking_result.confirmation_number)}</b>"
            if booking_result.confirmation_number else ""
        )
        return (
            f"🎉 <b>Auto-booking successful!</b>\n\n"
            f"<b>{name}</b>\n"
            f"📅 {date}  👥 Party of {party}{conf}\n\n"
            f"<i>{_esc(booking_result.message)}</i>"
        )
    return (
        f"⚠️ <b>Auto-booking attempted — needs attention</b>\n\n"
        f"<b>{name}</b>\n"
        f"📅 {date}  👥 Party of {party}\n\n"
        f"<i>{_esc(booking_result.message)}</i>\n\n"
        f'<a href="{url}">Book manually →</a>'
    )


def _format_burst_alert(watch: dict, minutes_away: float) -> str:
    name = _esc(watch.get("restaurant_name", "Unknown"))
    return (
        f"⏰ <b>Release window approaching!</b>\n\n"
        f"<b>{name}</b> reservations for {_esc(watch['target_date'])} "
        f"are predicted to drop in <b>{minutes_away:.0f} min</b>.\n\n"
        f"🔥 Switching to burst-check mode (every {BURST_CHECK_INTERVAL_SECONDS}s)."
    )


# ══════════════════════════════════════════════════════════════════════
# Slot-checking pipeline
# ══════════════════════════════════════════════════════════════════════

def _get_slots_via_api(watch: dict) -> list:
    """
    Use the direct platform API (fast, no Playwright).
    Falls back to scraper if API returns nothing.
    """
    platform  = watch.get("restaurant_platform", "generic")
    venue_id  = watch.get("restaurant_venue_id")

    if platform not in ("resy", "opentable") or not venue_id:
        return []

    try:
        if platform == "resy":
            client = _get_resy_client()
        else:
            client = _get_opentable_client()

        if not client:
            return []

        api_slots = client.get_slots(venue_id, watch["target_date"], watch["party_size"])
        return [s.to_dict() for s in api_slots]

    except Exception as exc:
        logger.warning("API slot fetch failed for watch %s: %s", watch.get("id"), exc)
        return []


def _resolve_venue_id(watch: dict) -> None:
    """
    If this restaurant is on Resy/OpenTable and has no venue_id yet,
    look it up via the API and persist it.
    """
    platform = watch.get("restaurant_platform", "generic")
    if platform not in ("resy", "opentable"):
        return
    if watch.get("restaurant_venue_id"):
        return  # already resolved

    try:
        if platform == "resy":
            client = _get_resy_client()
            if not client:
                logger.warning("Resy client unavailable — skipping venue ID lookup for %s", watch.get("restaurant_name"))
                return
            venue_id = client.get_venue_id(watch["restaurant_url"])
        else:
            client = _get_opentable_client()
            if not client:
                return
            venue_id = client.get_restaurant_id(watch["restaurant_url"])

        if venue_id:
            update_restaurant_venue_id(watch["restaurant_id"], venue_id)
            watch["restaurant_venue_id"] = venue_id
            logger.info("Resolved venue_id=%s for %s", venue_id, watch["restaurant_name"])
    except Exception as exc:
        logger.warning("Venue ID resolution failed for %s: %s", watch.get("restaurant_name"), exc)


def _fetch_slots_for_date(watch: dict, target_date: str) -> dict:
    """
    Fetch slots for one specific date — no persistence or alerting.

    Uses ``slot_cache`` so concurrent watches on the same
    (platform, venue, date, party_size) share one API call within a short TTL,
    and triggers diff-driven burst acceleration on positive slot-count deltas.
    """
    from slot_cache import get_or_fetch, record_and_diff

    w = {**watch, "target_date": target_date}
    platform = w.get("restaurant_platform", "generic")
    venue_id = w.get("restaurant_venue_id") or w["restaurant_url"]
    party    = w["party_size"]
    cache_key = (platform, venue_id, target_date, party)

    def _do_fetch() -> dict:
        import rate_limiter
        rate_limiter.acquire(platform)
        slots = _get_slots_via_api(w)
        html_hash = ""
        success = True
        if not slots:
            rate_limiter.acquire(platform)
            r = check_availability(
                url         = w["restaurant_url"],
                target_date = target_date,
                party_size  = party,
                platform    = platform,
            )
            slots     = r["slots"]
            html_hash = r["html_hash"]
            success   = r["success"]
        return {"slots": slots, "html_hash": html_hash, "success": success}

    result = get_or_fetch(cache_key, _do_fetch)

    # Diff-driven acceleration: positive delta = cancellation/release → burst the venue.
    delta = record_and_diff(cache_key, len(result["slots"]))
    if delta > 0:
        _accelerate_venue(platform, w.get("restaurant_venue_id"),
                          target_date, party, reason=f"+{delta} slots")

    return result


def _accelerate_venue(platform: str, venue_id, target_date: str,
                      party_size: int, reason: str = "") -> None:
    """
    Push all active watches matching this venue+date+party into burst mode
    for ``DIFF_BURST_DURATION_SECONDS``. Caught cancellation cascades stay hot.
    """
    matching: list[str] = []
    for w in get_watches(active_only=True):
        if w.get("restaurant_platform") != platform:
            continue
        if w.get("restaurant_venue_id") != venue_id:
            continue
        if w["party_size"] != party_size:
            continue
        if target_date in _dates_for_watch(w):
            matching.append(w["id"])

    if not matching:
        return

    expiry = _time.time() + DIFF_BURST_DURATION_SECONDS
    with _burst_lock:
        added = [wid for wid in matching if wid not in _burst_watch_ids]
        for wid in matching:
            _burst_watch_ids.add(wid)
            _burst_expiry[wid] = expiry

    if added:
        import metrics
        for wid in added:
            metrics.log("burst_diff_on", watch_id=wid, platform=platform,
                        venue_id=venue_id, target_date=target_date, reason=reason)
        logger.info(
            "DIFF BURST: %d watch(es) accelerated (%s) at %s/%s/%s for %ds",
            len(added), reason, platform, venue_id, target_date,
            DIFF_BURST_DURATION_SECONDS,
        )


def _check_watch_date_range(watch: dict) -> dict:
    """
    Check availability across all dates for range/any watches.
    Collects slots per date, sends one combined alert per cooldown window.
    """
    dates = _dates_for_watch(watch)
    dates_with_slots: dict[str, list] = {}
    success = True

    for target_date in dates:
        try:
            result = _fetch_slots_for_date(watch, target_date)
            if result["slots"]:
                dates_with_slots[target_date] = result["slots"]
            if not result["success"]:
                success = False
        except Exception as exc:
            logger.warning("Date %s check failed for watch %s: %s", target_date, watch.get("id"), exc)

    watch_id  = watch["id"]
    all_slots = [s for slots in dates_with_slots.values() for s in slots]
    save_availability(watch_id, all_slots, "")

    if dates_with_slots:
        chat_id = watch.get("chat_id", "")

        should_auto_book = (
            AUTO_BOOK_ENABLED
            and watch.get("auto_book", False)
            and not was_recently_booked(watch_id)
        )
        if should_auto_book:
            first_date = sorted(dates_with_slots)[0]
            first_slot = dates_with_slots[first_date][0]
            logger.info("Auto-booking triggered for watch %s (date %s)", watch_id, first_date)
            try:
                from booker import attempt_booking
                booking_watch  = {**watch, "target_date": first_date}
                booking_result = attempt_booking(booking_watch, first_slot)
                log_booking(watch_id, booking_result.to_dict())
                _send_telegram_alert(chat_id, _format_booking_alert(booking_watch, booking_result))
                if booking_result.success:
                    deactivate_watch(watch_id)
                    with _burst_lock:
                        _burst_watch_ids.discard(watch_id)
                        _burst_expiry.pop(watch_id, None)
            except Exception as exc:
                logger.exception("Auto-booking error: %s", exc)
                _send_telegram_alert(
                    chat_id,
                    f"⚠️ Auto-booking crashed for <b>{_esc(watch.get('restaurant_name'))}</b>: {_esc(exc)}",
                )

        if not was_recently_alerted(watch_id):
            from notify import send_range_alert
            sent = send_range_alert(watch, dates_with_slots, tg_chat_id=chat_id)
            if sent:
                log_alert(watch_id, f"Found slots on {len(dates_with_slots)} date(s)")

    return {"success": success, "slots": all_slots, "dates_with_slots": dates_with_slots}


def check_single_watch(watch: dict) -> dict:
    """
    Check availability for one watch.

    Pipeline
    ────────
    1. Resolve venue_id if missing.
    2. For range/any watches → iterate over all dates in the window.
    3. For single-date watches → API call (fast) or HTML scraper fallback.
    4. Persist result, send alert, trigger auto-book if enabled.
    """
    import metrics
    with _burst_lock:
        is_burst = watch.get("id") in _burst_watch_ids
    logger.info(
        "Checking: %s on %s (party %d)%s",
        watch["restaurant_name"], watch["target_date"], watch["party_size"],
        " [BURST]" if is_burst else "",
    )
    metrics.log("check_start",
                watch_id=watch.get("id"),
                venue_id=watch.get("restaurant_venue_id"),
                platform=watch.get("restaurant_platform"),
                target_date=watch["target_date"],
                party_size=watch["party_size"],
                burst=is_burst)
    _check_t0 = _time.perf_counter()

    _resolve_venue_id(watch)

    # Skip single-date watches whose target has already passed
    if watch.get("date_mode", "single") == "single":
        try:
            if _date.fromisoformat(watch["target_date"]) < _date.today():
                logger.debug("Skipping past-date watch %s (%s)", watch.get("id"), watch["target_date"])
                deactivate_watch(watch.get("id", ""))
                return {"success": True, "slots": [], "html_hash": ""}
        except (ValueError, KeyError):
            pass

    if watch.get("date_mode", "single") != "single":
        out = _check_watch_date_range(watch)
        metrics.log("check_done",
                    watch_id=watch.get("id"),
                    slots=len(out.get("slots", [])),
                    dates_with_slots=len(out.get("dates_with_slots", {})),
                    duration_ms=round((_time.perf_counter() - _check_t0) * 1000, 2),
                    burst=is_burst)
        return out

    # ── Single-date path ──────────────────────────────────────────────
    result    = _fetch_slots_for_date(watch, watch["target_date"])
    slots     = result["slots"]
    html_hash = result["html_hash"]
    success   = result["success"]

    watch_id = watch["id"]
    save_availability(watch_id, slots, html_hash)

    if slots:
        chat_id = watch.get("chat_id", "")

        should_auto_book = (
            AUTO_BOOK_ENABLED
            and watch.get("auto_book", False)
            and not was_recently_booked(watch_id)
        )
        if should_auto_book:
            logger.info("Auto-booking triggered for watch %s", watch_id)
            try:
                from booker import attempt_booking
                booking_result = attempt_booking(watch, slots[0])
                log_booking(watch_id, booking_result.to_dict())
                _send_telegram_alert(chat_id, _format_booking_alert(watch, booking_result))
                if booking_result.success:
                    deactivate_watch(watch_id)
                    with _burst_lock:
                        _burst_watch_ids.discard(watch_id)
                        _burst_expiry.pop(watch_id, None)
                    logger.info("Watch %s deactivated after successful booking.", watch_id)
            except Exception as exc:
                logger.exception("Auto-booking error: %s", exc)
                _send_telegram_alert(
                    chat_id,
                    f"⚠️ Auto-booking crashed for <b>{_esc(watch.get('restaurant_name'))}</b>: {_esc(exc)}",
                )

        if not was_recently_alerted(watch_id):
            from notify import send_alert
            sent = send_alert(watch, slots, tg_chat_id=chat_id)
            if sent:
                log_alert(watch_id, f"Found {len(slots)} slots")

    metrics.log("check_done",
                watch_id=watch.get("id"),
                slots=len(slots),
                duration_ms=round((_time.perf_counter() - _check_t0) * 1000, 2),
                burst=is_burst,
                success=success)
    return {"success": success, "slots": slots, "html_hash": html_hash}


# ══════════════════════════════════════════════════════════════════════
# Normal check — all watches
# ══════════════════════════════════════════════════════════════════════

SCHEDULER_TICK_SECONDS = 30  # how often check_all_watches fires; per-watch is_due() filters


def check_all_watches():
    """
    Adaptive tick. Iterates all active watches and only checks those whose
    per-watch interval (from ``priority.compute_interval_seconds``) has elapsed.
    Burst-mode watches are skipped here — handled by ``_burst_check``.
    Per-domain rate limiting is enforced inside ``_fetch_slots_for_date``.
    """
    import priority

    watches = get_watches(active_only=True)
    with _burst_lock:
        burst_snapshot = set(_burst_watch_ids)

    due = [w for w in watches if w["id"] not in burst_snapshot and priority.is_due(w)]
    logger.info(
        "Adaptive tick — %d due / %d active (%d in burst)",
        len(due), len(watches), len(burst_snapshot),
    )

    for watch in due:
        try:
            check_single_watch(watch)
        except Exception as exc:
            logger.exception("Error checking watch %s: %s", watch.get("id"), exc)
        finally:
            priority.mark_checked(watch["id"], priority.compute_interval_seconds(watch))


# ══════════════════════════════════════════════════════════════════════
# Burst mode
# ══════════════════════════════════════════════════════════════════════

def _monitor_release_windows():
    """
    Run every 5 minutes. Checks which watches have a release window
    approaching within BURST_WINDOW_MINUTES and puts them into burst mode.
    Also cleans up expired burst entries.
    """
    global _burst_watch_ids

    try:
        from release_learner import get_upcoming_releases
        upcoming = get_upcoming_releases(window_minutes=BURST_WINDOW_MINUTES)
    except Exception as exc:
        logger.debug("Release monitor error: %s", exc)
        return

    now = datetime.now(timezone.utc)

    for item in upcoming:
        w   = item["watch"]
        wid = w["id"]
        mins = item["minutes_away"]

        with _burst_lock:
            is_new = wid not in _burst_watch_ids
            if is_new:
                _burst_watch_ids.add(wid)
        if is_new:
            import metrics
            metrics.log("burst_release_on", watch_id=wid,
                        restaurant_name=w.get("restaurant_name"),
                        minutes_away=round(mins, 2))
            logger.info(
                "BURST MODE ON: %s release in %.1f min",
                w["restaurant_name"], mins,
            )
            from notify import send_burst_warning
            send_burst_warning(w, mins, tg_chat_id=w.get("chat_id", ""))

    # Expire burst entries: deactivated watches, or diff-burst entries past their TTL.
    with _burst_lock:
        has_burst = bool(_burst_watch_ids)
    if has_burst:
        active_ids = {w["id"] for w in get_watches(active_only=True)}
        now_ts = _time.time()
        with _burst_lock:
            expired: set[str] = set()
            for wid in list(_burst_watch_ids):
                if wid not in active_ids:
                    expired.add(wid)
                    continue
                exp = _burst_expiry.get(wid)
                if exp is not None and now_ts > exp:
                    expired.add(wid)
            for wid in expired:
                _burst_watch_ids.discard(wid)
                _burst_expiry.pop(wid, None)
        if expired:
            import metrics
            for wid in expired:
                metrics.log("burst_off", watch_id=wid)
            logger.info("Burst mode ended for %d watch(es).", len(expired))


def _burst_check():
    """
    Fires every BURST_CHECK_INTERVAL_SECONDS.
    Only checks watches that are in burst mode.
    """
    with _burst_lock:
        if not _burst_watch_ids:
            return
        burst_snapshot = set(_burst_watch_ids)

    watches      = get_watches(active_only=True)
    burst_watches = [w for w in watches if w["id"] in burst_snapshot]

    if burst_watches:
        logger.debug("Burst check: %d watch(es)", len(burst_watches))

    for watch in burst_watches:
        try:
            check_single_watch(watch)
        except Exception as exc:
            logger.exception("Burst check error for watch %s: %s", watch.get("id"), exc)


# ══════════════════════════════════════════════════════════════════════
# Release learning
# ══════════════════════════════════════════════════════════════════════

def _nightly_learn():
    """Run the release pattern learner. Registered as a daily 02:00 ET job."""
    logger.info("Running nightly release pattern learning…")
    try:
        from release_learner import learn_all
        learn_all()
    except Exception as exc:
        logger.exception("Nightly learner error: %s", exc)


ALERT_RETENTION_DAYS = 30


def _daily_cleanup():
    """
    Daily housekeeping: delete past watches + prune old alerts/availability.
    Also drops their priority-scheduling state.
    """
    try:
        import priority
        # Snapshot stale ids before deletion so we can forget priority state.
        stale_before = {w["id"] for w in get_watches(active_only=False)}
        result = cleanup_past_watches()
        stale_after = {w["id"] for w in get_watches(active_only=False)}
        for wid in stale_before - stale_after:
            priority.forget(wid)
            with _burst_lock:
                _burst_watch_ids.discard(wid)
                _burst_expiry.pop(wid, None)
        cleanup_old_alerts(days=ALERT_RETENTION_DAYS)
        import metrics as _m
        _m.cleanup_old_metrics(days=14)
        logger.info("Daily cleanup done: %s", result)
    except Exception as exc:
        logger.exception("Daily cleanup error: %s", exc)


# ══════════════════════════════════════════════════════════════════════
# Scheduler lifecycle
# ══════════════════════════════════════════════════════════════════════

def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.info("Scheduler already running.")
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)

    # 1. Adaptive tick — fires often; priority.is_due() filters which watches actually run.
    _scheduler.add_job(
        check_all_watches,
        "interval",
        seconds          = SCHEDULER_TICK_SECONDS,
        id               = "normal_check",
        replace_existing = True,
        max_instances    = 1,
        coalesce         = True,
        misfire_grace_time = 60,
        next_run_time    = datetime.now(timezone.utc),
    )

    # 2. Release window monitor (every 5 min)
    _scheduler.add_job(
        _monitor_release_windows,
        "interval",
        minutes       = 5,
        id            = "release_monitor",
        replace_existing = True,
    )

    # 3. Burst check (every N seconds — lightweight, only fires when burst set non-empty)
    _scheduler.add_job(
        _burst_check,
        "interval",
        seconds       = BURST_CHECK_INTERVAL_SECONDS,
        id            = "burst_check",
        replace_existing = True,
    )

    # 4. Nightly release learner — 02:00 ET
    _scheduler.add_job(
        _nightly_learn,
        CronTrigger(hour=2, minute=0, timezone="America/New_York"),
        id            = "nightly_learn",
        replace_existing = True,
    )

    # 5. Daily cleanup — 03:00 ET. Also runs once at startup to prune stale state.
    _scheduler.add_job(
        _daily_cleanup,
        CronTrigger(hour=3, minute=0, timezone="America/New_York"),
        id            = "daily_cleanup",
        replace_existing = True,
        next_run_time = datetime.now(timezone.utc),
    )

    _scheduler.start()
    logger.info(
        "Scheduler started — tick=%ds (adaptive per-watch), burst=%ds, release_monitor=5min, learner=02:00ET",
        SCHEDULER_TICK_SECONDS, BURST_CHECK_INTERVAL_SECONDS,
    )
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
        _scheduler = None


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def get_burst_watch_ids() -> Set[str]:
    with _burst_lock:
        return set(_burst_watch_ids)


# ── Standalone mode ───────────────────────────────────────────────────

if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Starting scheduler in standalone mode…")
    start_scheduler()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop_scheduler()
        logger.info("Bye.")
