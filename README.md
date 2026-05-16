# 🍽 Restaurant Booking Tracker

A self-hosted reservation monitor that watches Resy, OpenTable, Yelp, and generic restaurant websites for open slots and sends instant Telegram alerts the moment something becomes available.

Built this because Resy's own "Notify" feature is too slow — by the time you get the email, the slot is gone. This polls faster, detects cancellations through slot-count diff tracking, and learns each restaurant's release schedule to enter burst mode right before new inventory drops.

---

## What It Does

- **Monitors** restaurant reservation pages on Resy, OpenTable, Yelp, or any URL
- **Alerts instantly** via Telegram (and WhatsApp via Twilio if configured)
- **Conversational Telegram bot** — `add bungalow`, `watch carbone any 4`, `stop watching odo` — no slash commands required
- **🆕 Chrome extension bridge** — bypasses Resy's reCAPTCHA / 419 by polling from your own logged-in session, see [`extension/`](./extension/)
- **Burst mode** — detects upcoming release windows and switches to aggressive polling right before inventory drops
- **Diff detection** — when slot count changes mid-day (cancellations), automatically accelerates polling for 5 minutes
- **Adaptive intervals** — hot watches (≤3 days out) poll every 60s, cold ones every 15min
- **Fan-out cache** — multiple watches on the same venue share one API call per cycle
- **Rate limiting** — per-domain token bucket prevents IP bans
- **Metrics + structured logs** — every fetch, alert, burst trigger, and fallback recorded with latency in `logs/api.log`
- **/healthz endpoint** — external monitors can detect a stuck scheduler
- **Auto-cleanup** — past watches and stale alerts deleted nightly

---

## How I Built It

### Step 1 — Scraper foundation

Started with BeautifulSoup + Playwright to parse availability from restaurant pages. The problem: Resy and OpenTable are React SPAs, so raw HTML scraping misses most data. Added a Playwright headless browser fallback for JS-rendered pages alongside a fast `requests`-based path for static sites.

### Step 2 — Direct API clients

Reversed the Resy and OpenTable mobile APIs (both use simple REST with `Authorization: ResyToken` headers). Built `api_client.py` with `ResyClient` and `OpenTableClient` — these are 10× faster than Playwright and return structured JSON. API path runs first; scraper is the fallback.

### Step 3 — MongoDB persistence

Chose MongoDB because watch configs are naturally document-shaped and the schema evolved a lot early on. Collections:

| Collection | Purpose |
|---|---|
| `restaurants` | Tracked venues with platform + venue_id |
| `watches` | Date + party_size + time_preference combos |
| `availability` | Every slot snapshot with timestamp |
| `alert_log` | Cooldown tracking (no repeat spam) |
| `bookings` | Auto-booking attempt history |
| `metrics` | Internal events with latency |

Compound unique index on `(restaurant_id, target_date, party_size, time_preference, chat_id)` prevents duplicate watches.

### Step 4 — Telegram bot + alerts

Built a full Telegram bot (`bot.py`) for managing watches via chat commands. Separate `alerts.py` for formatting availability messages. All user-controlled text escaped with `html.escape()` before going into Telegram's HTML parse mode.

### Step 5 — APScheduler backbone

`scheduler.py` runs four background jobs:

| Job | Cadence | Purpose |
|---|---|---|
| Adaptive tick | Every 30s | Checks watches that are due |
| Burst check | Every 10s | Fast-polls burst-mode watches |
| Release monitor | Every 5min | Detects upcoming drops, enters burst |
| Nightly cleanup | 03:00 ET | Deletes past watches, prunes old logs |

### Step 6 — Release pattern learner

`release_learner.py` analyzes historical availability data per restaurant to detect *when* new slots typically drop (e.g. exactly 30 days out at midnight, or 28 days at 9am ET). When a release window is predicted within `BURST_WINDOW_MINUTES`, the scheduler enters burst mode for affected watches automatically.

### Step 7 — Slot cache + diff detection

Problem: with many watches on the same Resy venue, I was making N identical API calls per cycle. Added `slot_cache.py` — a 5-second in-process TTL cache with per-key locking. Concurrent watches on the same venue share one network round-trip.

Added diff tracking alongside: if slot count goes up mid-day, someone cancelled. `record_and_diff()` detects this and `_accelerate_venue()` pushes all matching watches into burst mode for 5 minutes.

### Step 8 — Rate limiter

Per-domain sliding-window token bucket in `rate_limiter.py`. All scheduler threads share one bucket per platform. Prevents IP bans even with 50+ active watches.

| Platform | Limit |
|---|---|
| Resy | 10 req/s |
| OpenTable | 5 req/s |
| Yelp | 3 req/s |
| Generic | 2 req/s |

### Step 9 — Adaptive polling intervals

Replaced the single global `CHECK_INTERVAL_MINUTES` with per-watch priority scoring in `priority.py`. Interval computed from days-to-target:

| Days out | Poll interval |
|---|---|
| ≤ 3 days | 60 seconds |
| ≤ 14 days | 5 minutes |
| > 14 days | 15 minutes |
| Past | 1 hour |

Scheduler ticks every 30s and uses `is_due()` to decide which watches actually run.

### Step 10 — Metrics + observability

`metrics.py` logs every meaningful event to a `metrics` collection — cache hits/misses, fetch durations, slot deltas, alert sends, burst triggers, login attempts. Non-blocking, best-effort. Streamlit has a **Metrics** tab with live event counts and fetch latency percentiles.

---

## Stack

| Layer | Tech |
|---|---|
| UI | Streamlit |
| Database | MongoDB (PyMongo) |
| Scraping | BeautifulSoup + Playwright |
| API clients | Direct Resy/OpenTable REST |
| Scheduling | APScheduler 3.x |
| Notifications | Telegram Bot API |
| Config | python-dotenv |

---

## Setup

### Step 1 — Prerequisites

Make sure you have the following installed and ready:

| Requirement | Notes |
|---|---|
| Python 3.11+ | `python3 --version` to check |
| MongoDB | Local install **or** free [MongoDB Atlas](https://cloud.mongodb.com) cluster |
| Telegram Bot Token | Create via [@BotFather](https://t.me/BotFather) on Telegram |
| Your Telegram Chat ID | Get it from [@userinfobot](https://t.me/userinfobot) |

---

### Step 2 — Clone the repo

```bash
git clone https://github.com/rchhabra13/restaurant-book-track.git
cd restaurant-book-track
```

---

### Step 3 — Create a Python virtual environment

```bash
python3 -m venv .venv

# Mac / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

---

### Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

Install Playwright's headless browser (used as scraper fallback):

```bash
playwright install chromium
```

---

### Step 5 — Create your Telegram bot

1. Open Telegram and message **[@BotFather](https://t.me/BotFather)**
2. Send `/newbot` and follow the prompts
3. Copy the **API token** (looks like `110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw`)
4. Message your new bot once (so it can send you messages)
5. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
6. Find your **chat ID** in the `"chat":{"id": ...}` field

---

### Step 6 — Set up MongoDB

**Option A — Local (fastest for dev):**
```bash
# Mac (Homebrew)
brew install mongodb-community
brew services start mongodb-community

# Your URI will be:
MONGO_URI=mongodb://localhost:27017
```

**Option B — MongoDB Atlas (free, cloud-hosted):**
1. Sign up at [cloud.mongodb.com](https://cloud.mongodb.com)
2. Create a free M0 cluster
3. Database Access → Add a user with read/write permissions
4. Network Access → Allow your IP (or `0.0.0.0/0` for dev)
5. Connect → Drivers → copy the connection string:
```
MONGO_URI=mongodb+srv://user:password@cluster.mongodb.net/tablewatch
```

---

### Step 7 — Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```env
# ── Required ──────────────────────────────────────────────────────────
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=tablewatch

TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_numeric_chat_id_here

# ── Resy (optional but recommended — 10x faster than scraping) ────────
RESY_EMAIL=your@email.com
RESY_PASSWORD=yourpassword

# ── OpenTable (optional) ──────────────────────────────────────────────
OPENTABLE_EMAIL=your@email.com
OPENTABLE_PASSWORD=yourpassword

# ── WhatsApp via Twilio (optional) ────────────────────────────────────
# Sign up at twilio.com → Messaging → Try WhatsApp → scan sandbox QR
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
TWILIO_WHATSAPP_TO=whatsapp:+1XXXXXXXXXX

# ── Polling tuning (defaults are fine to start) ───────────────────────
CHECK_INTERVAL_MINUTES=15
BURST_CHECK_INTERVAL_SECONDS=10
BURST_WINDOW_MINUTES=60
REQUEST_DELAY_SECONDS=2
ALERT_COOLDOWN_MINUTES=30

# ── Quiet hours (no alerts between these hours, local TZ) ─────────────
QUIET_HOURS_START=0
QUIET_HOURS_END=0
QUIET_HOURS_TZ=America/New_York

# ── Resy power-user options ───────────────────────────────────────────
# Manual auth token — grab the resy_token cookie from your logged-in browser
# to skip the auto-login flow (avoids HTTP 419 rate limits entirely).
RESY_AUTH_TOKEN=
# Outbound proxy — useful when an IP gets banned by Resy
# HTTPS_PROXY=http://user:pass@host:port

# ── Observability ─────────────────────────────────────────────────────
SENTRY_DSN=
HEALTHZ_PORT=8090
```

### Conversational bot

You don't need slash commands. Talk to the bot in plain English:

```
add bungalow              → starts tracking
watch carbone any 4       → alert on any date, party 4
track ishq on 2026-06-15  → alert on a specific date
check tatiana             → one-off availability check
stop watching odo         → delete the watch
remove semma              → remove restaurant entirely
pause carbone             → temporarily mute
list / status / help      → bare-word commands
```

Slash commands still work for power users — `/help` shows the full grammar.

### Health monitoring

A tiny `/healthz` endpoint is exposed (default port 8090) for uptime monitors:

```bash
curl http://127.0.0.1:8090/healthz
{"ok":true,"uptime_s":293.5,"last_tick_age_s":3.6,"mongo_ok":true,"active_watches":6}
```

HTTP `200` = healthy, `503` = scheduler stuck or Mongo unreachable.

---

### Step 8 — Run the app

```bash
# Start the web UI (scheduler starts automatically inside)
python -m streamlit run app.py
```

Open **[http://localhost:8501](http://localhost:8501)** in your browser.

Or run the scheduler standalone without the UI:

```bash
python -m scheduler
```

---

### Step 9 — Add your first restaurant

**Via the web UI:**
1. Click the **Restaurants** tab
2. Paste a Resy or OpenTable URL (e.g. `https://resy.com/cities/ny/carbone`)
3. Hit **Add Restaurant**
4. Click the **Watches** tab → select your restaurant, date, party size → **Create Watch**

**Via Telegram bot** (run `python bot.py` first):
```
/add https://resy.com/cities/ny/carbone Carbone
/watch Carbone 2026-06-15 2
/watches
```

---

### Step 10 — Optional: set up WhatsApp alerts

1. Sign up at [twilio.com](https://twilio.com) — it's free
2. Go to **Messaging → Try it out → Send a WhatsApp message**
3. Scan the QR code with your phone and send the join code
4. Copy your Account SID + Auth Token from the Twilio console
5. Fill in the 4 `TWILIO_*` vars in `.env`
6. Restart — alerts now go to **both** Telegram and WhatsApp simultaneously

---

## Adding Watches

**Via Streamlit UI:**
1. Restaurants tab → paste URL → Add
2. Watches tab → pick restaurant, date, party size → Create watch

**Via Telegram bot:**
```
/add <restaurant URL>
/watch <restaurant name> <YYYY-MM-DD> <party size>
/list
/remove <restaurant name>
```

---

## How Detection Works

```
Normal cycle (every 30s tick)
  for each due watch:
    → try Resy/OT direct API  (fast JSON)
    → fallback: Playwright scraper
    → diff vs last slot count
    → positive delta → accelerate to burst 5min
    → slots found → Telegram alert

Burst cycle (every 10s)
  for each burst-mode watch:
    → same pipeline, faster cadence

Release monitor (every 5min)
  for each watch with a learned pattern:
    → release predicted within BURST_WINDOW_MINUTES?
    → yes → enter burst, send "dropping soon" alert
```

---

## Project Structure

```
app.py              — Streamlit UI
scheduler.py        — APScheduler jobs, main detection loop
api_client.py       — Resy + OpenTable REST clients
scraper.py          — BeautifulSoup + Playwright HTML parsing
database.py         — MongoDB CRUD layer
bot.py              — Telegram bot command handlers
alerts.py           — Telegram message formatting + send
release_learner.py  — Release time pattern detection
slot_cache.py       — In-process TTL cache + diff tracking
rate_limiter.py     — Per-domain token bucket
priority.py         — Adaptive per-watch poll intervals
metrics.py          — Event logging to MongoDB
config.py           — Env var loading + defaults
```

---

## Metrics

Open the **Metrics** tab in the UI to see:
- Event counts (cache hits/misses, fetches, alerts, bursts, slot deltas)
- Fetch latency p50/p95/max
- Recent raw events with timestamps

Events are retained 14 days then auto-pruned.

---

## Known Issues / Problems

### 🛑 Resy API blocked by reCAPTCHA + IP fingerprinting

**Status:** active blocker. Resy gates non-browser API access behind Google reCAPTCHA. Every `/4/find` call from a plain `requests.Session` returns `HTTP 419 Unauthorized` regardless of:
- valid auth token from a logged-in session
- IP rotation (tested via VPN — Toronto endpoint also blocked)
- header spoofing (Origin, Referer, User-Agent)
- cookie injection

The hardcoded public API key (`VbWk7s3L4KiK5fzlO7JD3Q5EYolEVsC`) appears to be on Resy's per-key blocklist after years of bot abuse.

**Current workaround:** Playwright headless Chromium fallback. Works (real browser passes detection) but slow — ~3 seconds per fetch even with persistent browser singleton. Tick latency for 6 watches: 34 seconds.

**Real fix in progress:** see "Under Development" below.

### ⚠️ Cold-start tick latency

With 6 active watches and Playwright fallback active, the first scheduler tick takes ~34 seconds because:
- 6 watches × 2 dates per range × ~3s Playwright fetch = 36s
- Subsequent ticks are instant (priority intervals dedupe re-checks)

Not a freeze — APScheduler handles it correctly with `max_instances=1 + coalesce=True`. Just slow first pass.

### Other limitations

- Release pattern learner needs at least 3 historical observations per restaurant before it makes predictions
- MongoDB must be running before startup — no offline degradation mode
- OpenTable `_login()` is a stub; API works for slot search but auto-book unimplemented
- WhatsApp via Twilio sandbox is approval-free but caps at 1 number; production needs Meta Business verification (~1 week)

---

## 🚀 Chrome Extension — the Resy bypass

The most reliable path to defeat Resy's bot detection is **a small Chrome extension that polls from your own logged-in session**.

Already built and shipped in this repo at [`extension/`](./extension/). See [`extension/README.md`](./extension/README.md) for full setup, but the short version:

1. Start the scheduler — it now also boots an ingest server at `http://127.0.0.1:8091/ingest`
2. Chrome → `chrome://extensions` → enable **Developer mode** → **Load unpacked** → pick the `extension/` folder
3. Sign in to https://resy.com in the same Chrome
4. Click the extension icon → Settings → paste a watches JSON like:
   ```json
   [{"venue_id": 5340, "slug": "carbone", "party_size": 2,
     "dates": ["2026-06-15", "2026-06-16"]}]
   ```
5. Hit **Start**. Every 30s the extension polls Resy with your real cookies and forwards slots to the scheduler. The existing diff / dedup / cooldown / Telegram / WhatsApp pipeline lights up exactly as before.

**Why it works:** the request originates from your real browser tab — Resy sees a logged-in user, never triggers reCAPTCHA, never returns 419. No proxy fees, no token theft, no IP bans.

**Trade-off:** Chrome must stay open. Fine for laptop/desktop use.

---

## Roadmap

- Auto-detect `auth_token` cookie in extension and inject as `X-Resy-Auth-Token` (enables booking, not just monitoring)
- OpenTable support inside the extension (different endpoint structure)
- Pull watch list from MongoDB instead of pasting JSON into the popup
- Move WhatsApp from Twilio sandbox to production (Meta Business verification, ~1 week)
- Replace Streamlit admin UI with FastAPI + HTMX for multi-tenancy
- iCal feed export so users can subscribe in their calendar

---

## Legal

This tool sends **alerts only**. It does not book, hold, or resell reservations. You receive a notification and book the slot yourself. Use responsibly and respect each platform's rate limits and terms of service.

---

## License

MIT
