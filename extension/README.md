# TableWatch Bridge — Chrome Extension

Polls Resy from **your own logged-in Chrome session** and forwards open slots to your local TableWatch scheduler.

## Why this exists

Resy gates server-side scrapers behind reCAPTCHA + IP fingerprinting (see the project's main README, *Known Issues*). The only reliable way to call `/4/find` is from a real browser tab that's already authenticated.

This extension is that real browser tab. It runs as a background service worker, hits Resy's API every 30s using your cookies, and POSTs the results to `http://127.0.0.1:8091/ingest`. The Python scheduler reuses its existing diff detection, dedup, cooldown, Telegram + WhatsApp pipeline — the extension just feeds it data.

## What you get

- ✅ No 419 ever — Resy sees a real logged-in user
- ✅ No proxies / VPN / paid services
- ✅ Sub-30-second detection latency
- ✅ Your existing watches keep working (Telegram alerts, range mode, etc.)

Trade-off: your Chrome must be running. Fine for a laptop that stays on.

## Install (5 minutes)

### 1. Start the scheduler

The ingest server is bundled into `scheduler.py` — just run it as normal:

```bash
python -m scheduler
# or
python -m streamlit run app.py
```

You should see:

```
INFO Ingest server listening on http://127.0.0.1:8091/ingest
```

### 2. Load the extension

1. Open Chrome → **`chrome://extensions`**
2. Toggle **Developer mode** (top-right) ON
3. Click **Load unpacked**
4. Select the `extension/` directory from this repo
5. Pin the extension to your toolbar (puzzle icon → pin TableWatch Bridge)

### 3. Sign in to Resy

Visit **https://resy.com** and log in normally. The extension uses your browser cookies, so as long as you're logged in there, it works.

### 4. Configure watches in the popup

Click the extension icon → **Settings** (collapsible). Paste your watches as JSON:

```json
[
  { "venue_id": 5340, "slug": "carbone", "party_size": 2,
    "dates": ["2026-06-15", "2026-06-16"] },
  { "venue_id": 12345, "slug": "ambassadors-clubhouse-new-york",
    "party_size": 2, "dates": ["2026-06-20"] }
]
```

**Finding venue_id:** while on a Resy restaurant page, open DevTools → Network → filter `find` → click the request → look at the URL for `venue_id=...`.

### 5. Start polling

Hit the **Start** button. The dot turns green. You'll see "Last poll" tick every 30s.

### 6. Watch your Telegram

When a new slot appears, the existing pipeline alerts you exactly as before — same dedup, same cooldown, same WhatsApp delivery.

## Configuration

In the popup → Settings:

| Field | Default | Notes |
|---|---|---|
| Ingest URL | `http://127.0.0.1:8091/ingest` | Where to POST results |
| Resy API key | `VbWk7s3L4KiK5fzlO7JD3Q5EYolEVsC` | Resy's public key; replace if banned |
| Poll interval | `30` seconds | Chrome's `chrome.alarms` minimum is 30s |
| Watches | `[]` JSON | See format above |

## Security

- Default ingest URL is `127.0.0.1` (loopback only — no LAN exposure)
- Optionally set `INGEST_TOKEN=somesecret` in the scheduler `.env` and add `X-Token: somesecret` to extension requests (planned)
- Extension stores config in `chrome.storage.local` — never leaves your machine

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "Ingest failed: TypeError" | Scheduler not running on port 8091 | Start `python -m scheduler` |
| Polls run but no Telegram alert | venue_id doesn't match a Mongo watch | Check `restaurant.venue_id` in DB matches popup config |
| 401 from Resy | Logged out | Refresh https://resy.com, sign in again |
| Extension stops after laptop sleep | Service worker suspended | Click extension icon to wake it; alarm resumes |

## Limits

- Chrome `chrome.alarms` API enforces a 30-second minimum interval for unpacked extensions in dev mode (60s for production / Chrome Web Store)
- Each tick polls every `(venue, date)` combo serially in the browser — keep total watches ≲ 50 for sub-30s coverage
- Browser must stay open

## Roadmap (this extension)

- [ ] Auto-detect logged-in `auth_token` cookie and inject as `X-Resy-Auth-Token` header (lets you book, not just see)
- [ ] OpenTable support (different endpoint structure)
- [ ] Pull watch list from the scheduler's Mongo instead of pasting JSON
- [ ] Optional `INGEST_TOKEN` shared-secret auth
- [ ] Adaptive interval (faster polling near a known release window)

## Files

```
extension/
  manifest.json   — Manifest v3 declaration
  background.js   — Service worker; poll loop + Resy fetch + ingest POST
  popup.html      — UI shown when you click the toolbar icon
  popup.js        — UI controller (start/stop/save settings)
  icons/          — 16/48/128 PNG placeholders
```
