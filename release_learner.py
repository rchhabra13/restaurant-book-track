"""
Release pattern learner for Restaurant Booking Tracker.

Analyses the historical availability snapshots stored in MongoDB to
automatically detect *when* a restaurant releases reservations:

  - How many days before the dining date do slots appear?
  - What time of day (ET) does that happen?

Once learned, the scheduler uses this to enter "burst mode" — checking
every 10 seconds in the minutes before the predicted release — instead
of waiting up to 15 minutes for a normal poll to catch it.

How it works
────────────
1. For each restaurant, pull all availability snapshots.
2. For each target_date that was being watched, find the EARLIEST
   snapshot that contained ≥1 slot ("first_seen").
3. Compute days_ahead  = (target_date - first_seen.date)
           time_of_day = first_seen.time  (in ET)
4. If ≥3 data points agree within ±1 hour, we have a confident pattern.
5. Store the learned pattern back in the restaurants collection.

Usage
─────
  from release_learner import learn_all, predict_next_release
  learn_all()                       # call once a day
  dt = predict_next_release(watch)  # call per-watch in scheduler
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timedelta, timezone, date as date_type
from typing import Optional, List
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MIN_SAMPLES    = 3      # minimum data points to declare a pattern
MAX_STD_HOURS  = 1.5    # max standard deviation (hours) to be "confident"


# ── Public API ────────────────────────────────────────────────────────

def learn_all() -> None:
    """
    Run pattern learning for every restaurant that has enough history.
    Call this once daily (the scheduler registers it as a nightly job).
    """
    from database import get_restaurants, update_restaurant_release

    restaurants = get_restaurants(active_only=False)
    learned = 0
    for r in restaurants:
        pattern = analyze_restaurant(r["id"])
        if pattern:
            update_restaurant_release(
                restaurant_id = r["id"],
                days_ahead    = pattern["days_ahead"],
                release_time  = pattern["release_time"],
                learned       = True,
                confidence    = pattern["confidence"],
            )
            learned += 1
            logger.info(
                "Learned release for %s: %dd ahead at %s ET (confidence=%.0f%%)",
                r["name"], pattern["days_ahead"], pattern["release_time"],
                pattern["confidence"] * 100,
            )

    logger.info("Release learner: updated %d / %d restaurants.", learned, len(restaurants))


def analyze_restaurant(restaurant_id: str) -> Optional[dict]:
    """
    Analyse availability history for one restaurant.

    Returns
    -------
    dict with keys:
        days_ahead    : int   — median days before dining date
        release_time  : str   — "HH:MM" in ET (median time of first slot)
        confidence    : float — 0.0–1.0
    or None if not enough data.
    """
    from database import get_db

    db = get_db()

    # Find all watches for this restaurant
    watch_ids = [
        str(w["_id"])
        for w in db.watches.find({"restaurant_id": str(restaurant_id)}, {"_id": 1, "target_date": 1})
    ]

    if not watch_ids:
        return None

    data_points: List[dict] = []

    for wid in watch_ids:
        # Get all availability docs for this watch, sorted ascending
        docs = list(
            db.availability.find(
                {"watch_id": wid},
                sort=[("checked_at", 1)],
            )
        )
        if not docs:
            continue

        # Find the watch's target_date
        watch_doc = db.watches.find_one({"_id": __to_oid(wid)})
        if not watch_doc:
            continue
        try:
            target = datetime.strptime(watch_doc["target_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue

        # Find earliest snapshot with ≥1 slot
        first_seen: Optional[datetime] = None
        for doc in docs:
            if doc.get("slots_found"):
                first_seen = doc["checked_at"]
                break

        if first_seen is None:
            continue

        # Convert to ET
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        first_seen_et = first_seen.astimezone(ET)

        days_ahead   = (target - first_seen_et.date()).days
        time_minutes = first_seen_et.hour * 60 + first_seen_et.minute

        if 0 <= days_ahead <= 180:   # sanity check
            data_points.append({
                "days_ahead":   days_ahead,
                "time_minutes": time_minutes,
                "first_seen_et": first_seen_et,
            })

    if len(data_points) < MIN_SAMPLES:
        logger.debug(
            "restaurant %s: only %d sample(s) — need %d to learn",
            restaurant_id, len(data_points), MIN_SAMPLES,
        )
        return None

    days_list   = [d["days_ahead"]   for d in data_points]
    time_list   = [d["time_minutes"] for d in data_points]

    median_days = round(statistics.median(days_list))

    # Circular mean for time-of-day (handles midnight wraparound correctly)
    angles = [2 * math.pi * t / 1440 for t in time_list]
    sin_mean = sum(math.sin(a) for a in angles) / len(angles)
    cos_mean = sum(math.cos(a) for a in angles) / len(angles)
    mean_angle = math.atan2(sin_mean, cos_mean) % (2 * math.pi)
    median_time = round(mean_angle * 1440 / (2 * math.pi))

    # Circular standard deviation for confidence (handles midnight wraparound)
    R = math.sqrt(sin_mean ** 2 + cos_mean ** 2)
    R = min(R, 1.0)  # numerical safety
    if R < 1e-10:
        std_time_hours = 12.0  # maximum dispersion
    else:
        std_time_hours = math.sqrt(-2 * math.log(R)) * 1440 / (2 * math.pi * 60)

    raw_confidence = max(0.0, 1.0 - std_time_hours / MAX_STD_HOURS)
    # Also boost confidence with more samples
    sample_factor  = min(1.0, len(data_points) / 10)
    confidence     = round(raw_confidence * 0.7 + sample_factor * 0.3, 2)

    hours, mins = divmod(median_time, 60)
    release_time = f"{hours:02d}:{mins:02d}"

    return {
        "days_ahead":   median_days,
        "release_time": release_time,      # "HH:MM" ET
        "confidence":   confidence,
        "sample_count": len(data_points),
    }


def predict_next_release(restaurant: dict, target_date: str) -> Optional[datetime]:
    """
    Given a restaurant record (with learned or manual release fields)
    and a target_date, predict the exact UTC datetime when slots will
    be released.

    Returns None if no release schedule is known.
    """
    days_ahead   = restaurant.get("release_days_ahead")
    release_time = restaurant.get("release_time_et")   # "HH:MM"

    if days_ahead is None or not release_time:
        return None

    try:
        dining_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        release_date = dining_date - timedelta(days=days_ahead)

        hh, mm = map(int, release_time.split(":"))
        release_et = datetime(
            release_date.year, release_date.month, release_date.day,
            hh, mm, tzinfo=ET,
        )
        return release_et.astimezone(timezone.utc)

    except Exception as exc:
        logger.warning("predict_next_release failed: %s", exc)
        return None


def get_upcoming_releases(window_minutes: int = 15) -> List[dict]:
    """
    Return a list of (watch, predicted_release_utc) pairs where the
    release is within *window_minutes* in the future.

    Used by the scheduler to decide which watches need burst mode.
    """
    from database import get_watches, get_db
    from bson import ObjectId

    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc + timedelta(minutes=window_minutes)

    watches = get_watches(active_only=True)
    if not watches:
        return []

    # Batch-fetch all restaurant docs in one query instead of N queries
    db = get_db()
    rest_ids = list({w["restaurant_id"] for w in watches})
    rest_docs = {
        str(r["_id"]): r
        for r in db.restaurants.find(
            {"_id": {"$in": [__to_oid(rid) for rid in rest_ids]}}
        )
    }

    results: List[dict] = []

    for w in watches:
        rest = rest_docs.get(w["restaurant_id"])
        if not rest:
            continue

        # For range/any watches use date_from (or today) as the target dining date
        mode = w.get("date_mode", "single")
        if mode == "single":
            check_date = w["target_date"]
        elif mode == "range":
            check_date = w.get("date_from") or w["target_date"]
        else:
            # "any" — use today as the earliest possible dining date
            check_date = date_type.today().isoformat()

        # Skip if check_date is not a parseable YYYY-MM-DD
        try:
            datetime.strptime(check_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        predicted = predict_next_release(rest, check_date)
        if predicted is None:
            continue

        if now_utc <= predicted <= cutoff:
            results.append({
                "watch":        w,
                "release_utc":  predicted,
                "minutes_away": round((predicted - now_utc).total_seconds() / 60, 1),
            })

    return results


# ── Internal helper ───────────────────────────────────────────────────

def __to_oid(id_val):
    from bson import ObjectId
    if isinstance(id_val, ObjectId):
        return id_val
    return ObjectId(id_val)
