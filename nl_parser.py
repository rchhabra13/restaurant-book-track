"""
Natural-language intent parser for the Telegram bot.

No LLM, no external API. Regex + keyword matching against a known intent
grammar. Returns a structured ``Intent`` that the bot can dispatch to its
existing command handlers.

Examples
────────
  "add bungalow"                     → INTENT_ADD          name="bungalow"
  "track carbone for next week"      → INTENT_WATCH        name="carbone" date="any"
  "watch ishq on 2026-06-15 for 4"   → INTENT_WATCH        name="ishq" date="2026-06-15" party=4
  "check carbone"                    → INTENT_CHECK        name="carbone"
  "remove tatiana"                   → INTENT_REMOVE       name="tatiana"
  "stop watching odo"                → INTENT_UNWATCH      name="odo"
  "what am I tracking"               → INTENT_LIST
  "status" / "dashboard"             → INTENT_STATUS
  "help"                             → INTENT_HELP

Returns ``Intent(kind="unknown")`` when no pattern matches. Caller can then
fall back to the existing "I didn't understand, type /help" reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ── Intent kinds ──────────────────────────────────────────────────────
INTENT_ADD      = "add"
INTENT_WATCH    = "watch"
INTENT_CHECK    = "check"
INTENT_REMOVE   = "remove"
INTENT_UNWATCH  = "unwatch"
INTENT_LIST     = "list"
INTENT_WATCHES  = "watches"
INTENT_STATUS   = "status"
INTENT_HELP     = "help"
INTENT_PAUSE    = "pause"
INTENT_RESUME   = "resume"
INTENT_UNKNOWN  = "unknown"


@dataclass
class Intent:
    kind:       str
    name:       str = ""
    url:        str = ""
    date:       str = ""        # "YYYY-MM-DD" or "any"
    date_from:  str = ""
    date_to:    str = ""
    party_size: int = 2
    raw:        str = ""        # original input
    confidence: float = 0.0     # 0..1, for caller to gate on


# Pre-compiled patterns. Order matters — first match wins.

_URL_RE   = re.compile(r"(https?://[^\s]+)", re.I)
_DATE_RE  = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_PARTY_RE = re.compile(r"\b(?:for|party of|party)\s+(\d+)\b", re.I)
_PARTY_FALLBACK = re.compile(r"\b(\d+)\s*(?:people|ppl|persons|guests)\b", re.I)
# Trailing bare digit — only used when no explicit party phrase
_PARTY_TRAILING = re.compile(r"\s(\d{1,2})\s*$")

# Verbs grouped by intent
_ADD_VERBS    = r"(?:add|new|create|track|monitor|start watching|start tracking)"
_WATCH_VERBS  = r"(?:watch|track|monitor)"
_CHECK_VERBS  = r"(?:check|any slots? at|is there anything at|are there slots? for)"
_REMOVE_VERBS = r"(?:remove|delete|forget|drop)"
_UNWATCH_VERBS = r"(?:unwatch|stop watching|stop tracking|untrack)"
_PAUSE_VERBS  = r"(?:pause)"
_RESUME_VERBS = r"(?:resume|unpause)"

# Compiled per-intent patterns
_PATTERNS = [
    # "stop watching X" — handle before generic remove so "stop watching" isn't lost
    (INTENT_UNWATCH, re.compile(
        rf"^\s*{_UNWATCH_VERBS}\s+(?P<name>.+?)\s*[.?!]?\s*$", re.I)),

    # "remove X" / "delete X"
    (INTENT_REMOVE, re.compile(
        rf"^\s*{_REMOVE_VERBS}\s+(?P<name>.+?)(?:\s+from\s+(?:my\s+)?list)?\s*[.?!]?\s*$", re.I)),

    # "watch X on DATE for N" / "watch X any 4"
    (INTENT_WATCH, re.compile(
        rf"^\s*{_WATCH_VERBS}\s+(?P<name>.+?)(?:\s+(?:on|for))?\s*$", re.I)),

    # "check X"
    (INTENT_CHECK, re.compile(
        rf"^\s*{_CHECK_VERBS}\s+(?P<name>.+?)\s*[.?!]?\s*$", re.I)),

    # "pause X" / "resume X"
    (INTENT_PAUSE,  re.compile(rf"^\s*{_PAUSE_VERBS}\s+(?P<name>.+?)\s*$",  re.I)),
    (INTENT_RESUME, re.compile(rf"^\s*{_RESUME_VERBS}\s+(?P<name>.+?)\s*$", re.I)),

    # "add X" / "track X" — generic add (matched after more specific watch verbs)
    (INTENT_ADD, re.compile(
        rf"^\s*{_ADD_VERBS}\s+(?P<name>.+?)(?:\s+to\s+(?:my\s+|the\s+)?list)?\s*[.?!]?\s*$", re.I)),
]

# Bare-word intents (no name needed)
_BARE_PATTERNS = [
    (INTENT_LIST,    re.compile(r"^\s*(list|show|my restaurants|what(?:'s| is) in my list)\s*[.?!]?\s*$", re.I)),
    (INTENT_WATCHES, re.compile(r"^\s*(watches|my watches|what(?:'m| am) i (?:watch|track)ing)\s*[.?!]?\s*$", re.I)),
    (INTENT_STATUS,  re.compile(r"^\s*(status|dashboard|how(?:'re| are) (?:we|things)|overview)\s*[.?!]?\s*$", re.I)),
    (INTENT_HELP,    re.compile(r"^\s*(help|what can you do|commands)\s*[.?!]?\s*$", re.I)),
]


def _extract_party(text: str) -> Optional[int]:
    m = _PARTY_RE.search(text) or _PARTY_FALLBACK.search(text) or _PARTY_TRAILING.search(text)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 20:
                return n
        except ValueError:
            pass
    return None


def _extract_date(text: str) -> tuple[str, str, str, str]:
    """Return (date_mode, target, date_from, date_to). target is '' if not single."""
    m = _DATE_RE.findall(text)
    if not m:
        if re.search(r"\b(any\s+date|any\s+day|any\b|whenever|next\s+(?:week|month))\b",
                     text, re.I):
            return "any", "any", "", ""
        return "", "", "", ""
    if len(m) == 1:
        return "single", m[0], "", ""
    return "range", f"{m[0]}:{m[1]}", m[0], m[1]


def _strip_trailing_modifiers(name: str) -> str:
    """Remove trailing date/party/keyword cruft. Idempotent — runs twice for nesting."""
    for _ in range(2):  # two passes catch nested cruft like "any 4"
        name = _DATE_RE.sub("", name)
        name = re.sub(r"\b(?:on|for|party of|party|next\s+week|next\s+month|any\s+date|whenever)\b.*$",
                      "", name, flags=re.I)
        name = re.sub(r"\s+any(?:\s+date|\s+day)?\s*$", "", name, flags=re.I)
        name = re.sub(r"\s+to\s+(?:my\s+|the\s+)?list\s*$", "", name, flags=re.I)
        name = re.sub(r"\s+(?:1?\d|20)\s*$", "", name)
        name = re.sub(r"\s+\d+\s*(?:people|ppl|persons|guests)\s*$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" ,.?!")


def parse(text: str) -> Intent:
    """Best-effort intent extraction. Always returns an Intent (kind may be 'unknown')."""
    raw = text.strip()

    # URL-only message → suggest add
    url_match = _URL_RE.search(raw)
    if url_match and len(raw.split()) <= 3:
        return Intent(kind=INTENT_ADD, url=url_match.group(1),
                      raw=raw, confidence=0.95)

    # Bare commands
    for kind, pat in _BARE_PATTERNS:
        if pat.match(raw):
            return Intent(kind=kind, raw=raw, confidence=0.9)

    # Verb-based intents
    for kind, pat in _PATTERNS:
        m = pat.match(raw)
        if not m:
            continue
        name = _strip_trailing_modifiers(m.group("name"))
        if not name:
            continue
        party = _extract_party(raw) or 2
        date_mode, target, dfrom, dto = _extract_date(raw)
        url = url_match.group(1) if url_match else ""

        # Confidence: higher when more structure detected
        conf = 0.55
        if date_mode:           conf += 0.15
        if party != 2:          conf += 0.10
        if url:                 conf += 0.10
        if kind in (INTENT_ADD, INTENT_WATCH) and len(name) >= 3:
            conf += 0.10

        return Intent(
            kind=kind, name=name, url=url, raw=raw,
            date=target if date_mode == "single" else (date_mode or ""),
            date_from=dfrom, date_to=dto, party_size=party,
            confidence=min(conf, 0.99),
        )

    return Intent(kind=INTENT_UNKNOWN, raw=raw, confidence=0.0)
