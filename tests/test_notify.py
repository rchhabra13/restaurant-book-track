"""Test notify message templates + quiet hours + escaping."""

from tests._runner import run_test


def run():
    from notify import (
        _tg_alert, _tg_range_alert, _wa_alert, _wa_range_alert,
        _tg_esc, _tg_esc_attr, _wa_esc, _in_quiet_hours,
        _MAX_DATES_SHOWN,
    )

    def t_tg_escapes_tags():
        s = _tg_esc("<script>alert(1)</script>")
        assert "<" not in s.replace("&lt;", ""), "tags should be escaped"
        assert "&lt;script&gt;" in s

    def t_tg_keeps_apostrophe():
        # body text shouldn't escape apostrophes (Telegram allows them)
        assert _tg_esc("L'Artusi") == "L'Artusi"

    def t_tg_attr_escapes_quotes():
        # href attribute MUST escape quotes
        assert "&quot;" in _tg_esc_attr('"hello"'), "attribute must escape quotes"

    def t_alert_no_injection():
        w = {
            "restaurant_name": "<b>Pwned</b>",
            "target_date": "2026-05-20",
            "party_size": 2,
            "restaurant_url": 'https://example.com?x="evil"',
            "date_mode": "single",
        }
        slots = [{"time": "<img>", "extra": "alert(1)"}]
        msg = _tg_alert(w, slots)
        # Raw injection text must NOT appear
        assert "<b>Pwned</b>" not in msg
        assert "<img>" not in msg
        # but escaped versions are fine
        assert "&lt;b&gt;Pwned&lt;/b&gt;" in msg

    def t_range_collapses_repeating():
        """80 dates with identical slot times should collapse into single summary."""
        from datetime import date, timedelta
        w = {"restaurant_name": "Carbone", "party_size": 2, "restaurant_url": ""}
        dates = {}
        d = date(2026, 5, 20)
        for i in range(80):
            dates[(d + timedelta(days=i)).isoformat()] = [{"time": "11:30 PM", "extra": "Dinner"}]
        msg = _tg_range_alert(w, dates)
        # Should NOT contain 80 individual date lines
        date_lines = [l for l in msg.split("\n") if l.startswith("📅")]
        assert len(date_lines) <= 1, f"repeating range should collapse, got {len(date_lines)} date lines"
        assert "80 dates" in msg
        assert "Same availability" in msg

    def t_range_mixed_caps_at_max():
        """Mixed slots → shows at most _MAX_DATES_SHOWN dates + 'more' summary."""
        w = {"restaurant_name": "Test", "party_size": 2, "restaurant_url": ""}
        dates = {f"2026-06-{i+1:02d}": [{"time": f"{i+5}:00 PM"}] for i in range(10)}
        msg = _tg_range_alert(w, dates)
        date_lines = [l for l in msg.split("\n") if l.startswith("📅")]
        assert len(date_lines) == _MAX_DATES_SHOWN, \
            f"expected {_MAX_DATES_SHOWN} date lines, got {len(date_lines)}"
        assert f"and {10 - _MAX_DATES_SHOWN} more dates" in msg

    def t_quiet_hours_disabled_by_default():
        # Both 0 = disabled
        assert _in_quiet_hours() is False

    def t_quiet_hours_logic():
        # Patch config temporarily for this test
        import notify
        original = (notify.QUIET_HOURS_START, notify.QUIET_HOURS_END)
        try:
            from datetime import datetime
            try:
                from zoneinfo import ZoneInfo
                hour = datetime.now(ZoneInfo(notify.QUIET_HOURS_TZ)).hour
            except Exception:
                hour = datetime.now().hour

            # Window that wraps the current hour
            notify.QUIET_HOURS_START = hour
            notify.QUIET_HOURS_END   = (hour + 1) % 24
            assert notify._in_quiet_hours() is True, "current hour should be quiet"

            # Window that does NOT cover current hour
            notify.QUIET_HOURS_START = (hour + 2) % 24
            notify.QUIET_HOURS_END   = (hour + 3) % 24
            assert notify._in_quiet_hours() is False, "future hour should not be quiet"
        finally:
            notify.QUIET_HOURS_START, notify.QUIET_HOURS_END = original

    def t_wa_alert_no_html():
        """WhatsApp version must not contain HTML tags."""
        w = {"restaurant_name": "<b>name</b>", "party_size": 2,
             "restaurant_url": "", "date_mode": "single", "target_date": "2026-05-20"}
        msg = _wa_alert(w, [{"time": "7pm"}])
        assert "<b>" not in msg
        assert "<" not in msg or msg.count("<") == 0

    return [
        run_test("tg: escapes < and >",         t_tg_escapes_tags),
        run_test("tg: keeps apostrophe",        t_tg_keeps_apostrophe),
        run_test("tg: attr escapes quotes",     t_tg_attr_escapes_quotes),
        run_test("tg: no HTML injection",       t_alert_no_injection),
        run_test("tg: range collapses repeat",  t_range_collapses_repeating),
        run_test("tg: range caps mixed dates",  t_range_mixed_caps_at_max),
        run_test("quiet hours disabled default", t_quiet_hours_disabled_by_default),
        run_test("quiet hours logic",            t_quiet_hours_logic),
        run_test("wa: no HTML in output",        t_wa_alert_no_html),
    ]
