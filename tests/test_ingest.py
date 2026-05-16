"""Test ingest._handle_result against fake watches in MongoDB."""

from tests._runner import run_test


def run():
    from ingest import _handle_result
    from database import get_db, add_restaurant, add_watch

    db = get_db()

    def _cleanup():
        db.restaurants.delete_many({"name": {"$regex": "^__test_ingest"}})
        db.watches.delete_many({"chat_id": "__test_ingest_chat"})
        db.availability.delete_many({"watch_id": {"$regex": "^__test_ingest"}})

    def t_no_match_returns_zero():
        _cleanup()
        r = _handle_result({
            "venue_id": 99999, "date": "2099-01-01",
            "party_size": 2, "slots": [{"time": "7pm"}],
        })
        assert r["matched"] == 0, f"expected 0 matches, got {r}"

    def t_match_single_watch():
        _cleanup()
        rid = add_restaurant("__test_ingest_a", "https://x", chat_id="__test_ingest_chat")
        # Inject venue_id directly
        from bson import ObjectId
        db.restaurants.update_one({"_id": ObjectId(rid)}, {"$set": {"venue_id": 88888}})
        add_watch(restaurant_id=rid, target_date="2099-12-31",
                  party_size=2, chat_id="__test_ingest_chat",
                  date_mode="single")
        r = _handle_result({
            "venue_id": 88888, "date": "2099-12-31",
            "party_size": 2, "slots": [{"time": "7:00 PM", "extra": ""}],
        })
        assert r["matched"] == 1, f"expected 1 match, got {r}"
        _cleanup()

    def t_match_range_watch():
        _cleanup()
        rid = add_restaurant("__test_ingest_b", "https://y", chat_id="__test_ingest_chat")
        from bson import ObjectId
        db.restaurants.update_one({"_id": ObjectId(rid)}, {"$set": {"venue_id": 77777}})
        add_watch(restaurant_id=rid, target_date="2099-01-01",
                  party_size=4, chat_id="__test_ingest_chat",
                  date_mode="range",
                  date_from="2099-01-01", date_to="2099-12-31")
        r = _handle_result({
            "venue_id": 77777, "date": "2099-06-15",
            "party_size": 4, "slots": [{"time": "8:00 PM"}],
        })
        assert r["matched"] == 1, f"expected 1 range match, got {r}"
        # Date outside range should not match
        r2 = _handle_result({
            "venue_id": 77777, "date": "2100-01-01",
            "party_size": 4, "slots": [{"time": "8:00 PM"}],
        })
        assert r2["matched"] == 0, f"expected 0 (date outside), got {r2}"
        _cleanup()

    def t_party_size_must_match():
        _cleanup()
        rid = add_restaurant("__test_ingest_c", "https://z", chat_id="__test_ingest_chat")
        from bson import ObjectId
        db.restaurants.update_one({"_id": ObjectId(rid)}, {"$set": {"venue_id": 66666}})
        add_watch(restaurant_id=rid, target_date="2099-06-01",
                  party_size=2, chat_id="__test_ingest_chat",
                  date_mode="single")
        r = _handle_result({
            "venue_id": 66666, "date": "2099-06-01",
            "party_size": 4,   # wrong party
            "slots": [{"time": "7pm"}],
        })
        assert r["matched"] == 0, f"party-mismatch should not match, got {r}"
        _cleanup()

    def t_missing_fields_graceful():
        r = _handle_result({"venue_id": None, "date": "2099-01-01", "party_size": 2})
        assert r["matched"] == 0 and "reason" in r

    results = [
        run_test("no match returns zero",   t_no_match_returns_zero),
        run_test("matches single watch",    t_match_single_watch),
        run_test("matches range watch",     t_match_range_watch),
        run_test("party size must match",   t_party_size_must_match),
        run_test("missing fields graceful", t_missing_fields_graceful),
    ]
    _cleanup()
    return results
