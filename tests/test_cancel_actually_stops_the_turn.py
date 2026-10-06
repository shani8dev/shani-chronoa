"""Choosing "No, and stop this turn" has to stop the turn.

`permissions.decide()` files a `Decision.CANCEL` when the user picks it, and
`cancel_requested(action, resource)` existed to read it back — with **zero
callers** anywhere in `usr/` or `tests/`. So the option was offered, recorded,
and then **nothing happened**: the model saw a refusal and carried on with the
turn.

That is the same shape as `ALLOW_SESSION` being recorded and ignored, one level
up, and the label is the promise: an option that says "and stop this turn" while
the turn continues is worse than one that does not exist, because it teaches the
person to distrust the words on the buttons.

The gateway change of 2026-10-06 turned the option on there, which is what made
this reachable from a second direction.
"""

import asyncio
import json

import pytest


class TestCancelIsRecordedAndReadable:
    """The two halves of the decision layer, tested directly."""

    def test_nothing_is_cancelled_to_begin_with(self):
        from shani_chronoa import permissions

        permissions.clear()
        assert permissions.turn_cancelled() is False

    @pytest.mark.parametrize("choice,expected", [
        ("cancel", True),
        ("deny", False),      # "No, don't allow" refuses the call, not the turn
        ("allow_once", False),
    ])
    def test_only_cancel_counts(self, monkeypatch, choice, expected):
        from shani_chronoa import ask_bridge, permissions

        permissions.clear()
        monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)
        monkeypatch.setattr(ask_bridge, "ask",
                            lambda q, o, timeout=0.0: _answer(permissions, choice))
        try:
            permissions.decide("delete_file", "/tmp/x", "delete-file", "d",
                               offer_cancel=True)
            assert permissions.turn_cancelled() is expected, (
                f"{choice!r} recorded turn_cancelled()="
                f"{permissions.turn_cancelled()}, expected {expected}")
        finally:
            permissions.clear()

    def test_it_is_read_only_and_only_clear_forgets(self):
        """A later tool in the same turn must not un-cancel the turn."""
        from shani_chronoa import ask_bridge, permissions

        permissions.clear()
        monkey = pytest.MonkeyPatch()
        monkey.setattr(ask_bridge, "has_presenter", lambda: True)
        monkey.setattr(ask_bridge, "ask",
                       lambda q, o, timeout=0.0: _answer(permissions, "cancel"))
        try:
            permissions.decide("a", "r", "k", "d", offer_cancel=True)
            assert permissions.turn_cancelled() is True
            assert permissions.turn_cancelled() is True, "reading it cleared it"
        finally:
            monkey.undo()
            permissions.clear()
        assert permissions.turn_cancelled() is False, "clear() did not forget it"

    def test_a_refusal_on_another_resource_still_reads_as_the_turn(self):
        """The loop has the turn, not the (action, resource) key."""
        from shani_chronoa import ask_bridge, permissions

        permissions.clear()
        monkey = pytest.MonkeyPatch()
        monkey.setattr(ask_bridge, "has_presenter", lambda: True)
        monkey.setattr(ask_bridge, "ask",
                       lambda q, o, timeout=0.0: _answer(permissions, "cancel"))
        try:
            permissions.decide("delete_file", "/tmp/a", "delete-file", "d",
                               offer_cancel=True)
            # A different pair - the thing the assistant loop actually knows.
            assert permissions.turn_cancelled() is True
        finally:
            monkey.undo()
            permissions.clear()


def _answer(permissions, choice):
    return {"cancel": permissions.CANCEL_CHOICE if hasattr(permissions, "CANCEL_CHOICE")
            else "No, and stop this turn",
            "deny": permissions.DENY_CHOICE,
            "allow_once": permissions.ALLOW_ONCE_CHOICE}[choice]


class _LLM:
    """A backend that asks for one tool call, then would answer in prose."""

    def __init__(self):
        self.calls = 0

    async def chat_message(self, *a, **k):
        # **Awaited**, because `_ask` awaits it: a sync stub produced
        # `TypeError: 'dict' object can't be awaited`.
        self.calls += 1
        if self.calls == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "delete_file",
                              "arguments": json.dumps({"path": "/tmp/x"})}}]}
        return {"role": "assistant", "content": "I would have carried on."}


class TestTheLoopHonoursIt:
    """The real `Assistant`, a real tool call, and a real Cancel."""

    def _assistant(self, monkeypatch):
        from shani_chronoa import assistant as assistant_module
        from shani_chronoa import ask_bridge, permissions

        permissions.clear()
        llm = _LLM()
        monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)
        monkeypatch.setattr(ask_bridge, "ask",
                            lambda q, o, timeout=0.0: _answer(permissions, "cancel"))
        # The gated tool refuses, having recorded the Cancel - which is exactly
        # what `tools.dispatch` now does when the user picks that option.
        monkeypatch.setattr(assistant_module, "execute_tool",
                            lambda name, args: permissions.decide(
                                name, str(args.get("path")), "delete-file",
                                "delete a file", offer_cancel=True) and "deleted")
        return assistant_module.Assistant(llm), llm, permissions

    def test_the_turn_stops_after_one_model_call(self, monkeypatch):
        assistant, llm, permissions = self._assistant(monkeypatch)
        try:
            said = asyncio.run(assistant.handle("delete everything in downloads"))
        finally:
            permissions.clear()
        assert llm.calls == 1, (
            f"the model was called {llm.calls} times after the user said stop - "
            "the turn carried on, which is the defect")
        assert "stop" in str(said).lower(), said

    def test_the_conversation_stays_valid(self, monkeypatch):
        """A tool_call with no tool_result is what every backend here rejects."""
        assistant, _llm, permissions = self._assistant(monkeypatch)
        try:
            asyncio.run(assistant.handle("delete everything in downloads"))
            history = list(assistant.build_messages())
        finally:
            permissions.clear()
        ids = [m.get("tool_call_id") for m in history if m.get("role") == "tool"]
        assert ids, f"no tool result was recorded: {history}"
        assert all(i for i in ids), (
            f"a tool result has an empty tool_call_id: {history} - that is what "
            "every backend in this repo rejects")


class TestTheOptionIsActuallyOffered:
    """**Mutation C5 slipped through this gap, and this is the fix.**

    Every other test here patches `ask_bridge.ask` to *return* the Cancel answer,
    which bypasses `offer_cancel` entirely — so removing
    `offer_cancel=True` from `tools.dispatch` left all of them green while the
    option silently stopped being offered to anybody.

    So this asks the question the other tests cannot: **what does `dispatch` pass
    to `decide`?**
    """

    def test_dispatch_offers_cancel(self, monkeypatch):
        from shani_chronoa import permissions, tools

        seen = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            seen["args"] = args
            return permissions.Decision.ALLOW_ONCE

        monkeypatch.setattr(permissions, "can_ask", lambda: True)
        monkeypatch.setattr(permissions, "decide", spy)
        monkeypatch.setattr(permissions, "session_grant", lambda *a, **k: None)
        # A real tool name, with its consent key off, so the gate is reached.
        monkeypatch.setattr(tools, "_consent_key_for", lambda name: "delete-file")

        class Config:
            def get_bool(self, key, default=False):
                return False

        # `tools.py` imports it as `config as config_mod`, so the patch target is
        # the real module - `shani_chronoa.config_mod` does not exist, and my
        # first version raised ImportError rather than reaching the gate.
        monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", Config)
        # `execute_tool`, not `dispatch`: there is no public `dispatch` on this
        # module, so the first version called a name that does not exist and the
        # gate was never reached - caught by the assertion below, which is what it
        # is for.
        try:
            tools.execute_tool("delete_file", {"path": "/tmp/x"})
        except Exception:  # noqa: BLE001 - reaching the gate is what matters
            pass
        assert seen, ("the permission gate was never reached, so this test would "
                      "pass without proving anything")
        assert seen.get("offer_cancel") is True, (
            f"the tool path asked without offer_cancel (kwargs={sorted(seen)}), so "
            "'No, and stop this turn' is not on the buttons - and every other "
            "test here patches ask_bridge.ask directly and cannot see this")
