# CLAUDE.md

Behavioral guidelines and project context for the Restaurant Booking Tracker.

---

## General Coding Guidelines

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## Project Context

### What This Is
Restaurant reservation availability monitor. Watches configured restaurants on Resy, OpenTable, Yelp, and generic sites. Detects open slots, sends Telegram alerts, optionally auto-books via Playwright.

### Stack
- **UI**: Streamlit (`app.py`)
- **Database**: MongoDB via PyMongo (`database.py`)
- **Scraping**: BeautifulSoup + Playwright (`scraper.py`)
- **API clients**: Direct Resy/OpenTable REST APIs (`api_client.py`)
- **Scheduling**: APScheduler background jobs (`scheduler.py`)
- **Notifications**: Telegram Bot API (`alerts.py`, `bot.py`)
- **Auto-booking**: Playwright automation (`booker.py`)
- **Pattern learning**: Statistical release time detection (`release_learner.py`)

### Key Architecture Decisions
- `scheduler.py` uses `_burst_watch_ids` (protected by `_burst_lock: threading.Lock()`) to track watches in burst mode (10s polling). Always acquire this lock when reading or writing the set.
- All Telegram messages use HTML parse mode. Escape user-controlled data with `_esc()` (wraps `html.escape`) before inserting into message templates.
- MongoDB watches collection has a unique compound index on `(restaurant_id, target_date, party_size, time_preference, chat_id)`. `add_watch()` returns the existing ID on duplicate — don't bypass this.
- Screenshots in `booker.py` use `tempfile.mkstemp()` with `chmod 600` — never use predictable `/tmp/` paths for booking screenshots.
- robots.txt fetch errors are fail-closed (`allow_all = False`) — don't change this to allow-all.
- `RESY_API_KEY` lives in `config.py` / `.env`, not hardcoded in source.

### Running the App
```bash
streamlit run app.py
# or
./run.sh
```

### Environment Variables
Copy `.env.example` to `.env`. Required for full functionality:
- `MONGO_URI` — MongoDB connection string
- `TELEGRAM_BOT_TOKEN` — alerts won't work without this (startup warning logged)
- `TELEGRAM_CHAT_ID` — default fallback chat

Optional (for auto-booking):
- `RESY_EMAIL`, `RESY_PASSWORD`
- `OPENTABLE_EMAIL`, `OPENTABLE_PASSWORD`
- `BOOKING_NAME`, `BOOKING_EMAIL`, `BOOKING_PHONE`
- `AUTO_BOOK_ENABLED=true`

### No Tests
There is no test suite. When making changes, verify manually: run the app, add a restaurant, create a watch, trigger a check.
