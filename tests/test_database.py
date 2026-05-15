"""Test database helpers: signatures, token storage, cleanup."""

from tests._runner import run_test


def run():
    from database import (
        _slot_signature,
        save_platform_token, load_platform_token,
        get_previous_slot_signature, log_alert_with_signature,
        get_db,
    )

    def t_slot_sig_order_independent():
        a = [{"time": "7:00 PM", "extra": "Indoor"},
             {"time": "8:30 PM", "extra": ""}]
        b = list(reversed(a))
        assert _slot_signature(a) == _slot_signature(b), "order should not matter"

    def t_slot_sig_differs_on_change():
        a = [{"time": "7:00 PM"}]
        b = [{"time": "7:00 PM"}, {"time": "8:30 PM"}]
        assert _slot_signature(a) != _slot_signature(b)

    def t_slot_sig_empty_is_stable():
        assert _slot_signature([]) == _slot_signature([])

    def t_token_roundtrip():
        save_platform_token("__test_platform", "abc123XYZ")
        assert load_platform_token("__test_platform") == "abc123XYZ"
        # Cleanup
        get_db().platform_tokens.delete_one({"platform": "__test_platform"})

    def t_token_overwrite():
        save_platform_token("__test_platform2", "first")
        save_platform_token("__test_platform2", "second")
        assert load_platform_token("__test_platform2") == "second"
        get_db().platform_tokens.delete_one({"platform": "__test_platform2"})

    def t_token_missing_returns_none():
        assert load_platform_token("__never_existed_xyz") is None

    def t_log_alert_signature():
        """log_alert_with_signature stores sig, get_previous returns it."""
        db = get_db()
        # Clean up first
        db.alert_log.delete_many({"watch_id": "__test_watch_999"})
        slots = [{"time": "7pm"}, {"time": "8pm"}]
        sig = log_alert_with_signature("__test_watch_999", "test", slots)
        assert get_previous_slot_signature("__test_watch_999") == sig
        # Cleanup
        db.alert_log.delete_many({"watch_id": "__test_watch_999"})

    return [
        run_test("slot sig order-independent",  t_slot_sig_order_independent),
        run_test("slot sig differs on change",  t_slot_sig_differs_on_change),
        run_test("slot sig empty stable",       t_slot_sig_empty_is_stable),
        run_test("token roundtrip",             t_token_roundtrip),
        run_test("token overwrite",             t_token_overwrite),
        run_test("token missing returns None",  t_token_missing_returns_none),
        run_test("alert signature stored",      t_log_alert_signature),
    ]
