"""Test scheduler helper logic: date rotation, past-date guard."""

from datetime import date, timedelta

from tests._runner import run_test


def run():
    import scheduler as sch

    today = date.today()
    iso = lambda n: (today + timedelta(days=n)).isoformat()

    def t_dates_for_watch_single():
        w = {"date_mode": "single", "target_date": iso(5)}
        assert sch._dates_for_watch(w) == [iso(5)]

    def t_dates_for_watch_range_clipped_to_today():
        """Range starting in the past should clip to today."""
        w = {"date_mode": "range",
             "date_from": iso(-10),
             "date_to":   iso(5),
             "target_date": iso(-10)}
        dates = sch._dates_for_watch(w)
        assert dates[0] == iso(0), f"first date should be today, got {dates[0]}"
        assert dates[-1] == iso(5)

    def t_dates_for_watch_any_mode():
        w = {"date_mode": "any", "target_date": "any"}
        dates = sch._dates_for_watch(w)
        # ANY_DATE_WINDOW_DAYS default 30 → 31 dates inclusive
        assert len(dates) == 31, f"expected 31 dates, got {len(dates)}"
        assert dates[0] == iso(0)

    def t_rotation_includes_today():
        """First slot of rotation window always = soonest date."""
        sch._range_cursor.clear()
        w = {"id": "rot_test_1", "date_mode": "any", "target_date": "any"}
        first = sch._dates_to_check_this_tick(w)
        assert first[0] == iso(0), "soonest date must be in every window"

    def t_rotation_advances():
        """Cursor advances across calls so different dates get sampled."""
        sch._range_cursor.clear()
        w = {"id": "rot_test_2", "date_mode": "any", "target_date": "any"}
        windows = [sch._dates_to_check_this_tick(w) for _ in range(4)]
        # Each window has 5 dates with the first always = today
        for w_ in windows:
            assert len(w_) == sch.RANGE_DATES_PER_TICK
            assert w_[0] == iso(0)
        # The tail dates should differ across calls
        tails = [tuple(w_[1:]) for w_ in windows]
        assert len(set(tails)) > 1, "rotation should produce different tails"

    def t_rotation_short_range_returns_all():
        """If range fits in one window, no rotation, return all."""
        sch._range_cursor.clear()
        w = {"id": "rot_test_3",
             "date_mode": "range",
             "date_from": iso(1),
             "date_to":   iso(3),
             "target_date": iso(1)}
        result = sch._dates_to_check_this_tick(w)
        assert len(result) == 3, f"3-day range should return all 3, got {len(result)}"

    def t_past_date_is_skipped():
        from database import deactivate_watch, get_watches, add_restaurant, add_watch, get_db
        # Create a throwaway past-date watch
        rid = add_restaurant("__test_past", "https://example.com/x",
                             platform="generic", chat_id="test")
        wid = add_watch(restaurant_id=rid, target_date=iso(-5),
                        party_size=2, chat_id="test",
                        date_mode="single")
        watches = [w for w in get_watches(active_only=True) if w["id"] == wid]
        if watches:
            result = sch.check_single_watch(watches[0])
            assert result["slots"] == [], "past-date watch should return empty"
            # Watch should now be deactivated
            db = get_db()
            doc = db.watches.find_one({"_id": __import__("bson").ObjectId(wid)})
            assert doc and doc.get("active") is False, "past-date watch should be deactivated"
            # Cleanup
            db.watches.delete_one({"_id": doc["_id"]})
            db.restaurants.delete_one({"_id": __import__("bson").ObjectId(rid)})

    return [
        run_test("dates_for_watch single",       t_dates_for_watch_single),
        run_test("dates_for_watch range clip",   t_dates_for_watch_range_clipped_to_today),
        run_test("dates_for_watch any mode",     t_dates_for_watch_any_mode),
        run_test("rotation includes today",      t_rotation_includes_today),
        run_test("rotation advances cursor",     t_rotation_advances),
        run_test("rotation short range = all",   t_rotation_short_range_returns_all),
        run_test("past-date watch deactivated",  t_past_date_is_skipped),
    ]
