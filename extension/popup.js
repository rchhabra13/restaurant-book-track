/* popup.js — small UI controller for the TableWatch Bridge extension. */

const $ = (id) => document.getElementById(id);

function send(cmd, extra = {}) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ cmd, ...extra }, (resp) => resolve(resp || {}));
  });
}

function fmtTime(ts) {
  if (!ts) return "never";
  const d = new Date(ts);
  return d.toLocaleTimeString(undefined, { hour12: false });
}

async function refresh() {
  const s = await send("getStatus");
  if (!s.ok) return;

  $("status").textContent   = s.running ? "running" : "stopped";
  $("dot").classList.toggle("on", s.running);
  $("watches").textContent  = s.watches;
  $("interval").textContent = `${s.interval}s`;
  $("lastRun").textContent  = fmtTime(s.lastRun);
  $("toggle").textContent   = s.running ? "Stop" : "Start";
  $("err").textContent      = s.lastError || "";

  // Pre-fill settings inputs
  $("ingestUrl").value   = s.ingestUrl   || "";
  $("intervalSec").value = s.interval    || 30;

  // Watches JSON pre-fill
  const data = await chrome.storage.local.get(["watches", "config"]);
  $("watchesJson").value = JSON.stringify(data.watches || [], null, 2);
  $("apiKey").value      = (data.config || {}).apiKey || "";
}

$("toggle").addEventListener("click", async () => {
  const s = await send("getStatus");
  await send(s.running ? "stop" : "start");
  await refresh();
});

$("pollNow").addEventListener("click", async () => {
  await send("pollNow");
  setTimeout(refresh, 400);
});

$("save").addEventListener("click", async () => {
  const patch = {
    ingestUrl:   $("ingestUrl").value.trim(),
    apiKey:      $("apiKey").value.trim(),
    intervalSec: Math.max(30, parseInt($("intervalSec").value || "30", 10)),
  };
  await send("setConfig", { patch });

  let watches;
  try {
    watches = JSON.parse($("watchesJson").value || "[]");
    if (!Array.isArray(watches)) throw new Error("watches must be an array");
  } catch (exc) {
    $("err").textContent = `Invalid JSON: ${exc}`;
    return;
  }
  await send("setWatches", { watches });
  $("err").textContent = "Saved.";
  setTimeout(refresh, 300);
});

document.addEventListener("DOMContentLoaded", refresh);
setInterval(refresh, 4000);
