/*
 * TableWatch Bridge — background service worker
 *
 * Polls Resy's /4/find endpoint at INTERVAL_SEC using the browser's own
 * cookies and credentials. Because the request originates from a real
 * logged-in Chrome session, Resy's reCAPTCHA / 419 bot-detection never
 * triggers.
 *
 * Discovered slot lists are POSTed to the local TableWatch scheduler at
 * INGEST_URL where the existing diff/alert/notify pipeline picks them up.
 */

const ALARM_NAME   = "tw-poll";
const STORAGE_KEYS = {
  watches:    "watches",      // [{venue_id, slug, party_size, dates: [...]}]
  config:     "config",       // {ingestUrl, apiKey, intervalSec, running}
  lastError:  "lastError",
  lastRun:    "lastRun",
};

const DEFAULT_CONFIG = {
  ingestUrl:   "http://127.0.0.1:8091/ingest",
  apiKey:      "VbWk7s3L4KiK5fzlO7JD3Q5EYolEVsC", // Resy public web key — extracted at runtime if possible
  intervalSec: 30,
  running:     false,
};

// ──────────────────────────────────────────────────────────────────────
// Storage helpers
// ──────────────────────────────────────────────────────────────────────

async function getConfig() {
  const data = await chrome.storage.local.get(STORAGE_KEYS.config);
  return { ...DEFAULT_CONFIG, ...(data[STORAGE_KEYS.config] || {}) };
}

async function setConfig(patch) {
  const cur = await getConfig();
  await chrome.storage.local.set({ [STORAGE_KEYS.config]: { ...cur, ...patch } });
}

async function getWatches() {
  const data = await chrome.storage.local.get(STORAGE_KEYS.watches);
  return data[STORAGE_KEYS.watches] || [];
}

async function setWatches(watches) {
  await chrome.storage.local.set({ [STORAGE_KEYS.watches]: watches });
}

async function setStatus({ lastError = null, lastRun = null } = {}) {
  const patch = {};
  if (lastError !== null) patch[STORAGE_KEYS.lastError] = lastError;
  if (lastRun   !== null) patch[STORAGE_KEYS.lastRun]   = lastRun;
  if (Object.keys(patch).length) await chrome.storage.local.set(patch);
}

// ──────────────────────────────────────────────────────────────────────
// Resy fetch — runs in the browser's own session
// ──────────────────────────────────────────────────────────────────────

async function fetchResySlots({ apiKey, venueId, day, partySize }) {
  const url = new URL("https://api.resy.com/4/find");
  url.search = new URLSearchParams({
    lat: "0", long: "0", day, party_size: String(partySize),
    venue_id: String(venueId),
  }).toString();

  const resp = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: {
      "Authorization": `ResyAPI api_key="${apiKey}"`,
      "X-Origin":  "https://resy.com",
      "Accept":    "application/json, text/plain, */*",
    },
  });

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  }
  const json = await resp.json();
  return parseResySlots(json, partySize);
}

function parseResySlots(json, partySize) {
  const slots = [];
  const venues = json?.results?.venues || [];
  for (const v of venues) {
    for (const raw of v.slots || []) {
      const start  = raw?.date?.start || "";
      const time   = parseResyTime(start);
      const config = raw?.config || {};
      if (time) {
        slots.push({
          time,
          extra:      config.type || "",
          config_id:  config.id,
          config_token: config.token,
          party_size: partySize,
          platform:   "resy",
        });
      }
    }
  }
  return slots;
}

function parseResyTime(start) {
  // Resy returns "2026-06-15 19:30:00"
  const match = start.match(/(\d{2}):(\d{2})/);
  if (!match) return "";
  let h = parseInt(match[1], 10);
  const m = match[2];
  const period = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${h}:${m} ${period}`;
}

// ──────────────────────────────────────────────────────────────────────
// Ingest — forward to local scheduler
// ──────────────────────────────────────────────────────────────────────

async function postSlots(ingestUrl, payload) {
  const resp = await fetch(ingestUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    throw new Error(`Ingest HTTP ${resp.status}`);
  }
}

// ──────────────────────────────────────────────────────────────────────
// Poll loop
// ──────────────────────────────────────────────────────────────────────

async function pollOnce() {
  const cfg = await getConfig();
  const watches = await getWatches();
  if (!cfg.running || watches.length === 0) return;

  const results = [];
  for (const w of watches) {
    for (const day of w.dates || []) {
      try {
        const slots = await fetchResySlots({
          apiKey:    cfg.apiKey,
          venueId:   w.venue_id,
          day,
          partySize: w.party_size,
        });
        results.push({
          venue_id: w.venue_id, slug: w.slug,
          date: day, party_size: w.party_size,
          slots, ok: true,
        });
      } catch (exc) {
        results.push({
          venue_id: w.venue_id, slug: w.slug,
          date: day, party_size: w.party_size,
          slots: [], ok: false, error: String(exc),
        });
      }
    }
  }

  try {
    await postSlots(cfg.ingestUrl, {
      source: "extension",
      ts:     Date.now(),
      results,
    });
    await setStatus({ lastRun: Date.now(), lastError: "" });
  } catch (exc) {
    await setStatus({ lastError: `Ingest failed: ${exc}` });
  }
}

// ──────────────────────────────────────────────────────────────────────
// Alarms — chrome.alarms is preferred over setInterval in MV3
// ──────────────────────────────────────────────────────────────────────

async function startPolling() {
  const cfg = await getConfig();
  await setConfig({ running: true });
  await chrome.alarms.clear(ALARM_NAME);
  // Minimum is 0.5min — for sub-30s you'd need to chain alarms or use offscreen docs
  const periodMinutes = Math.max(0.5, cfg.intervalSec / 60);
  await chrome.alarms.create(ALARM_NAME, { periodInMinutes: periodMinutes });
  pollOnce(); // run immediately
}

async function stopPolling() {
  await setConfig({ running: false });
  await chrome.alarms.clear(ALARM_NAME);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) pollOnce();
});

// Restart polling on extension reload if it was running
chrome.runtime.onStartup.addListener(async () => {
  const cfg = await getConfig();
  if (cfg.running) startPolling();
});
chrome.runtime.onInstalled.addListener(async () => {
  await setConfig({}); // ensure defaults exist
});

// ──────────────────────────────────────────────────────────────────────
// Message handlers — invoked from popup.js
// ──────────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg.cmd) {
        case "start":     await startPolling();      sendResponse({ ok: true }); break;
        case "stop":      await stopPolling();       sendResponse({ ok: true }); break;
        case "pollNow":   await pollOnce();          sendResponse({ ok: true }); break;
        case "getStatus": {
          const cfg = await getConfig();
          const w   = await getWatches();
          const st  = await chrome.storage.local.get([STORAGE_KEYS.lastRun, STORAGE_KEYS.lastError]);
          sendResponse({
            ok: true,
            running:   cfg.running,
            ingestUrl: cfg.ingestUrl,
            interval:  cfg.intervalSec,
            watches:   w.length,
            lastRun:   st[STORAGE_KEYS.lastRun] || null,
            lastError: st[STORAGE_KEYS.lastError] || null,
          });
          break;
        }
        case "setConfig":  await setConfig(msg.patch);   sendResponse({ ok: true }); break;
        case "setWatches": await setWatches(msg.watches); sendResponse({ ok: true }); break;
        default: sendResponse({ ok: false, error: `unknown cmd ${msg.cmd}` });
      }
    } catch (exc) {
      sendResponse({ ok: false, error: String(exc) });
    }
  })();
  return true; // async response
});
