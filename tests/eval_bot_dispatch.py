"""
End-to-end bot evaluation — simulates real Telegram messages and verifies
the dispatcher routes them to the correct command handler with the right args.

No real Telegram network calls. We build a fake ``Update`` object that
captures replies, run ``handle_message``, then assert what was replied to
the user.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List

from tests._runner import Result, run_test


@dataclass
class _Reply:
    text: str
    parse_mode: str = ""


class _FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: List[_Reply] = []
    async def reply_text(self, text, parse_mode=None, **kw):
        self.replies.append(_Reply(text=text, parse_mode=str(parse_mode)))


class _FakeChat:
    def __init__(self, chat_id: str = "999999999"):
        self.id = chat_id


class _FakeUpdate:
    def __init__(self, text: str, chat_id: str = "999999999"):
        self.message = _FakeMessage(text)
        self.effective_chat = _FakeChat(chat_id)


class _FakeContext:
    args: list = field(default_factory=list)
    def __init__(self):
        self.args = []


def _send(text: str) -> _FakeUpdate:
    """Run handle_message against text, return Update with captured replies."""
    import bot
    upd = _FakeUpdate(text)
    ctx = _FakeContext()
    asyncio.run(bot.handle_message(upd, ctx))
    return upd


def run() -> list[Result]:
    results = []

    def assert_reply_contains(upd, substrings):
        assert upd.message.replies, "no reply was sent"
        text = upd.message.replies[-1].text
        for s in substrings:
            assert s.lower() in text.lower(), f"reply missing {s!r}: {text[:200]}"

    def t_list_command():
        upd = _send("list")
        # Should reach cmd_list — it always replies (empty or filled)
        assert upd.message.replies, "list command should reply"

    def t_help_command():
        upd = _send("help")
        assert_reply_contains(upd, ["how to talk", "natural phrases"])

    def t_status_command():
        upd = _send("status")
        assert_reply_contains(upd, ["status"])

    def t_unknown_falls_back():
        upd = _send("blah blah random nonsense")
        assert_reply_contains(upd, ["didn't catch"])

    def t_url_only_suggests_add():
        upd = _send("https://resy.com/cities/ny/test")
        # Either NL-parser intercepted as INTENT_ADD or fallback URL handler ran
        assert upd.message.replies, "URL should produce a reply"
        text = upd.message.replies[-1].text.lower()
        assert "add" in text or "track" in text

    def t_add_without_url_for_unknown_name_asks():
        upd = _send("add not-a-real-restaurant-xyz")
        # Should respond explaining a URL is needed
        text = upd.message.replies[-1].text.lower()
        assert "url" in text or "need" in text

    def t_watch_without_existing_restaurant():
        upd = _send("watch not-a-real-place-xyz any 2")
        text = upd.message.replies[-1].text.lower()
        assert "not" in text or "add it first" in text

    def t_remove_unknown_responds_gracefully():
        upd = _send("remove not-a-real-restaurant-xyz")
        # Should reply (with not-found error), not crash
        assert upd.message.replies, "remove on unknown should still reply"

    results.append(run_test("list → cmd_list",              t_list_command))
    results.append(run_test("help → cmd_help",              t_help_command))
    results.append(run_test("status → cmd_status",          t_status_command))
    results.append(run_test("gibberish → fallback help",    t_unknown_falls_back))
    results.append(run_test("URL → add suggestion",         t_url_only_suggests_add))
    results.append(run_test("add unknown → asks for URL",   t_add_without_url_for_unknown_name_asks))
    results.append(run_test("watch unknown → needs add",    t_watch_without_existing_restaurant))
    results.append(run_test("remove unknown → graceful",    t_remove_unknown_responds_gracefully))

    return results
