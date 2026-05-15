"""
Restaurant Booking Tracker — Streamlit UI
Run with:  streamlit run app.py --server.headless true
"""

import streamlit as st
from datetime import date, datetime, timezone, timedelta
import logging
import time as _time

from logging_config import configure as _configure_logging
_configure_logging()

from database import (
    add_restaurant,
    get_restaurants,
    delete_restaurant,
    toggle_restaurant,
    add_watch,
    get_watches,
    deactivate_watch,
    delete_watch,
    get_latest_availability,
    check_connection,
)
from scraper import detect_platform, check_availability
from scheduler import (
    start_scheduler,
    stop_scheduler,
    is_running,
    check_single_watch,
    check_all_watches,
)
from alerts import send_test_message
from config import CHECK_INTERVAL_MINUTES

# ═══════════════════════════════════════════════════════════════════════
# Page Config
# ═══════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="TableWatch — Restaurant Booking Tracker",
    page_icon="https://em-content.zobj.net/source/apple/391/fork-and-knife-with-plate_1f37d-fe0f.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════
# Theme & Styles
# ═══════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
    /* ── Global ─────────────────────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 1200px;
    }

    /* ── Hide Streamlit defaults ───────────────────────────────── */
    #MainMenu, footer, header { visibility: hidden; }
    .stDeployButton { display: none; }

    /* ── Sidebar ───────────────────────────────────────────────── */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
        color: #e2e8f0;
        min-width: 280px;
    }
    section[data-testid="stSidebar"] .stMarkdown p,
    section[data-testid="stSidebar"] .stMarkdown li,
    section[data-testid="stSidebar"] .stMarkdown span,
    section[data-testid="stSidebar"] label {
        color: #cbd5e1 !important;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        color: #f8fafc !important;
    }
    section[data-testid="stSidebar"] hr {
        border-color: rgba(148,163,184,0.2);
    }

    /* ── Card component ────────────────────────────────────────── */
    .tw-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 20px 24px;
        margin-bottom: 12px;
        transition: box-shadow 0.2s ease, border-color 0.2s ease;
    }
    .tw-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.06);
        border-color: #cbd5e1;
    }
    .tw-card-alert {
        border-left: 4px solid #22c55e;
    }
    .tw-card-warn {
        border-left: 4px solid #f59e0b;
    }
    .tw-card-muted {
        border-left: 4px solid #94a3b8;
        opacity: 0.7;
    }

    /* ── Stat cards ────────────────────────────────────────────── */
    .tw-stat {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 20px 24px;
        text-align: center;
    }
    .tw-stat-value {
        font-size: 2rem;
        font-weight: 700;
        color: #0f172a;
        line-height: 1.2;
    }
    .tw-stat-label {
        font-size: 0.8rem;
        font-weight: 500;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 4px;
    }

    /* ── Status badges ─────────────────────────────────────────── */
    .tw-badge {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 3px 12px;
        border-radius: 20px;
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.02em;
    }
    .tw-badge-green  { background: #dcfce7; color: #166534; }
    .tw-badge-amber  { background: #fef3c7; color: #92400e; }
    .tw-badge-red    { background: #fee2e2; color: #991b1b; }
    .tw-badge-slate  { background: #f1f5f9; color: #475569; }
    .tw-badge-blue   { background: #dbeafe; color: #1e40af; }
    .tw-badge-violet { background: #ede9fe; color: #5b21b6; }

    /* ── Platform chips ────────────────────────────────────────── */
    .tw-platform {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 2px 10px;
        border-radius: 6px;
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .tw-platform-resy      { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
    .tw-platform-opentable  { background: #fdf2f8; color: #be185d; border: 1px solid #fbcfe8; }
    .tw-platform-yelp       { background: #fef9ee; color: #b45309; border: 1px solid #fed7aa; }
    .tw-platform-generic    { background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }

    /* ── Section headers ───────────────────────────────────────── */
    .tw-section-header {
        font-size: 1.15rem;
        font-weight: 700;
        color: #0f172a;
        margin-bottom: 16px;
        padding-bottom: 8px;
        border-bottom: 2px solid #e2e8f0;
    }

    /* ── Empty states ──────────────────────────────────────────── */
    .tw-empty {
        text-align: center;
        padding: 48px 24px;
        color: #94a3b8;
    }
    .tw-empty-icon {
        font-size: 2.5rem;
        margin-bottom: 12px;
    }
    .tw-empty-text {
        font-size: 0.95rem;
        line-height: 1.6;
    }

    /* ── Slot pill ─────────────────────────────────────────────── */
    .tw-slot {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 12px;
        margin: 3px;
        border-radius: 8px;
        font-size: 0.82rem;
        font-weight: 500;
        background: #f0fdf4;
        color: #166534;
        border: 1px solid #bbf7d0;
    }

    /* ── Override Streamlit tab styling ─────────────────────────── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        background: #f8fafc;
        border-radius: 10px;
        padding: 4px;
        border: 1px solid #e2e8f0;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 20px;
        font-weight: 500;
        font-size: 0.88rem;
        color: #64748b;
    }
    .stTabs [aria-selected="true"] {
        background: #ffffff !important;
        color: #0f172a !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        font-weight: 600;
    }
    .stTabs [data-baseweb="tab-highlight"] {
        display: none;
    }
    .stTabs [data-baseweb="tab-border"] {
        display: none;
    }

    /* ── Override metric cards ──────────────────────────────────── */
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: none;
    }
    div[data-testid="stMetric"] label {
        color: #64748b !important;
        font-size: 0.78rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        font-size: 1.8rem !important;
        font-weight: 700 !important;
        color: #0f172a !important;
    }

    /* ── Form overrides ────────────────────────────────────────── */
    .stForm {
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 24px !important;
        background: #fafbfc !important;
    }

    /* ── Button overrides ──────────────────────────────────────── */
    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
        font-size: 0.85rem;
        padding: 6px 16px;
        transition: all 0.15s ease;
    }
    .stButton > button[kind="primary"] {
        background: #0f172a;
        color: #ffffff;
    }
    .stButton > button[kind="primary"]:hover {
        background: #1e293b;
    }

    /* ── Divider override ──────────────────────────────────────── */
    hr {
        border: none;
        border-top: 1px solid #f1f5f9;
        margin: 8px 0;
    }

    /* ── Sidebar status indicator ──────────────────────────────── */
    .tw-sidebar-status {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 14px;
        border-radius: 10px;
        font-size: 0.82rem;
        font-weight: 500;
        margin-bottom: 8px;
    }
    .tw-sidebar-status-on {
        background: rgba(34,197,94,0.12);
        color: #86efac;
    }
    .tw-sidebar-status-off {
        background: rgba(251,191,36,0.12);
        color: #fcd34d;
    }
    .tw-status-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        display: inline-block;
    }
    .tw-dot-green {
        background: #22c55e;
        box-shadow: 0 0 6px rgba(34,197,94,0.5);
    }
    .tw-dot-amber {
        background: #f59e0b;
        box-shadow: 0 0 6px rgba(245,158,11,0.5);
    }

    /* ── Watch row ─────────────────────────────────────────────── */
    .tw-watch-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 12px;
    }
    .tw-watch-info {
        flex: 1;
        min-width: 200px;
    }
    .tw-watch-name {
        font-weight: 600;
        font-size: 0.95rem;
        color: #0f172a;
    }
    .tw-watch-meta {
        font-size: 0.82rem;
        color: #64748b;
        margin-top: 2px;
    }
    .tw-watch-actions {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    /* ── Timestamp formatting ──────────────────────────────────── */
    .tw-timestamp {
        font-size: 0.72rem;
        color: #94a3b8;
        font-weight: 400;
    }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def platform_chip(platform: str) -> str:
    """Return styled HTML chip for a platform name."""
    p = platform.lower()
    cls = f"tw-platform-{p}" if p in ("resy", "opentable", "yelp") else "tw-platform-generic"
    icons = {"resy": "R", "opentable": "OT", "yelp": "Y", "generic": "G"}
    return f'<span class="tw-platform {cls}">{icons.get(p, "G")} {platform}</span>'


def status_badge(label: str, variant: str = "slate") -> str:
    return f'<span class="tw-badge tw-badge-{variant}">{label}</span>'


def format_checked_time(checked) -> str:
    if isinstance(checked, datetime):
        now = datetime.now(timezone.utc)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        diff = now - checked
        if diff < timedelta(minutes=1):
            return "just now"
        if diff < timedelta(hours=1):
            return f"{int(diff.total_seconds() // 60)}m ago"
        if diff < timedelta(hours=24):
            return f"{int(diff.total_seconds() // 3600)}h ago"
        return checked.strftime("%b %d, %H:%M")
    if checked:
        return str(checked)
    return ""


# ═══════════════════════════════════════════════════════════════════════
# MongoDB connection gate
# ═══════════════════════════════════════════════════════════════════════

if not check_connection():
    st.markdown("""
    <div style="text-align:center; padding: 80px 20px;">
        <div style="font-size: 3rem; margin-bottom: 16px;">🔌</div>
        <h2 style="color: #0f172a; margin-bottom: 8px;">Cannot connect to MongoDB</h2>
        <p style="color: #64748b; max-width: 480px; margin: 0 auto; line-height: 1.6;">
            Make sure MongoDB is running and <code>MONGO_URI</code> is set in your
            <code>.env</code> file.<br>
            Default: <code>mongodb://localhost:27017</code>
        </p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ═══════════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="padding: 8px 0 4px 0;">
        <span style="font-size: 1.6rem; font-weight: 700; color: #f8fafc; letter-spacing: -0.02em;">
            🍽️ TableWatch
        </span>
        <div style="font-size: 0.78rem; color: #94a3b8; margin-top: 2px;">
            Restaurant Booking Tracker
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Scheduler status ──────────────────────────────────────────
    if is_running():
        st.markdown("""
        <div class="tw-sidebar-status tw-sidebar-status-on">
            <span class="tw-status-dot tw-dot-green"></span>
            Scheduler active &middot; every {interval}m
        </div>
        """.format(interval=CHECK_INTERVAL_MINUTES), unsafe_allow_html=True)
        if st.button("⏸  Pause Scheduler", use_container_width=True):
            stop_scheduler()
            st.rerun()
    else:
        st.markdown("""
        <div class="tw-sidebar-status tw-sidebar-status-off">
            <span class="tw-status-dot tw-dot-amber"></span>
            Scheduler paused
        </div>
        """, unsafe_allow_html=True)
        if st.button("▶  Start Scheduler", use_container_width=True):
            start_scheduler()
            st.rerun()

    st.divider()

    # ── Manual check all ──────────────────────────────────────────
    if st.button("🔄  Check All Now", use_container_width=True):
        with st.spinner("Running checks…"):
            check_all_watches()
        st.success("Done")
        _time.sleep(1)
        st.rerun()

    st.divider()

    # ── Telegram ──────────────────────────────────────────────────
    st.markdown('<p style="font-size:0.78rem; font-weight:600; color:#94a3b8; text-transform:uppercase; letter-spacing:0.05em;">Notifications</p>', unsafe_allow_html=True)
    if st.button("📱  Send Test Alert", use_container_width=True):
        ok = send_test_message()
        if ok:
            st.success("Sent!")
        else:
            st.error("Failed — check .env")

    st.divider()

    st.markdown("""
    <div style="font-size: 0.7rem; color: #475569; line-height: 1.6; padding: 8px 0;">
        <strong style="color: #94a3b8;">Ethical scraping</strong><br>
        Respects robots.txt · Rate-limited · Identified User-Agent
    </div>
    """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════
# Tabs
# ═══════════════════════════════════════════════════════════════════════

tab_dash, tab_restaurants, tab_watches, tab_check, tab_metrics = st.tabs(
    ["Dashboard", "Restaurants", "Watches", "Quick Check", "Metrics"]
)


# ═══════════════════════════════════════════════════════════════════════
# TAB: Dashboard
# ═══════════════════════════════════════════════════════════════════════

with tab_dash:
    restaurants_all = get_restaurants(active_only=False)
    watches_all = get_watches(active_only=False)
    active_watches = [w for w in watches_all if w.get("active")]

    # ── Summary metrics ───────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Restaurants", len(restaurants_all))
    c2.metric("Active Watches", len(active_watches))
    c3.metric("Total Checks", len(watches_all))
    sched_label = f"Every {CHECK_INTERVAL_MINUTES}m" if is_running() else "Paused"
    c4.metric("Scheduler", sched_label)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Active watches feed ───────────────────────────────────────
    st.markdown('<div class="tw-section-header">Live Availability Feed</div>', unsafe_allow_html=True)

    if not active_watches:
        st.markdown("""
        <div class="tw-empty">
            <div class="tw-empty-icon">👀</div>
            <div class="tw-empty-text">
                No active watches yet.<br>
                Add a restaurant and create a watch to start monitoring.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        for w in active_watches:
            latest = get_latest_availability(w["id"])
            has_slots = latest and latest.get("slots_found")
            slot_count = len(latest["slots_found"]) if has_slots else 0

            # Card variant
            if has_slots:
                card_cls = "tw-card tw-card-alert"
            elif latest:
                card_cls = "tw-card tw-card-warn"
            else:
                card_cls = "tw-card"

            # Status badge
            if has_slots:
                badge = status_badge(f"✓ {slot_count} slot{'s' if slot_count != 1 else ''}", "green")
            elif latest:
                badge = status_badge("No availability", "amber")
            else:
                badge = status_badge("Pending", "blue")

            # Timestamp
            checked_str = ""
            if latest:
                checked_str = format_checked_time(latest.get("checked_at", ""))

            plat = platform_chip(w.get("restaurant_platform", "generic"))
            timestamp_html = f'<span class="tw-timestamp">Checked {checked_str}</span>' if checked_str else '<span class="tw-timestamp">Not checked yet</span>'

            st.markdown(f"""
            <div class="{card_cls}">
                <div class="tw-watch-row">
                    <div class="tw-watch-info">
                        <div class="tw-watch-name">{w['restaurant_name']}</div>
                        <div class="tw-watch-meta">
                            📅 {w['target_date']} &nbsp;·&nbsp; 👥 {w['party_size']} &nbsp;·&nbsp; 🕐 {w.get('time_preference', 'any')} &nbsp;·&nbsp; {plat}
                        </div>
                    </div>
                    <div class="tw-watch-actions">
                        {badge}
                        &nbsp;
                        {timestamp_html}
                    </div>
                </div>
            """, unsafe_allow_html=True)

            # Show slot pills if available
            if has_slots:
                slot_pills = "".join(
                    f'<span class="tw-slot">🕐 {s.get("time", "?")}' +
                    (f' — {s.get("extra", "")}' if s.get("extra") else '') +
                    '</span>'
                    for s in latest["slots_found"][:8]
                )
                more_html = f'<span class="tw-slot" style="background:#f1f5f9;color:#64748b;border-color:#e2e8f0;">+{slot_count - 8} more</span>' if slot_count > 8 else ""
                st.markdown(f'<div style="margin-top:10px;">{slot_pills}{more_html}</div>', unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

            # Action buttons row
            bcol1, bcol2, bcol3 = st.columns([1, 1, 8])
            with bcol1:
                if st.button("🔄 Check", key=f"dash_chk_{w['id']}"):
                    with st.spinner("Checking…"):
                        result = check_single_watch(w)
                    st.rerun()
            with bcol2:
                if st.button("⏸ Pause", key=f"dash_pause_{w['id']}"):
                    deactivate_watch(w["id"])
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════════
# TAB: Restaurants
# ═══════════════════════════════════════════════════════════════════════

with tab_restaurants:
    st.markdown('<div class="tw-section-header">Manage Restaurants</div>', unsafe_allow_html=True)

    # ── Add form ──────────────────────────────────────────────────
    with st.expander("➕  Add New Restaurant", expanded=False):
        with st.form("add_restaurant", clear_on_submit=True):
            col_a, col_b = st.columns(2)
            with col_a:
                r_name = st.text_input(
                    "Restaurant Name",
                    placeholder="e.g. Bungalow, Semma, Don Angie",
                )
            with col_b:
                r_platform = st.selectbox(
                    "Platform",
                    ["auto-detect", "resy", "opentable", "yelp", "generic"],
                    help="'auto-detect' infers from the URL",
                )

            r_url = st.text_input(
                "Booking URL",
                placeholder="https://resy.com/cities/ny/restaurant-name",
            )
            r_notes = st.text_input("Notes (optional)", placeholder="Outdoor seating preferred, etc.")

            submitted = st.form_submit_button("Add Restaurant", use_container_width=True)
            if submitted:
                if not r_name or not r_url:
                    st.error("Name and URL are required.")
                else:
                    platform = detect_platform(r_url) if r_platform == "auto-detect" else r_platform
                    add_restaurant(r_name, r_url, platform, r_notes)
                    st.success(f"Added **{r_name}**")
                    _time.sleep(0.5)
                    st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Restaurant list ───────────────────────────────────────────
    restaurants = get_restaurants(active_only=False)

    if not restaurants:
        st.markdown("""
        <div class="tw-empty">
            <div class="tw-empty-icon">🏪</div>
            <div class="tw-empty-text">No restaurants added yet. Use the form above to get started.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        for r in restaurants:
            is_active = r.get("active", True)
            card_cls = "tw-card" if is_active else "tw-card tw-card-muted"
            badge = status_badge("Active", "green") if is_active else status_badge("Paused", "slate")
            plat = platform_chip(r.get("platform", "generic"))
            notes_html = f'<div style="font-size:0.78rem;color:#94a3b8;margin-top:4px;">📝 {r.get("notes", "")}</div>' if r.get("notes") else ""

            st.markdown(f"""
            <div class="{card_cls}">
                <div class="tw-watch-row">
                    <div class="tw-watch-info">
                        <div class="tw-watch-name">{r['name']}</div>
                        <div class="tw-watch-meta">
                            {plat} &nbsp;·&nbsp;
                            <a href="{r.get('url', '#')}" target="_blank" style="color:#3b82f6;text-decoration:none;font-size:0.82rem;">
                                {r.get('url', '')[:60]}{'…' if len(r.get('url', '')) > 60 else ''}
                            </a>
                        </div>
                        {notes_html}
                    </div>
                    <div class="tw-watch-actions">
                        {badge}
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            bcol1, bcol2, bcol3 = st.columns([1, 1, 8])
            with bcol1:
                if is_active:
                    if st.button("⏸ Pause", key=f"rest_pause_{r['id']}"):
                        toggle_restaurant(r["id"], False)
                        st.rerun()
                else:
                    if st.button("▶ Resume", key=f"rest_resume_{r['id']}"):
                        toggle_restaurant(r["id"], True)
                        st.rerun()
            with bcol2:
                if st.button("🗑 Remove", key=f"rest_del_{r['id']}"):
                    delete_restaurant(r["id"])
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════════
# TAB: Watches
# ═══════════════════════════════════════════════════════════════════════

with tab_watches:
    st.markdown('<div class="tw-section-header">Availability Watches</div>', unsafe_allow_html=True)

    restaurants_active = get_restaurants(active_only=True)

    if not restaurants_active:
        st.info("Add an active restaurant first before creating watches.")
    else:
        with st.expander("➕  Add New Watch", expanded=False):
            with st.form("add_watch", clear_on_submit=True):
                r_options = {r["name"]: r["id"] for r in restaurants_active}

                col_a, col_b = st.columns(2)
                with col_a:
                    selected_name = st.selectbox("Restaurant", list(r_options.keys()))
                    w_date = st.date_input("Target Date", min_value=date.today())
                with col_b:
                    w_party = st.number_input("Party Size", min_value=1, max_value=20, value=2)
                    w_time_pref = st.selectbox(
                        "Time Preference",
                        ["any", "lunch (11:00–14:00)", "dinner (17:00–21:00)", "late (21:00+)"],
                    )

                submitted = st.form_submit_button("Create Watch", use_container_width=True)
                if submitted:
                    add_watch(
                        restaurant_id=r_options[selected_name],
                        target_date=str(w_date),
                        party_size=w_party,
                        time_preference=w_time_pref,
                    )
                    st.success(f"Watching **{selected_name}** on {w_date}")
                    _time.sleep(0.5)
                    st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Watch list ────────────────────────────────────────────────
    watches = get_watches(active_only=False)

    if not watches:
        st.markdown("""
        <div class="tw-empty">
            <div class="tw-empty-icon">👁</div>
            <div class="tw-empty-text">No watches created yet.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        for w in watches:
            is_active = w.get("active")
            latest = get_latest_availability(w["id"])
            has_slots = latest and latest.get("slots_found")
            slot_count = len(latest["slots_found"]) if has_slots else 0

            if not is_active:
                card_cls = "tw-card tw-card-muted"
                badge = status_badge("Paused", "slate")
            elif has_slots:
                card_cls = "tw-card tw-card-alert"
                badge = status_badge(f"✓ {slot_count} slot{'s' if slot_count != 1 else ''}", "green")
            elif latest:
                card_cls = "tw-card tw-card-warn"
                badge = status_badge("No slots", "amber")
            else:
                card_cls = "tw-card"
                badge = status_badge("Pending", "blue")

            plat = platform_chip(w.get("restaurant_platform", "generic"))
            checked_str = format_checked_time(latest.get("checked_at", "")) if latest else ""
            timestamp_html = f'<span class="tw-timestamp">Checked {checked_str}</span>' if checked_str else '<span class="tw-timestamp">—</span>'

            st.markdown(f"""
            <div class="{card_cls}">
                <div class="tw-watch-row">
                    <div class="tw-watch-info">
                        <div class="tw-watch-name">{w['restaurant_name']}</div>
                        <div class="tw-watch-meta">
                            📅 {w['target_date']} &nbsp;·&nbsp; 👥 {w['party_size']} &nbsp;·&nbsp; 🕐 {w.get('time_preference', 'any')} &nbsp;·&nbsp; {plat}
                        </div>
                    </div>
                    <div class="tw-watch-actions">
                        {badge}
                        &nbsp;
                        {timestamp_html}
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            bcol1, bcol2, bcol3, bcol4 = st.columns([1, 1, 1, 7])
            if is_active:
                with bcol1:
                    if st.button("🔄 Check", key=f"w_chk_{w['id']}"):
                        with st.spinner("Checking…"):
                            result = check_single_watch(w)
                        if result["slots"]:
                            st.toast(f"Found {len(result['slots'])} slot(s)!", icon="✅")
                        else:
                            st.toast("No slots found", icon="⚠️")
                        _time.sleep(1)
                        st.rerun()
                with bcol2:
                    if st.button("⏸ Pause", key=f"w_pause_{w['id']}"):
                        deactivate_watch(w["id"])
                        st.rerun()
            with bcol3:
                if st.button("🗑 Delete", key=f"w_del_{w['id']}"):
                    delete_watch(w["id"])
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════════
# TAB: Quick Check
# ═══════════════════════════════════════════════════════════════════════

with tab_check:
    st.markdown('<div class="tw-section-header">Quick Availability Check</div>', unsafe_allow_html=True)
    st.caption("One-off check without saving — useful for testing URLs before adding.")

    with st.form("quick_check"):
        qc_url = st.text_input(
            "Restaurant Booking URL",
            placeholder="https://resy.com/cities/ny/semma?date=2026-03-15&seats=2",
        )

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            qc_date = st.date_input("Date", min_value=date.today())
        with col_b:
            qc_party = st.number_input("Party Size", min_value=1, max_value=20, value=2)
        with col_c:
            qc_platform = st.selectbox(
                "Platform",
                ["auto-detect", "resy", "opentable", "yelp", "generic"],
            )

        run_check = st.form_submit_button("🔍  Check Availability", use_container_width=True)

    if run_check:
        if not qc_url:
            st.error("URL is required.")
        else:
            platform = "auto" if qc_platform == "auto-detect" else qc_platform
            with st.spinner("Fetching & parsing…"):
                result = check_availability(
                    url=qc_url,
                    target_date=str(qc_date),
                    party_size=qc_party,
                    platform=platform,
                )

            st.markdown("<br>", unsafe_allow_html=True)

            if not result["success"]:
                st.markdown(f"""
                <div class="tw-card tw-card-warn">
                    <div class="tw-watch-name">⚠️  Fetch Failed</div>
                    <div class="tw-watch-meta" style="margin-top:6px;">{result['message']}</div>
                </div>
                """, unsafe_allow_html=True)

            elif result["slots"]:
                slot_count = len(result["slots"])
                st.markdown(f"""
                <div class="tw-card tw-card-alert">
                    <div class="tw-watch-name">✅  Found {slot_count} available slot{'s' if slot_count != 1 else ''}</div>
                    <div style="margin-top: 10px;">
                """, unsafe_allow_html=True)

                slot_pills = "".join(
                    f'<span class="tw-slot">🕐 {s.get("time", "?")}' +
                    (f' — {s.get("extra", "")}' if s.get("extra") else '') +
                    '</span>'
                    for s in result["slots"][:12]
                )
                more_html = f'<span class="tw-slot" style="background:#f1f5f9;color:#64748b;border-color:#e2e8f0;">+{slot_count - 12} more</span>' if slot_count > 12 else ""
                st.markdown(f'{slot_pills}{more_html}</div></div>', unsafe_allow_html=True)

            else:
                st.markdown(f"""
                <div class="tw-card">
                    <div class="tw-watch-name">No slots found</div>
                    <div class="tw-watch-meta" style="margin-top:6px;">
                        The page may be JS-rendered (common for Resy/OpenTable), no availability exists,
                        or the parser needs tuning for this site.
                    </div>
                </div>
                """, unsafe_allow_html=True)

            detected = detect_platform(qc_url)
            st.markdown(f"""
            <div style="margin-top:8px;">
                <span class="tw-timestamp">
                    Detected platform: <strong>{detected}</strong> &nbsp;·&nbsp;
                    HTML hash: {result['html_hash'][:12]}…
                </span>
            </div>
            """, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════
# TAB: Metrics
# ═══════════════════════════════════════════════════════════════════════

with tab_metrics:
    st.subheader('📊 Metrics & Test Results')
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from database import get_db as _gdb
    _mdb = _gdb().metrics

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        hours = st.number_input('Window (hours)', min_value=1, max_value=168, value=24)
    with col2:
        evt_filter = st.text_input('Event type filter (blank = all)', '')
    with col3:
        st.caption('Polls every page render. Use sidebar refresh to update.')

    _since = _dt.now(_tz.utc) - _td(hours=int(hours))
    _q = {'ts': {'$gte': _since}}
    if evt_filter.strip():
        _q['event_type'] = evt_filter.strip()

    # Summary counts
    _agg = list(_mdb.aggregate([
        {'$match': _q},
        {'$group': {'_id': '$event_type', 'count': {'$sum': 1}}},
        {'$sort': {'count': -1}},
    ]))
    if _agg:
        st.markdown('### Event counts')
        import pandas as _pd
        _df_counts = _pd.DataFrame([{'event': a['_id'], 'count': a['count']} for a in _agg])
        st.dataframe(_df_counts, use_container_width=True, hide_index=True)
    else:
        st.info('No events in window.')

    # Latency summary for fetch_done
    _fetch_docs = list(_mdb.find({'event_type': 'fetch_done', 'ts': {'$gte': _since}}, {'duration_ms': 1, 'slots': 1}))
    if _fetch_docs:
        import statistics as _stat
        _durs = [d['duration_ms'] for d in _fetch_docs if 'duration_ms' in d]
        if _durs:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('Fetches', len(_durs))
            c2.metric('p50 ms', f"{_stat.median(_durs):.0f}")
            c3.metric('p95 ms', f"{sorted(_durs)[int(len(_durs)*0.95)-1]:.0f}" if len(_durs) >= 20 else 'n/a')
            c4.metric('max ms', f"{max(_durs):.0f}")

    # Alerts
    _alerts_sent = _mdb.count_documents({'event_type': 'alert_sent', 'ts': {'$gte': _since}})
    _alerts_failed = _mdb.count_documents({'event_type': 'alert_failed', 'ts': {'$gte': _since}})
    _bursts_diff = _mdb.count_documents({'event_type': 'burst_diff_on', 'ts': {'$gte': _since}})
    _bursts_rel = _mdb.count_documents({'event_type': 'burst_release_on', 'ts': {'$gte': _since}})
    _deltas = _mdb.count_documents({'event_type': 'slot_delta', 'ts': {'$gte': _since}})
    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric('Alerts sent', _alerts_sent)
    a2.metric('Alerts failed', _alerts_failed)
    a3.metric('Slot deltas', _deltas)
    a4.metric('Diff bursts', _bursts_diff)
    a5.metric('Release bursts', _bursts_rel)

    st.markdown('### Recent events')
    _limit = st.slider('Rows', 20, 500, 100)
    _recent = list(_mdb.find(_q, sort=[('ts', -1)], limit=_limit))
    if _recent:
        import pandas as _pd
        for d in _recent:
            d.pop('_id', None)
            if 'ts' in d:
                d['ts'] = d['ts'].strftime('%H:%M:%S')
        st.dataframe(_pd.DataFrame(_recent), use_container_width=True, hide_index=True)
    else:
        st.info('No events matching filter.')

