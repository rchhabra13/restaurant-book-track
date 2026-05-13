"""
MongoDB persistence layer for the Restaurant Booking Tracker.

Collections
───────────
restaurants      – tracked restaurant records (scoped by chat_id)
watches          – date/party-size combinations to monitor
availability     – scraped availability snapshots
alert_log        – history of sent Telegram alerts (for cooldown)

Requires a running MongoDB instance. Configure via MONGO_URI in .env.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

from config import ALERT_COOLDOWN_MINUTES, MONGO_URI, MONGO_DB_NAME

logger = logging.getLogger(__name__)

# ── Connection ────────────────────────────────────────────────────────

_client: Optional[MongoClient] = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client


def get_db():
    return get_client()[MONGO_DB_NAME]


def init_db():
    """Create indexes for efficient querying."""
    db = get_db()
    db.restaurants.create_index("active")
    db.restaurants.create_index("chat_id")
    db.watches.create_index([("restaurant_id", 1), ("active", 1)])
    db.watches.create_index("chat_id")
    # Prevent duplicate watches for the same restaurant/date/party/time/chat
    try:
        db.watches.create_index(
            [
                ("restaurant_id", 1),
                ("target_date", 1),
                ("party_size", 1),
                ("time_preference", 1),
                ("chat_id", 1),
            ],
            unique=True,
            sparse=True,
        )
    except Exception as exc:
        logger.warning(
            "Could not create unique watch index (existing duplicates?): %s. "
            "Run deduplication then restart to enforce uniqueness.",
            exc,
        )
    db.availability.create_index([("watch_id", 1), ("checked_at", -1)])
    db.alert_log.create_index([("watch_id", 1), ("sent_at", -1)])
    db.bookings.create_index([("watch_id", 1), ("booked_at", -1)])
    logger.info("MongoDB indexes ensured.")


def check_connection() -> bool:
    """Return True if MongoDB is reachable."""
    try:
        get_client().admin.command("ping")
        return True
    except Exception as exc:
        logger.error("MongoDB connection failed: %s", exc)
        return False


# ── Helpers ───────────────────────────────────────────────────────────

def _str_id(doc: dict) -> dict:
    """Convert ObjectId fields to strings for easier handling."""
    if doc and "_id" in doc:
        doc["id"] = str(doc["_id"])
    return doc


def _to_object_id(id_val):
    """Safely convert to ObjectId."""
    if isinstance(id_val, ObjectId):
        return id_val
    return ObjectId(id_val)


# ── Restaurant CRUD ───────────────────────────────────────────────────

def add_restaurant(
    name: str, url: str, platform: str = "generic",
    notes: str = "", chat_id: str = ""
) -> str:
    db = get_db()
    doc = {
        "name": name,
        "url": url,
        "platform": platform,
        "notes": notes,
        "active": True,
        "chat_id": str(chat_id),
        "created_at": datetime.now(timezone.utc),
    }
    result = db.restaurants.insert_one(doc)
    return str(result.inserted_id)


def get_restaurants(active_only: bool = True, chat_id: str = "") -> List[dict]:
    db = get_db()
    query: dict = {}
    if active_only:
        query["active"] = True
    if chat_id:
        query["chat_id"] = str(chat_id)
    return [_str_id(doc) for doc in db.restaurants.find(query)]


def get_restaurant_by_id(restaurant_id: str) -> Optional[dict]:
    db = get_db()
    doc = db.restaurants.find_one({"_id": _to_object_id(restaurant_id)})
    return _str_id(doc) if doc else None


def find_restaurant_by_name(name: str, chat_id: str = "") -> Optional[dict]:
    """Case-insensitive search by name, scoped to chat_id if given."""
    db = get_db()
    query = {"name": {"$regex": f"^{name}$", "$options": "i"}}
    if chat_id:
        query["chat_id"] = str(chat_id)
    doc = db.restaurants.find_one(query)
    return _str_id(doc) if doc else None


def toggle_restaurant(restaurant_id: str, active: bool):
    db = get_db()
    db.restaurants.update_one(
        {"_id": _to_object_id(restaurant_id)},
        {"$set": {"active": active}},
    )


def update_restaurant_venue_id(restaurant_id: str, venue_id: int):
    """Store the platform venue_id so we can use the direct API."""
    db = get_db()
    db.restaurants.update_one(
        {"_id": _to_object_id(restaurant_id)},
        {"$set": {"venue_id": venue_id}},
    )


def update_restaurant_release(
    restaurant_id: str,
    days_ahead: int,
    release_time: str,          # "HH:MM" in ET
    learned: bool = False,
    confidence: float = 0.0,
):
    """
    Store the known or learned reservation release schedule.

    Parameters
    ----------
    days_ahead   : how many days before dining date slots drop
    release_time : "HH:MM" ET — e.g. "00:00" or "09:00"
    learned      : True if detected automatically from history
    confidence   : 0.0–1.0, only meaningful when learned=True
    """
    db = get_db()
    db.restaurants.update_one(
        {"_id": _to_object_id(restaurant_id)},
        {"$set": {
            "release_days_ahead": days_ahead,
            "release_time_et":    release_time,
            "release_learned":    learned,
            "release_confidence": round(confidence, 2),
        }},
    )


def delete_restaurant(restaurant_id: str):
    db = get_db()
    oid = _to_object_id(restaurant_id)
    # Also remove associated watches
    watch_ids = [
        str(w["_id"]) for w in db.watches.find({"restaurant_id": str(oid)}, {"_id": 1})
    ]
    for wid in watch_ids:
        db.availability.delete_many({"watch_id": wid})
        db.alert_log.delete_many({"watch_id": wid})
    db.watches.delete_many({"restaurant_id": str(oid)})
    db.restaurants.delete_one({"_id": oid})


# ── Watch CRUD ────────────────────────────────────────────────────────

def add_watch(
    restaurant_id: str,
    target_date: str,
    party_size: int = 2,
    time_preference: str = "any",
    chat_id: str = "",
    auto_book: bool = False,
    date_mode: str = "single",   # "single" | "range" | "any"
    date_from: str = "",         # YYYY-MM-DD, range start
    date_to: str = "",           # YYYY-MM-DD, range end
) -> str:
    db = get_db()
    doc = {
        "restaurant_id": str(restaurant_id),
        "target_date": target_date,
        "party_size": party_size,
        "time_preference": time_preference,
        "active": True,
        "auto_book": auto_book,
        "chat_id": str(chat_id),
        "date_mode": date_mode,
        "date_from": date_from,
        "date_to": date_to,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = db.watches.insert_one(doc)
    except DuplicateKeyError:
        existing = db.watches.find_one({
            "restaurant_id": str(restaurant_id),
            "target_date": target_date,
            "party_size": party_size,
            "time_preference": time_preference,
            "chat_id": str(chat_id),
        })
        if existing:
            logger.info("Watch already exists (id=%s) — returning existing.", existing["_id"])
            return str(existing["_id"])
        raise
    return str(result.inserted_id)


def set_watch_auto_book(watch_id: str, enabled: bool):
    """Toggle auto-booking for a specific watch."""
    db = get_db()
    db.watches.update_one(
        {"_id": _to_object_id(watch_id)},
        {"$set": {"auto_book": enabled}},
    )


def mark_waitlist_joined(watch_id: str, platform: str = ""):
    """Record that the notify/waitlist was joined for this watch."""
    db = get_db()
    db.watches.update_one(
        {"_id": _to_object_id(watch_id)},
        {"$set": {"waitlist_joined": True, "waitlist_platform": platform}},
    )


def get_watches(active_only: bool = True, chat_id: str = "") -> List[dict]:
    db = get_db()

    # Pre-filter on the watches collection (uses indexes) before the join
    pre_match: dict = {}
    if active_only:
        pre_match["active"] = True
    if chat_id:
        pre_match["chat_id"] = str(chat_id)

    pipeline: list = []
    if pre_match:
        pipeline.append({"$match": pre_match})

    pipeline += [
        {"$addFields": {"rest_oid": {"$toObjectId": "$restaurant_id"}}},
        {
            "$lookup": {
                "from": "restaurants",
                "localField": "rest_oid",
                "foreignField": "_id",
                "as": "restaurant",
            }
        },
        {"$unwind": "$restaurant"},
    ]

    # Post-join filter for restaurant.active (can't be pushed before join)
    if active_only:
        pipeline.append({"$match": {"restaurant.active": True}})

    results = []
    for doc in db.watches.aggregate(pipeline):
        flat = {
            "id": str(doc["_id"]),
            "restaurant_id": doc["restaurant_id"],
            "target_date": doc["target_date"],
            "party_size": doc["party_size"],
            "time_preference": doc.get("time_preference", "any"),
            "active": doc["active"],
            "chat_id": doc.get("chat_id", ""),
            "restaurant_name":     doc["restaurant"]["name"],
            "restaurant_url":      doc["restaurant"]["url"],
            "restaurant_platform": doc["restaurant"].get("platform", "generic"),
            "restaurant_venue_id": doc["restaurant"].get("venue_id"),
            "auto_book":           doc.get("auto_book", False),
            "waitlist_joined":     doc.get("waitlist_joined", False),
            "date_mode":           doc.get("date_mode", "single"),
            "date_from":           doc.get("date_from", ""),
            "date_to":             doc.get("date_to", ""),
        }
        results.append(flat)
    return results


def deactivate_watch(watch_id: str):
    db = get_db()
    db.watches.update_one(
        {"_id": _to_object_id(watch_id)},
        {"$set": {"active": False}},
    )


def delete_watch(watch_id: str):
    db = get_db()
    oid = _to_object_id(watch_id)
    db.availability.delete_many({"watch_id": str(oid)})
    db.alert_log.delete_many({"watch_id": str(oid)})
    db.watches.delete_one({"_id": oid})


# ── Availability ──────────────────────────────────────────────────────

def save_availability(watch_id: str, slots: list, html_hash: str = ""):
    db = get_db()
    db.availability.insert_one({
        "watch_id": str(watch_id),
        "checked_at": datetime.now(timezone.utc),
        "slots_found": slots,
        "raw_html_hash": html_hash,
    })


def get_latest_availability(watch_id: str) -> Optional[dict]:
    db = get_db()
    doc = db.availability.find_one(
        {"watch_id": str(watch_id)},
        sort=[("checked_at", -1)],
    )
    return _str_id(doc) if doc else None


# ── Alert log ─────────────────────────────────────────────────────────

def log_alert(watch_id: str, message: str):
    db = get_db()
    db.alert_log.insert_one({
        "watch_id": str(watch_id),
        "message": message,
        "sent_at": datetime.now(timezone.utc),
    })


def was_recently_alerted(watch_id: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    db = get_db()
    doc = db.alert_log.find_one({
        "watch_id": str(watch_id),
        "sent_at": {"$gt": cutoff},
    })
    return doc is not None


# ── Booking log ───────────────────────────────────────────────────────

def log_booking(watch_id: str, result: dict):
    """Persist the outcome of an auto-booking attempt."""
    db = get_db()
    db.bookings.insert_one({
        "watch_id": str(watch_id),
        "booked_at": datetime.now(timezone.utc),
        "success": result.get("success", False),
        "message": result.get("message", ""),
        "confirmation_number": result.get("confirmation_number", ""),
        "platform": result.get("platform", ""),
        "screenshot_path": result.get("screenshot_path", ""),
    })


def was_recently_booked(watch_id: str, within_hours: int = 24) -> bool:
    """Return True if a *successful* booking was logged within the last N hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    db = get_db()
    doc = db.bookings.find_one({
        "watch_id": str(watch_id),
        "success": True,
        "booked_at": {"$gt": cutoff},
    })
    return doc is not None


# ── Cleanup ───────────────────────────────────────────────────────────

def cleanup_past_watches() -> dict:
    """
    Delete watches whose target date window has fully passed, plus their
    availability snapshots, alert logs, and booking logs.

    Rules
    -----
    - ``single``  : delete if ``target_date < today``.
    - ``range``   : delete if ``date_to   < today`` (window fully expired).
    - ``any``     : never delete — rolling window.

    Returns counts: ``{"watches": N, "availability": N, "alerts": N, "bookings": N}``.
    """
    db = get_db()
    today_iso = datetime.now(timezone.utc).date().isoformat()

    query = {
        "$or": [
            {"date_mode": {"$in": [None, "single"]}, "target_date": {"$lt": today_iso}},
            {"date_mode": "range", "date_to": {"$ne": "", "$lt": today_iso}},
        ]
    }
    stale_ids = [str(w["_id"]) for w in db.watches.find(query, {"_id": 1})]
    if not stale_ids:
        return {"watches": 0, "availability": 0, "alerts": 0, "bookings": 0}

    avail   = db.availability.delete_many({"watch_id": {"$in": stale_ids}}).deleted_count
    alerts  = db.alert_log.delete_many   ({"watch_id": {"$in": stale_ids}}).deleted_count
    books   = db.bookings.delete_many    ({"watch_id": {"$in": stale_ids}}).deleted_count
    watches = db.watches.delete_many(
        {"_id": {"$in": [_to_object_id(i) for i in stale_ids]}}
    ).deleted_count

    logger.info(
        "Cleanup: deleted %d past watch(es), %d availability rows, %d alerts, %d booking logs.",
        watches, avail, alerts, books,
    )
    return {"watches": watches, "availability": avail, "alerts": alerts, "bookings": books}


def cleanup_old_alerts(days: int = 30) -> dict:
    """Prune alert_log and availability snapshots older than ``days`` days."""
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    alerts = db.alert_log.delete_many   ({"sent_at":    {"$lt": cutoff}}).deleted_count
    avail  = db.availability.delete_many({"checked_at": {"$lt": cutoff}}).deleted_count
    if alerts or avail:
        logger.info("Cleanup: pruned %d alerts and %d availability rows older than %dd.",
                    alerts, avail, days)
    return {"alerts": alerts, "availability": avail}


def get_bookings(watch_id: Optional[str] = None, chat_id: str = "", limit: int = 20) -> List[dict]:
    """Retrieve booking history, optionally filtered by watch or chat."""
    db = get_db()
    query: dict = {}
    if watch_id:
        query["watch_id"] = str(watch_id)

    # If chat_id given, join through watches to scope results
    if chat_id and not watch_id:
        watch_ids = [
            str(w["_id"])
            for w in db.watches.find({"chat_id": str(chat_id)}, {"_id": 1})
        ]
        query["watch_id"] = {"$in": watch_ids}

    docs = list(
        db.bookings.find(query, sort=[("booked_at", -1)], limit=limit)
    )
    return [_str_id(doc) for doc in docs]


# Initialize indexes on import
try:
    init_db()
except Exception as e:
    logger.warning("Could not initialize MongoDB indexes (DB may not be running yet): %s", e)
