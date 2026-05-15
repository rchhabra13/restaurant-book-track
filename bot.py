"""
TableWatch Telegram Bot — Restaurant Booking Tracker
═════════════════════════════════════════════════════

A deployable Telegram bot that lets users manage restaurant watches
directly from chat. Integrates with the existing scraper, database,
and scheduler modules.

Run:  python bot.py

Commands
────────
/start              – Welcome message + command overview
/help               – Full command reference
/add <url> [name]   – Add a restaurant by booking URL
/list               – List your tracked restaurants
/remove <name|#>    – Remove a restaurant
/watch <name> <date> [party] [time_pref] – Create an availability watch
/watches            – List your active watches
/unwatch <#>        – Remove a watch by number
/check <name|#>     – One-off availability check for a restaurant
/checkall           – Check all your active watches now
/autobook <#N> on|off – Toggle auto-booking for a watch
/bookings           – Show recent auto-booking history
/setrelease <name> <days> <HH:MM> – Set reservation release schedule
/joinwaitlist <#N>  – Auto-join platform's notify/waitlist for a watch
/learnrelease       – Run release pattern analysis now
/status             – Dashboard summary
/pause <name|#>     – Pause a restaurant
/resume <name|#>    – Resume a paused restaurant
"""

import asyncio
import logging
import re
from datetime import date, datetime, timezone

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

from config import TELEGRAM_BOT_TOKEN, CHECK_INTERVAL_MINUTES, AUTO_BOOK_ENABLED, BURST_CHECK_INTERVAL_SECONDS
from database import (
    add_restaurant,
    get_restaurants,
    get_restaurant_by_id,
    find_restaurant_by_name,
    delete_restaurant,
    toggle_restaurant,
    add_watch,
    get_watches,
    deactivate_watch,
    delete_watch,
    get_latest_availability,
    set_watch_auto_book,
    get_bookings,
    mark_waitlist_joined,
    update_restaurant_release,
    check_connection,
)
from scraper import detect_platform, check_availability
from scheduler import start_scheduler, check_single_watch, get_burst_watch_ids

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

import html as _html_module

def esc(text: str) -> str:
    """Escape text for Telegram HTML parse mode body content."""
    return _html_module.escape(str(text), quote=False)

def esc_attr(text: str) -> str:
    """Escape for Telegram HTML attribute values (e.g. href)."""
    return _html_module.escape(str(text), quote=True)


def _chat_id(update: Update) -> str:
    return str(update.effective_chat.id)


def _resolve_restaurant(text: str, chat_id: str) -> dict | None:
    """Resolve a restaurant by #index (from /list) or by name."""
    text = text.strip()

    # Try #N index
    if text.startswith("#"):
        try:
            idx = int(text[1:]) - 1
            restaurants = get_restaurants(active_only=False, chat_id=chat_id)
            if 0 <= idx < len(restaurants):
                return restaurants[idx]
        except ValueError:
            pass

    # Try by name
    r = find_restaurant_by_name(text, chat_id=chat_id)
    if r:
        return r

    # Try partial match
    restaurants = get_restaurants(active_only=False, chat_id=chat_id)
    lower = text.lower()
    for r in restaurants:
        if lower in r["name"].lower():
            return r

    return None


def _resolve_watch(text: str, chat_id: str) -> dict | None:
    """Resolve a watch by #index from /watches."""
    text = text.strip()
    if text.startswith("#"):
        try:
            idx = int(text[1:]) - 1
            watches = get_watches(active_only=False, chat_id=chat_id)
            if 0 <= idx < len(watches):
                return watches[idx]
        except ValueError:
            pass
    return None


def _format_slots(slots: list, max_show: int = 8) -> str:
    """Format slot list for display."""
    lines = []
    for s in slots[:max_show]:
        time_str = s.get("time", "?")
        extra = s.get("extra", "")
        line = f"  🕐 {esc(time_str)}"
        if extra:
            line += f" — {esc(extra[:40])}"
        lines.append(line)
    if len(slots) > max_show:
        lines.append(f"  … and {len(slots) - max_show} more")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🍽 <b>TableWatch</b>\n"
        "I watch Resy &amp; OpenTable for open reservations and ping you the moment a slot drops.\n\n"
        "<b>Talk to me however you like</b> — I understand:\n"
        "  • <i>add bungalow</i> · <i>track carbone any 4</i>\n"
        "  • <i>check ishq</i> · <i>list</i> · <i>status</i>\n"
        "  • <i>stop watching odo</i> · <i>pause tatiana</i>\n\n"
        "<b>Or use slash commands</b> — type /help for the full list.\n\n"
        "<b>Step 1 — add a restaurant</b>\n"
        "<code>/add https://resy.com/cities/ny/semma Semma</code>\n"
        "(or paste any booking URL)\n\n"
        "<b>Step 2 — set a watch</b>\n"
        "<i>watch semma any 2</i>   ← any day next 30d\n"
        "<i>watch semma 2026-06-15 2</i>   ← specific date\n\n"
        "I'll alert you as soon as a slot opens."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 <b>How to talk to me</b>\n\n"
        "<b>Natural phrases (recommended)</b>\n"
        "  • <i>add bungalow</i> — start tracking\n"
        "  • <i>watch carbone any 4</i> — alert on any date, party 4\n"
        "  • <i>track ishq on 2026-06-15 for 2</i>\n"
        "  • <i>check tatiana</i> — one-off availability check\n"
        "  • <i>stop watching odo</i> — delete a watch\n"
        "  • <i>remove semma</i> — remove restaurant + all its watches\n"
        "  • <i>pause carbone</i> / <i>resume carbone</i>\n"
        "  • <i>list</i> · <i>watches</i> · <i>status</i>\n\n"
        "<b>Slash-command reference</b>\n"
        "/add <code>&lt;url&gt;</code> <code>[name]</code> — add a restaurant\n"
        "/list — your restaurants\n"
        "/remove <code>&lt;name|#N&gt;</code> — drop one\n"
        "/pause · /resume <code>&lt;name|#N&gt;</code>\n"
        "/watch <code>&lt;name&gt; &lt;date_spec&gt; [party] [time_pref]</code>\n"
        "  date_spec: <code>YYYY-MM-DD</code> · <code>any</code> · <code>DATE:DATE</code>\n"
        "  time_pref: any, lunch, dinner, late\n"
        "/watches — your watches\n"
        "/unwatch <code>&lt;#N&gt;</code>\n"
        "/check <code>&lt;name|#N&gt;</code> · /checkall\n"
        "/status — dashboard\n"
        "/setrelease · /learnrelease — release schedule\n\n"
        "<i>Tip: reference items by #N (e.g. #1) from /list or /watches.</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /add ──────────────────────────────────────────────────────────────

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add <url> [name]
    If name is omitted, tries to extract from URL.
    """
    chat_id = _chat_id(update)
    args = context.args

    if not args:
        await update.message.reply_text(
            "Usage: <code>/add &lt;booking_url&gt; [restaurant name]</code>\n\n"
            "Example:\n<code>/add https://resy.com/cities/ny/semma Semma</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    url = args[0]

    # Validate URL
    if not re.match(r"https?://", url):
        url = "https://" + url

    # Name: either provided or extracted from URL
    if len(args) > 1:
        name = " ".join(args[1:])
    else:
        # Extract from URL path
        from urllib.parse import urlparse
        path = urlparse(url).path.strip("/").split("/")
        name = path[-1].replace("-", " ").title() if path else "Unknown"

    platform = detect_platform(url)
    rid = add_restaurant(name, url, platform, chat_id=chat_id)

    await update.message.reply_text(
        f"✅ Added <b>{esc(name)}</b>\n"
        f"🔗 {esc(url[:80])}\n"
        f"📡 Platform: {esc(platform)}\n\n"
        f"Now create a watch:\n"
        f"<code>/watch {esc(name)} {date.today().isoformat()} 2</code>",
        parse_mode=ParseMode.HTML,
    )


# ── /list ─────────────────────────────────────────────────────────────

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = _chat_id(update)
    restaurants = get_restaurants(active_only=False, chat_id=chat_id)

    if not restaurants:
        await update.message.reply_text(
            "No restaurants tracked yet.\n\n"
            "Add one with:\n<code>/add https://resy.com/cities/ny/semma Semma</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = ["🏪 <b>Your Restaurants</b>\n"]
    for i, r in enumerate(restaurants, 1):
        status = "✅" if r.get("active", True) else "⏸"
        platform = r.get("platform", "generic").upper()
        lines.append(
            f"  <b>#{i}</b> {status} <b>{esc(r['name'])}</b>  "
            f"<code>[{esc(platform)}]</code>"
        )
    lines.append(f"\n<i>{len(restaurants)} restaurant(s) total</i>")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /remove ───────────────────────────────────────────────────────────

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = _chat_id(update)
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/remove &lt;name or #N&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    query = " ".join(context.args)
    r = _resolve_restaurant(query, chat_id)

    if not r:
        await update.message.reply_text(f"❌ Restaurant not found: {esc(query)}", parse_mode=ParseMode.HTML)
        return

    name = r["name"]
    delete_restaurant(r["id"])
    await update.message.reply_text(
        f"🗑 Removed <b>{esc(name)}</b> and all its watches.",
        parse_mode=ParseMode.HTML,
    )


# ── /pause & /resume ─────────────────────────────────────────────────

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = _chat_id(update)
    if not context.args:
        await update.message.reply_text("Usage: <code>/pause &lt;name or #N&gt;</code>", parse_mode=ParseMode.HTML)
        return

    query = " ".join(context.args)
    r = _resolve_restaurant(query, chat_id)
    if not r:
        await update.message.reply_text(f"❌ Not found: {esc(query)}", parse_mode=ParseMode.HTML)
        return

    toggle_restaurant(r["id"], False)
    await update.message.reply_text(f"⏸ Paused <b>{esc(r['name'])}</b>", parse_mode=ParseMode.HTML)


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = _chat_id(update)
    if not context.args:
        await update.message.reply_text("Usage: <code>/resume &lt;name or #N&gt;</code>", parse_mode=ParseMode.HTML)
        return

    query = " ".join(context.args)
    r = _resolve_restaurant(query, chat_id)
    if not r:
        await update.message.reply_text(f"❌ Not found: {esc(query)}", parse_mode=ParseMode.HTML)
        return

    toggle_restaurant(r["id"], True)
    await update.message.reply_text(f"▶ Resumed <b>{esc(r['name'])}</b>", parse_mode=ParseMode.HTML)


# ── /watch ────────────────────────────────────────────────────────────

def _parse_watch_date(args: list[str]) -> tuple[int, str, str, str, str, int]:
    """
    Find the date token(s) in args and return:
      (date_idx, date_mode, target_date, date_from, date_to, tokens_consumed)

    Supported formats (all after the restaurant name):
      YYYY-MM-DD              → single date
      YYYY-MM-DD:YYYY-MM-DD   → compact range
      YYYY-MM-DD to YYYY-MM-DD → verbose range (3 tokens)
      any                     → any date in next 30 days
    """
    for i, arg in enumerate(args):
        low = arg.lower()

        if low == "any":
            return i, "any", "any", "", "", 1

        if re.match(r"\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$", arg):
            d_from, d_to = arg.split(":")
            return i, "range", arg, d_from, d_to, 1

        if re.match(r"\d{4}-\d{2}-\d{2}$", arg):
            # Check for verbose range: YYYY-MM-DD to YYYY-MM-DD
            if (i + 2 < len(args)
                    and args[i + 1].lower() == "to"
                    and re.match(r"\d{4}-\d{2}-\d{2}$", args[i + 2])):
                d_from, d_to = arg, args[i + 2]
                return i, "range", f"{d_from}:{d_to}", d_from, d_to, 3
            return i, "single", arg, "", "", 1

    return -1, "", "", "", "", 0


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /watch <name|#N> <date_spec> [party_size] [time_pref]

    date_spec:
      YYYY-MM-DD                   – specific date
      any                          – any date in the next 30 days
      YYYY-MM-DD:YYYY-MM-DD        – date range (compact)
      YYYY-MM-DD to YYYY-MM-DD     – date range (verbose)
    """
    chat_id = _chat_id(update)
    args = context.args

    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/watch &lt;name|#N&gt; &lt;date_spec&gt; [party_size] [time_pref]</code>\n\n"
            "Examples:\n"
            "<code>/watch Semma 2026-05-15</code>\n"
            "<code>/watch Semma any 4</code>\n"
            "<code>/watch Semma 2026-05-01:2026-05-31 4 dinner</code>\n"
            "<code>/watch Semma 2026-05-01 to 2026-05-31 4</code>\n\n"
            "Time prefs: any, lunch, dinner, late",
            parse_mode=ParseMode.HTML,
        )
        return

    date_idx, date_mode, target_date, date_from, date_to, tokens = _parse_watch_date(args)

    if date_idx == -1:
        await update.message.reply_text(
            "❌ Couldn't find a date. Use <code>YYYY-MM-DD</code>, "
            "<code>YYYY-MM-DD:YYYY-MM-DD</code>, or <code>any</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    restaurant_query = " ".join(args[:date_idx])

    # Validate dates
    today = date.today()
    try:
        if date_mode == "single":
            if datetime.strptime(target_date, "%Y-%m-%d").date() < today:
                await update.message.reply_text("❌ Date is in the past.", parse_mode=ParseMode.HTML)
                return
        elif date_mode == "range":
            d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
            d_to   = datetime.strptime(date_to,   "%Y-%m-%d").date()
            if d_to < today:
                await update.message.reply_text("❌ End date is in the past.", parse_mode=ParseMode.HTML)
                return
            if d_from > d_to:
                await update.message.reply_text("❌ Start date must be before end date.", parse_mode=ParseMode.HTML)
                return
    except ValueError:
        await update.message.reply_text("❌ Invalid date format. Use YYYY-MM-DD.", parse_mode=ParseMode.HTML)
        return

    # Optional args after the date spec
    remaining = args[date_idx + tokens:]
    party_size = 2
    time_pref  = "any"

    if remaining:
        try:
            party_size = int(remaining[0])
        except ValueError:
            time_pref = remaining[0]
    if len(remaining) > 1:
        time_pref = remaining[1]

    r = _resolve_restaurant(restaurant_query, chat_id)
    if not r:
        await update.message.reply_text(
            f"❌ Restaurant not found: <b>{esc(restaurant_query)}</b>\n"
            f"Use /list to see your restaurants, or /add to add one first.",
            parse_mode=ParseMode.HTML,
        )
        return

    add_watch(
        restaurant_id  = r["id"],
        target_date    = target_date,
        party_size     = party_size,
        time_preference= time_pref,
        chat_id        = chat_id,
        date_mode      = date_mode,
        date_from      = date_from,
        date_to        = date_to,
    )

    if date_mode == "any":
        date_display = "any available date (next 30 days)"
    elif date_mode == "range":
        date_display = f"{date_from} → {date_to}"
    else:
        date_display = target_date

    await update.message.reply_text(
        f"👁 <b>Watch created</b>\n\n"
        f"🏪 {esc(r['name'])}\n"
        f"📅 {esc(date_display)}\n"
        f"👥 Party of {party_size}\n"
        f"🕐 Time: {esc(time_pref)}\n\n"
        f"I'll check every {CHECK_INTERVAL_MINUTES} min and alert you when slots open.",
        parse_mode=ParseMode.HTML,
    )


# ── /watches ──────────────────────────────────────────────────────────

async def cmd_watches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = _chat_id(update)
    watches = get_watches(active_only=False, chat_id=chat_id)

    if not watches:
        await update.message.reply_text(
            "No watches yet. Create one with:\n"
            "<code>/watch Semma 2026-03-15 2</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    burst_ids = get_burst_watch_ids()
    lines = ["👁 <b>Your Watches</b>\n"]
    for i, w in enumerate(watches, 1):
        status = "✅" if w.get("active") else "⏸"
        latest = get_latest_availability(w["id"])
        if latest and latest.get("slots_found"):
            slot_info = f"🟢 {len(latest['slots_found'])} slots"
        elif latest:
            slot_info = "🟡 No slots"
        else:
            slot_info = "⚪ Pending"

        extras = []
        if w.get("auto_book"):        extras.append("🤖 auto-book")
        if w.get("waitlist_joined"):  extras.append("📋 waitlisted")
        if w["id"] in burst_ids:      extras.append("🔥 burst mode")
        extras_str = "  " + "  ".join(extras) if extras else ""

        mode = w.get("date_mode", "single")
        if mode == "any":
            date_display = "any date (30d)"
        elif mode == "range":
            date_display = f"{w.get('date_from', '?')} → {w.get('date_to', '?')}"
        else:
            date_display = w["target_date"]

        lines.append(
            f"  <b>#{i}</b> {status} <b>{esc(w['restaurant_name'])}</b>\n"
            f"      📅 {esc(date_display)}  👥 {w['party_size']}  🕐 {w.get('time_preference', 'any')}\n"
            f"      {slot_info}{extras_str}"
        )

    lines.append(f"\n<i>{len(watches)} watch(es) total</i>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /unwatch ──────────────────────────────────────────────────────────

async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = _chat_id(update)
    if not context.args:
        await update.message.reply_text("Usage: <code>/unwatch &lt;#N&gt;</code>", parse_mode=ParseMode.HTML)
        return

    query = context.args[0]
    w = _resolve_watch(query, chat_id)
    if not w:
        await update.message.reply_text(
            f"❌ Watch not found: {esc(query)}\nUse /watches to see numbers.",
            parse_mode=ParseMode.HTML,
        )
        return

    name = w["restaurant_name"]
    delete_watch(w["id"])
    await update.message.reply_text(
        f"🗑 Deleted watch for <b>{esc(name)}</b> on {w['target_date']}",
        parse_mode=ParseMode.HTML,
    )


# ── /check ────────────────────────────────────────────────────────────

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """One-off check for a restaurant's watches."""
    chat_id = _chat_id(update)
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/check &lt;name or #N&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    query = " ".join(context.args)
    r = _resolve_restaurant(query, chat_id)
    if not r:
        await update.message.reply_text(f"❌ Not found: {esc(query)}", parse_mode=ParseMode.HTML)
        return

    # Find active watches for this restaurant
    watches = get_watches(active_only=True, chat_id=chat_id)
    restaurant_watches = [w for w in watches if w["restaurant_id"] == r["id"]]

    if not restaurant_watches:
        # No watches — do a generic check with today's date
        await update.message.reply_text(f"⏳ Checking <b>{esc(r['name'])}</b>…", parse_mode=ParseMode.HTML)
        result = check_availability(
            url=r["url"],
            target_date=date.today().isoformat(),
            party_size=2,
            platform=r.get("platform", "auto"),
        )
        if result["slots"]:
            text = (
                f"✅ <b>{esc(r['name'])}</b> — {len(result['slots'])} slot(s)\n\n"
                f"{_format_slots(result['slots'])}\n\n"
                f"<a href=\"{esc_attr(r['url'])}\">Book now →</a>"
            )
        elif result["success"]:
            text = f"⚠️ <b>{esc(r['name'])}</b> — No slots found.\nThe page may be JS-rendered or fully booked."
        else:
            text = f"❌ Failed to check <b>{esc(r['name'])}</b>: {esc(result['message'])}"

        await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    # Check each watch
    await update.message.reply_text(
        f"⏳ Checking <b>{esc(r['name'])}</b> ({len(restaurant_watches)} watch{'es' if len(restaurant_watches) != 1 else ''})…",
        parse_mode=ParseMode.HTML,
    )

    loop = asyncio.get_running_loop()
    for w in restaurant_watches:
        result = await loop.run_in_executor(None, check_single_watch, w)
        if result["slots"]:
            text = (
                f"✅ <b>{esc(w['restaurant_name'])}</b>\n"
                f"📅 {w['target_date']}  👥 {w['party_size']}\n\n"
                f"{_format_slots(result['slots'])}\n\n"
                f"<a href=\"{esc_attr(w['restaurant_url'])}\">Book now →</a>"
            )
        elif result["success"]:
            text = (
                f"⚠️ <b>{esc(w['restaurant_name'])}</b>\n"
                f"📅 {w['target_date']}  👥 {w['party_size']}\n"
                f"No slots found."
            )
        else:
            text = f"❌ Failed: {esc(result['message'])}"

        await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ── /checkall ─────────────────────────────────────────────────────────

async def cmd_checkall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = _chat_id(update)
    watches = get_watches(active_only=True, chat_id=chat_id)

    if not watches:
        await update.message.reply_text("No active watches to check.", parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text(
        f"⏳ Checking {len(watches)} watch{'es' if len(watches) != 1 else ''}…",
        parse_mode=ParseMode.HTML,
    )

    loop = asyncio.get_running_loop()
    found_any = False
    for w in watches:
        try:
            result = await loop.run_in_executor(None, check_single_watch, w)
            if result["slots"]:
                found_any = True
                text = (
                    f"✅ <b>{esc(w['restaurant_name'])}</b>\n"
                    f"📅 {w['target_date']}  👥 {w['party_size']}\n\n"
                    f"{_format_slots(result['slots'])}\n\n"
                    f"<a href=\"{esc_attr(w['restaurant_url'])}\">Book now →</a>"
                )
                await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as exc:
            logger.exception("Error checking watch %s: %s", w.get("id"), exc)

    if not found_any:
        await update.message.reply_text("No slots found across any watches. I'll keep checking!", parse_mode=ParseMode.HTML)


# ── /autobook ─────────────────────────────────────────────────────────

async def cmd_autobook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /autobook <#N> on|off
    Toggle automatic slot-booking for a specific watch.
    """
    chat_id = _chat_id(update)
    args = context.args

    if len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/autobook &lt;#N&gt; on|off</code>\n\n"
            "Example: <code>/autobook #1 on</code>\n\n"
            "Use /watches to see watch numbers.\n\n"
            + (
                "⚠️ <b>Auto-booking is currently disabled globally.</b>\n"
                "Set <code>AUTO_BOOK_ENABLED=true</code> in .env and add your credentials to activate."
                if not AUTO_BOOK_ENABLED else
                "✅ Auto-booking is <b>globally enabled</b>. Toggle it per-watch below."
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    watch_ref = args[0]
    toggle = args[1].lower()

    if toggle not in ("on", "off"):
        await update.message.reply_text(
            "Please use <code>on</code> or <code>off</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    w = _resolve_watch(watch_ref, chat_id)
    if not w:
        await update.message.reply_text(
            f"❌ Watch not found: {esc(watch_ref)}\nUse /watches to see your list.",
            parse_mode=ParseMode.HTML,
        )
        return

    enabled = toggle == "on"
    set_watch_auto_book(w["id"], enabled)

    icon = "🤖" if enabled else "🛑"
    state_text = "enabled" if enabled else "disabled"

    msg = (
        f"{icon} Auto-booking <b>{state_text}</b> for watch:\n\n"
        f"🏪 {esc(w['restaurant_name'])}\n"
        f"📅 {w['target_date']}  👥 {w['party_size']}\n"
    )

    if enabled and not AUTO_BOOK_ENABLED:
        msg += (
            "\n⚠️ Note: <code>AUTO_BOOK_ENABLED</code> is still <b>false</b> in .env.\n"
            "Set it to <code>true</code> and add credentials before booking will fire."
        )

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


# ── /bookings ─────────────────────────────────────────────────────────

async def cmd_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent auto-booking history for this chat."""
    chat_id = _chat_id(update)
    bookings = get_bookings(chat_id=chat_id, limit=10)

    if not bookings:
        await update.message.reply_text(
            "No auto-booking attempts yet.\n\n"
            "Enable auto-booking on a watch with:\n"
            "<code>/autobook #1 on</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = ["📋 <b>Recent Auto-Bookings</b>\n"]
    for b in bookings:
        icon = "✅" if b.get("success") else "❌"
        booked_at = b.get("booked_at", "")
        if hasattr(booked_at, "strftime"):
            booked_at = booked_at.strftime("%b %d %H:%M")
        conf = f" · #{esc(b['confirmation_number'])}" if b.get("confirmation_number") else ""
        lines.append(
            f"{icon} <b>{esc(b.get('platform', '?').title())}</b>{conf}\n"
            f"   <i>{esc(b.get('message', '')[:80])}</i>\n"
            f"   🕐 {booked_at}"
        )

    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)


# ── /setrelease ───────────────────────────────────────────────────────

async def cmd_setrelease(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setrelease <restaurant name|#N> <days_ahead> <HH:MM>

    Manually set the reservation release schedule for a restaurant.
    Example: /setrelease Semma 30 00:00
             → Semma releases 30 days in advance at midnight ET.

    The scheduler will enter burst mode (checking every 10s) starting
    BURST_WINDOW_MINUTES before this time.
    """
    chat_id = _chat_id(update)
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Usage: <code>/setrelease &lt;name|#N&gt; &lt;days_ahead&gt; &lt;HH:MM&gt;</code>\n\n"
            "Examples:\n"
            "<code>/setrelease Semma 30 00:00</code>\n"
            "<code>/setrelease #1 28 09:00</code>\n\n"
            "<i>Times are in US Eastern Time (ET).</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Find the time arg (matches HH:MM)
    time_idx = None
    for i, arg in enumerate(args):
        if re.match(r"^\d{1,2}:\d{2}$", arg):
            time_idx = i
            break

    if time_idx is None or time_idx < 2:
        await update.message.reply_text(
            "❌ Could not find a time. Use <code>HH:MM</code> format (e.g. <code>00:00</code> or <code>09:00</code>).",
            parse_mode=ParseMode.HTML,
        )
        return

    days_str   = args[time_idx - 1]
    time_str   = args[time_idx]
    name_query = " ".join(args[:time_idx - 1])

    try:
        days_ahead = int(days_str)
        if not (1 <= days_ahead <= 365):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ days_ahead must be a number between 1 and 365.", parse_mode=ParseMode.HTML)
        return

    r = _resolve_restaurant(name_query, chat_id)
    if not r:
        await update.message.reply_text(f"❌ Restaurant not found: <b>{esc(name_query)}</b>", parse_mode=ParseMode.HTML)
        return

    update_restaurant_release(r["id"], days_ahead, time_str, learned=False)

    await update.message.reply_text(
        f"✅ Release schedule set for <b>{esc(r['name'])}</b>\n\n"
        f"📅 <b>{days_ahead}</b> days before dining date\n"
        f"🕐 <b>{time_str} ET</b>\n\n"
        f"The bot will switch to burst mode (every {BURST_CHECK_INTERVAL_SECONDS}s checks) "
        f"starting {BURST_CHECK_INTERVAL_SECONDS // 60 or 1}–15 min before release.",
        parse_mode=ParseMode.HTML,
    )


# ── /joinwaitlist ─────────────────────────────────────────────────────

async def cmd_joinwaitlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /joinwaitlist <#N>
    Auto-join the platform's built-in notify/waitlist for a watch.
    """
    chat_id = _chat_id(update)
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/joinwaitlist &lt;#N&gt;</code>\n\n"
            "Example: <code>/joinwaitlist #1</code>\n\n"
            "Use /watches to see watch numbers.",
            parse_mode=ParseMode.HTML,
        )
        return

    w = _resolve_watch(context.args[0], chat_id)
    if not w:
        await update.message.reply_text(
            f"❌ Watch not found: {esc(context.args[0])}\nUse /watches to see your list.",
            parse_mode=ParseMode.HTML,
        )
        return

    platform = w.get("restaurant_platform", "generic")
    if platform not in ("resy", "opentable"):
        await update.message.reply_text(
            f"⚠️ Waitlist joining is only supported for Resy and OpenTable.\n"
            f"This watch uses: <b>{esc(platform)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        f"⏳ Joining {esc(platform.title())} waitlist for "
        f"<b>{esc(w['restaurant_name'])}</b> on {w['target_date']}…",
        parse_mode=ParseMode.HTML,
    )

    try:
        from waitlist import join_notify
        result = join_notify(w)
    except Exception as exc:
        result = {"success": False, "message": str(exc), "platform": platform}

    if result["success"]:
        mark_waitlist_joined(w["id"], platform)
        await update.message.reply_text(
            f"✅ <b>Waitlist joined!</b>\n\n"
            f"🏪 {esc(w['restaurant_name'])}\n"
            f"📅 {w['target_date']}  👥 {w['party_size']}\n\n"
            f"<i>{esc(result['message'])}</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"❌ <b>Waitlist join failed</b>\n\n"
            f"<i>{esc(result['message'])}</i>",
            parse_mode=ParseMode.HTML,
        )


# ── /learnrelease ─────────────────────────────────────────────────────

async def cmd_learnrelease(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /learnrelease
    Trigger the release pattern learner immediately (normally runs at 02:00 ET).
    """
    await update.message.reply_text(
        "🧠 Running release pattern analysis on your history…\n"
        "<i>This may take a moment.</i>",
        parse_mode=ParseMode.HTML,
    )

    try:
        from release_learner import learn_all
        learn_all()

        # Show updated restaurants
        restaurants = get_restaurants(active_only=False, chat_id=_chat_id(update))
        lines = ["🧠 <b>Release Patterns Learned</b>\n"]
        any_learned = False
        for r in restaurants:
            days  = r.get("release_days_ahead")
            time  = r.get("release_time_et")
            conf  = r.get("release_confidence", 0)
            src   = "📚 learned" if r.get("release_learned") else "✏️ manual"
            if days and time:
                any_learned = True
                lines.append(
                    f"  🏪 <b>{esc(r['name'])}</b>\n"
                    f"     {days}d before dining at <b>{time} ET</b>  "
                    f"({src}, {conf*100:.0f}% confidence)"
                )

        if not any_learned:
            lines.append(
                "<i>Not enough history yet to learn patterns.\n"
                "Patterns emerge after 3+ watched dates per restaurant.\n"
                "You can also set them manually with /setrelease.</i>"
            )

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    except Exception as exc:
        logger.exception("learnrelease error: %s", exc)
        await update.message.reply_text(
            f"❌ Learner error: {esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )


# ── /status ───────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = _chat_id(update)
    restaurants = get_restaurants(active_only=False, chat_id=chat_id)
    watches = get_watches(active_only=False, chat_id=chat_id)
    active_watches = [w for w in watches if w.get("active")]

    # Count watches with slots
    slots_found = 0
    for w in active_watches:
        latest = get_latest_availability(w["id"])
        if latest and latest.get("slots_found"):
            slots_found += 1

    auto_book_watches  = sum(1 for w in active_watches if w.get("auto_book"))
    waitlisted_watches = sum(1 for w in active_watches if w.get("waitlist_joined"))
    auto_book_status   = "✅ ON" if AUTO_BOOK_ENABLED else "❌ OFF (set AUTO_BOOK_ENABLED=true)"
    burst_ids          = get_burst_watch_ids()
    burst_count        = len(burst_ids)

    # Count restaurants with learned release patterns
    learned_count = sum(
        1 for r in restaurants
        if r.get("release_days_ahead") is not None
    )

    text = (
        f"📊 <b>TableWatch Status</b>\n\n"
        f"🏪 Restaurants: <b>{len(restaurants)}</b>  "
        f"({learned_count} with release schedule)\n"
        f"👁 Active watches: <b>{len(active_watches)}</b> / {len(watches)}\n"
        f"🟢 Slots found: <b>{slots_found}</b>\n"
        f"🔥 Burst mode: <b>{burst_count} watch(es)</b>\n"
        f"🤖 Auto-book: <b>{auto_book_status}</b>  "
        f"({auto_book_watches} watch(es))\n"
        f"📋 Waitlisted: <b>{waitlisted_watches} watch(es)</b>\n\n"
        f"⏱ Normal check: every <b>{CHECK_INTERVAL_MINUTES} min</b>\n"
        f"🔥 Burst check: every <b>{BURST_CHECK_INTERVAL_SECONDS}s</b>\n\n"
        f"<i>Scheduler running. Burst mode activates automatically\n"
        f"when a known release window is approaching.\n"
        f"Use /setrelease or /learnrelease to configure.</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── Catch-all for unrecognized messages ───────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle plain text. Try the natural-language parser first; fall back to URL
    detection and finally to a help-prompt.
    """
    text = update.message.text.strip()
    from nl_parser import (
        parse, INTENT_ADD, INTENT_WATCH, INTENT_CHECK, INTENT_REMOVE,
        INTENT_UNWATCH, INTENT_LIST, INTENT_WATCHES, INTENT_STATUS,
        INTENT_HELP, INTENT_PAUSE, INTENT_RESUME, INTENT_UNKNOWN,
    )

    intent = parse(text)
    if intent.kind != INTENT_UNKNOWN and intent.confidence >= 0.5:
        await _dispatch_intent(update, context, intent)
        return

    # Legacy URL-only fallback
    url_match = re.search(r"https?://[^\s.,;!?)'\"]+", text)
    if url_match:
        url = url_match.group(0)
        platform = detect_platform(url)
        await update.message.reply_text(
            f"📎 Got a link. Want me to track this?\n\n"
            f"Reply <code>add</code> to confirm — or use:\n"
            f"<code>/add {esc(url)}</code>\n\n"
            f"Detected platform: <b>{esc(platform)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        "🤔 I didn't catch that. Try things like:\n"
        "  • <i>add bungalow</i>\n"
        "  • <i>watch carbone any 4</i>\n"
        "  • <i>check ishq</i>\n"
        "  • <i>list</i> · <i>status</i> · <i>help</i>",
        parse_mode=ParseMode.HTML,
    )


async def _dispatch_intent(update, context, intent):
    """Route a parsed NL Intent to the corresponding slash-command handler."""
    from nl_parser import (
        INTENT_ADD, INTENT_WATCH, INTENT_CHECK, INTENT_REMOVE, INTENT_UNWATCH,
        INTENT_LIST, INTENT_WATCHES, INTENT_STATUS, INTENT_HELP,
        INTENT_PAUSE, INTENT_RESUME,
    )

    # Build a fake context.args list and reuse existing handlers
    class _A: pass

    async def _run(handler, args):
        ctx = _A()
        ctx.args = args
        await handler(update, ctx)

    if intent.kind == INTENT_LIST:    return await _run(cmd_list,    [])
    if intent.kind == INTENT_WATCHES: return await _run(cmd_watches, [])
    if intent.kind == INTENT_STATUS:  return await _run(cmd_status,  [])
    if intent.kind == INTENT_HELP:    return await _run(cmd_help,    [])

    if intent.kind == INTENT_PAUSE:   return await _run(cmd_pause,   intent.name.split())
    if intent.kind == INTENT_RESUME:  return await _run(cmd_resume,  intent.name.split())
    if intent.kind == INTENT_CHECK:   return await _run(cmd_check,   intent.name.split())
    if intent.kind == INTENT_REMOVE:  return await _run(cmd_remove,  intent.name.split())

    if intent.kind == INTENT_UNWATCH:
        # /unwatch needs #N — look up first watch by name
        chat_id = _chat_id(update)
        watches = get_watches(active_only=False, chat_id=chat_id)
        match = next((w for w in watches
                      if intent.name.lower() in w["restaurant_name"].lower()), None)
        if not match:
            await update.message.reply_text(
                f"❌ No watch found matching <b>{esc(intent.name)}</b>.",
                parse_mode=ParseMode.HTML,
            )
            return
        delete_watch(match["id"])
        await update.message.reply_text(
            f"🗑 Stopped watching <b>{esc(match['restaurant_name'])}</b> on {esc(match['target_date'])}",
            parse_mode=ParseMode.HTML,
        )
        return

    if intent.kind == INTENT_ADD:
        if intent.url:
            return await _run(cmd_add, [intent.url])
        # No URL — name only. Check if restaurant already exists; if so, suggest /watch.
        chat_id = _chat_id(update)
        existing = _resolve_restaurant(intent.name, chat_id)
        if existing:
            await update.message.reply_text(
                f"✅ <b>{esc(existing['name'])}</b> is already tracked.\n\n"
                f"Create a watch with:\n"
                f"<code>/watch {esc(existing['name'])} any {intent.party_size}</code>\n"
                f"or just say <i>watch {esc(existing['name'])} any</i>",
                parse_mode=ParseMode.HTML,
            )
            return
        await update.message.reply_text(
            f"🔗 I need a booking URL to add <b>{esc(intent.name)}</b>.\n\n"
            f"Send something like:\n"
            f"<code>/add https://resy.com/cities/ny/{intent.name.lower().replace(' ', '-')} {esc(intent.name)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if intent.kind == INTENT_WATCH:
        chat_id = _chat_id(update)
        r = _resolve_restaurant(intent.name, chat_id)
        if not r:
            await update.message.reply_text(
                f"❌ I don't have <b>{esc(intent.name)}</b> in your list yet.\n"
                f"Add it first: <code>/add &lt;url&gt; {esc(intent.name)}</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        date_arg = intent.date or "any"
        args = [r["name"], date_arg, str(intent.party_size)]
        return await _run(cmd_watch, args)


# ═══════════════════════════════════════════════════════════════════════
# Scheduler Integration
# ═══════════════════════════════════════════════════════════════════════

async def _send_alert_to_chat(bot, chat_id: str, watch: dict, slots: list):
    """Send an availability alert to a specific chat."""
    text = (
        f"🍽 <b>Reservation Alert!</b>\n\n"
        f"<b>{esc(watch['restaurant_name'])}</b>\n"
        f"📅 {watch['target_date']}  👥 Party of {watch['party_size']}\n\n"
        f"<b>Available slots:</b>\n{_format_slots(slots)}\n\n"
        f"<a href=\"{esc_attr(watch['restaurant_url'])}\">Book now →</a>"
    )
    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error("Failed to send alert to chat %s: %s", chat_id, exc)


# ═══════════════════════════════════════════════════════════════════════
# Bot Menu Setup
# ═══════════════════════════════════════════════════════════════════════

async def post_init(application: Application):
    """Set the bot's command menu in Telegram."""
    commands = [
        BotCommand("start", "Welcome + quick start"),
        BotCommand("help", "Full command reference"),
        BotCommand("add", "Add a restaurant — /add <url> [name]"),
        BotCommand("list", "List your restaurants"),
        BotCommand("remove", "Remove a restaurant"),
        BotCommand("watch", "Watch availability — /watch <name> <date|any|range> [party]"),
        BotCommand("watches", "List your watches"),
        BotCommand("unwatch", "Delete a watch — /unwatch <#N>"),
        BotCommand("check", "Check a restaurant now"),
        BotCommand("checkall", "Check all active watches"),
        BotCommand("autobook",      "Toggle auto-booking — /autobook <#N> on|off"),
        BotCommand("bookings",      "Show auto-booking history"),
        BotCommand("setrelease",    "Set release schedule — /setrelease <name> <days> <HH:MM>"),
        BotCommand("joinwaitlist",  "Join platform waitlist — /joinwaitlist <#N>"),
        BotCommand("learnrelease",  "Run release pattern analysis now"),
        BotCommand("status",        "Dashboard overview"),
        BotCommand("pause", "Pause a restaurant"),
        BotCommand("resume", "Resume a restaurant"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot menu commands registered.")


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    from logging_config import configure as _configure_logging
    _configure_logging()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env — cannot start bot.")
        return

    if not check_connection():
        logger.error("Cannot connect to MongoDB — check MONGO_URI in .env.")
        return

    # Start the background availability checker
    logger.info("Starting background scheduler…")
    start_scheduler()

    # Build the Telegram bot
    logger.info("Starting Telegram bot…")
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Register command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("watches", cmd_watches))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("checkall", cmd_checkall))
    app.add_handler(CommandHandler("autobook",     cmd_autobook))
    app.add_handler(CommandHandler("bookings",     cmd_bookings))
    app.add_handler(CommandHandler("setrelease",   cmd_setrelease))
    app.add_handler(CommandHandler("joinwaitlist", cmd_joinwaitlist))
    app.add_handler(CommandHandler("learnrelease", cmd_learnrelease))
    app.add_handler(CommandHandler("status",       cmd_status))

    # Catch-all for regular messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
