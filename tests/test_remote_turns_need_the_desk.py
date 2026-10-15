"""A message from a phone may not do at this computer what only the desk allowed.

Gateways (WhatsApp, Telegram, the phone link) turn a remote message into an
ordinary turn. A channel granted `execute` admits messages without asking, and a
switch like "Let Chronoa run commands" is turned on by someone *at the desk*.
Together they meant a message from a phone could run commands, edit or delete
files, or call external MCP tools with nobody watching. Now a remote turn's
risky call is asked about on screen every time - switch on or not, earlier
session grants ignored - and refused when nobody is there.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import permissions, tools  # noqa: E402


class _SwitchOn:
    def get_bool(self, key, default=False):
        return True


@pytest.fixture
def world(monkeypatch):
    asked = []

    def decide(action, resource, key, describe="", offer_cancel=False, preview=None, fresh=False):
        asked.append({"action": action, "describe": describe, "fresh": fresh})
        return None                    # "no", so nothing runs either way

    monkeypatch.setattr(permissions, "decide", decide)
    monkeypatch.setattr(tools.config_mod, "ChronoaConfig", lambda: _SwitchOn())
    permissions.clear()
    tools.set_current_turn("turn-1")
    yield asked
    tools.clear_current_turn()
    tools.set_current_channel("")
    permissions.clear()


def _text(result):
    return str(getattr(result, "text", result))


def test_remote_with_nobody_at_the_desk_is_refused(world, monkeypatch):
    monkeypatch.setattr(permissions, "can_ask", lambda: False)
    tools.set_current_channel("whatsapp")
    out = _text(tools.execute_tool("run_command", {"command": "true"}))
    assert "asked for from whatsapp" in out and "nobody is at the computer" in out, out
    assert world == []


def test_remote_asks_even_with_the_switch_on(world, monkeypatch):
    monkeypatch.setattr(permissions, "can_ask", lambda: True)
    tools.set_current_channel("telegram")
    out = _text(tools.execute_tool("run_command", {"command": "true"}))
    assert len(world) == 1, "a remote turn ran a command without asking at the desk"
    assert world[0]["fresh"] is True
    assert "telegram" in world[0]["describe"]
    assert "did not allow" in out


def test_at_the_desk_a_switch_that_is_on_still_means_on(world, monkeypatch):
    monkeypatch.setattr(permissions, "can_ask", lambda: True)
    tools.set_current_channel("")
    tools.execute_tool("run_command", {"command": "true"})
    assert world == [], "a local turn was asked although its switch is on"


def test_an_earlier_session_grant_does_not_answer_a_remote_turn(world, monkeypatch):
    monkeypatch.setattr(permissions, "can_ask", lambda: True)
    permissions.add_rule("run_command", "*", permissions.Decision.ALLOW_SESSION, session_only=True)
    tools.set_current_channel("phone")
    tools.execute_tool("run_command", {"command": "true"})
    assert len(world) == 1, "a desk-time session grant let a remote message run a command"


def test_fresh_questions_do_not_offer_this_session(monkeypatch):
    from shani_chronoa import ask_bridge
    seen = []
    monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)
    monkeypatch.setattr(ask_bridge, "ask", lambda q, options, **k: seen.append(options) or "")
    permissions.clear()
    # An ordinary tool, which *does* offer "this session" when not fresh -
    # so the comparison below can fail.
    assert not permissions.is_bypass_immune("take_screenshot")
    permissions.decide("take_screenshot", "*", "vision-sense-enabled")
    permissions.decide("take_screenshot", "*", "vision-sense-enabled", fresh=True)
    assert permissions.ALLOW_SESSION_CHOICE in seen[0], seen
    assert permissions.ALLOW_SESSION_CHOICE not in seen[1], seen


def test_the_assistant_tells_the_tools_which_channel_asked(monkeypatch):
    import asyncio
    from shani_chronoa.assistant import Assistant
    channels = []
    real = tools.set_current_channel

    def record(channel):
        channels.append(channel)
        return real(channel)

    monkeypatch.setattr(tools, "set_current_channel", record)

    class _LLM:
        async def chat_message(self, messages, tools=None, stream=False):
            return {"role": "assistant", "content": "ok"}

    asyncio.run(Assistant(_LLM()).handle("hi", channel="whatsapp"))
    assert channels[:1] == ["whatsapp"] and channels[-1] == "", channels


def test_decide_itself_asks_again_when_fresh(monkeypatch):
    """`decide()` has its own "already allowed this session" shortcut; a fresh
    question must go past it to the person, not answer from the record."""
    from shani_chronoa import ask_bridge
    asked = []
    monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)
    monkeypatch.setattr(ask_bridge, "ask",
                        lambda q, options, **k: asked.append(q) or permissions.ALLOW_ONCE_CHOICE)
    permissions.clear()
    try:
        permissions.add_rule("take_screenshot", "*", permissions.Decision.ALLOW_SESSION,
                             session_only=True)
        assert permissions.decide("take_screenshot", "*", "vision-sense-enabled") == \
            permissions.Decision.ALLOW_SESSION and asked == [], "precondition: the shortcut exists"
        permissions.decide("take_screenshot", "*", "vision-sense-enabled", fresh=True)
        assert len(asked) == 1, "a fresh question was answered from an old session grant"
    finally:
        permissions.clear()
