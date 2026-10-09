"""A refusal the user cannot act on is not consent, it is a dead end.

Until this, a gated tool whose consent key was off simply returned "the vision
sense is turned off (enable 'vision-sense-enabled')". That is honest and it is
correct - but it leaves the only real decision with nobody able to make it. The
user could go and open Settings, or accept that this capability is off.

mini-swe-agent asks before running anything irreversible, and puts the ask where
the decision is actually made rather than in a prompt the model can talk past.
The same shape here: when a gate is shut *and* someone is present to answer, ask
them, once, and record what they said.

The two failure modes this module exists to prevent are both quiet:

- Asking when nobody can answer, which hangs the turn until a timeout.
- Replacing a useful refusal with a bare "the user did not allow", when there
  was no user - losing the one line that told them which setting to turn on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ask_bridge, permissions  # noqa: E402
from shani_chronoa.tools import execute_tool_outcome  # noqa: E402


def _presenter(choice: str, seen: list | None = None):
    """A presenter that answers `choice` immediately, recording the question."""
    def show(question, options):
        if seen is not None:
            seen.append((question, list(options)))
        done, resolve = ask_bridge.make_event()
        resolve(choice)
        return done
    return show


@pytest.fixture(autouse=True)
def a_screen_capture_tool(tmp_path, monkeypatch):
    """A `grim` that writes a real 2x2 PNG to stdout, first on PATH.

    These tests are about the approval flow, and they use `screenshot` as the
    gated tool. Without a capture tool on the host, "allow once" correctly ran
    the tool and the tool correctly said "no Wayland screen capture tool is
    installed" - so the two allow tests failed on any machine without grim or
    gnome-screenshot, for a reason that has nothing to do with permissions.
    An executable rather than a monkeypatch, because the skill runs in a
    sandboxed child that inherits PATH and not this process's patches.
    """
    import struct
    import zlib

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00" + b"\xff\x00\x00" * 2 + b"\x00" + b"\x00\xff\x00" * 2))
           + chunk(b"IEND", b""))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "image.png").write_bytes(png)
    tool = bin_dir / "grim"
    tool.write_text(f"#!/bin/sh\nexec cat '{bin_dir / 'image.png'}'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ.get('PATH', '')}")
    # **And a display, because the tool is never reached without one.**
    # `screengrab.capture_screen` calls `display_environment()` first and
    # raises "there is no display to capture" when neither `WAYLAND_DISPLAY`
    # nor `DISPLAY` is set - so on a headless runner the fake `grim` above is
    # never executed and the two allow tests fail with *"no display"*, which
    # is about the machine and not about permissions at all.
    #
    # This is why they were on the environmental list as *"no Wayland
    # screen-capture tool"* when the actual cause was one level earlier and
    # fixable here: supplying the *tool* does not supply the *display*.
    #
    # `WAYLAND_DISPLAY` first, because `display_environment()` prefers it -
    # setting only `DISPLAY` on a Wayland desktop would make the code capture
    # the XWayland root, which is the mistake that function's own docstring
    # warns about, and it would do it in this test.
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")


@pytest.fixture(autouse=True)
def clean_state():
    permissions.clear()
    ask_bridge.set_presenter(None)
    yield
    permissions.clear()
    ask_bridge.set_presenter(None)


class TestNobodyToAsk:
    def test_a_headless_run_refuses_without_asking(self):
        ask_bridge.set_presenter(None)
        out = execute_tool_outcome("screenshot", {}).text
        assert "not permitted" in out.lower() or "turned off" in out.lower()

    def test_headless_keeps_the_refusal_that_names_the_setting(self):
        """The bare "the user did not allow" loses the only actionable line.

        There was no user, so there is nothing to allow - reporting otherwise
        invents an actor, and throws away the instruction to turn the key on.
        """
        ask_bridge.set_presenter(None)
        out = execute_tool_outcome("screenshot", {}).text
        assert "vision-sense-enabled" in out, (
            "the headless refusal no longer names the consent key, so the user "
            "has no way to act on it"
        )

    def test_can_ask_is_false_without_a_presenter(self):
        ask_bridge.set_presenter(None)
        assert permissions.can_ask() is False


class TestAllowing:
    def test_allow_once_runs_the_tool(self):
        ask_bridge.set_presenter(
            _presenter(permissions.ALLOW_ONCE_CHOICE))
        assert "Screenshot saved" in execute_tool_outcome("screenshot", {}).text

    def test_allow_once_does_not_leak_to_the_next_call(self):
        ask_bridge.set_presenter(_presenter(permissions.ALLOW_ONCE_CHOICE))
        execute_tool_outcome("screenshot", {})
        ask_bridge.set_presenter(None)
        second = execute_tool_outcome("screenshot", {}).text
        assert "not permitted" in second.lower() or "turned off" in second.lower()

    def test_allow_session_survives_the_prompt_gone(self):
        ask_bridge.set_presenter(
            _presenter(permissions.ALLOW_SESSION_CHOICE))
        first = execute_tool_outcome("screenshot", {}).text
        ask_bridge.set_presenter(None)
        second = execute_tool_outcome("screenshot", {}).text
        assert "Screenshot saved" in first
        assert "Screenshot saved" in second, (
            "a session grant evaporated once the presenter was removed"
        )


class TestRefusing:
    def test_an_explicit_no_does_not_run_the_tool(self):
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE))
        out = execute_tool_outcome("screenshot", {}).text
        assert "did not allow" in out
        assert "Screenshot saved" not in out

    def test_a_dismissed_prompt_counts_as_no(self):
        """Dismissed, timed out, and "no" are the same answer: nobody said yes."""
        ask_bridge.set_presenter(_presenter(""))
        out = execute_tool_outcome("screenshot", {}).text
        assert "did not allow" in out

    def test_a_refusal_is_not_asked_again(self):
        """The tool loop retries. A repeated question gets clicked through."""
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE))
        execute_tool_outcome("screenshot", {})
        seen: list = []
        ask_bridge.set_presenter(
            _presenter(permissions.ALLOW_SESSION_CHOICE, seen))
        execute_tool_outcome("screenshot", {})
        assert seen == [], (
            f"the same refusal was put to the user {len(seen)} more time(s) "
            f"after they had already answered it"
        )


class TestWhatThePromptSays:
    def test_it_names_the_tool_and_the_consent_key(self):
        seen: list = []
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE, seen))
        execute_tool_outcome("screenshot", {})
        assert seen, "nothing was asked at all"
        question, options = seen[0]
        assert "screenshot" in question.lower()
        assert "vision-sense-enabled" in question
        # **Membership and order, not an exact list.** The exact list stopped
        # being true on 2026-10-06 when `tools.dispatch` began passing
        # `offer_cancel=True`, which appends "No, and stop this turn" - and the
        # failure was correct: the option should now be offered. Asserting the
        # whole list would have made adding it look like a regression.
        #
        # What this test is actually for is that the prompt *names the tool and
        # its consent key*, so that is still asserted above; here the claims are
        # that both allow choices come first and that a refusal is on offer.
        assert options[:2] == [permissions.ALLOW_ONCE_CHOICE,
                               permissions.ALLOW_SESSION_CHOICE], options
        assert permissions.DENY_CHOICE in options, options
        assert len(options) >= 3, options

    def test_the_refusal_is_offered_as_an_option_not_the_default(self):
        """Ordering carries meaning: the two allow choices come first, but the
        outcome is identical for a dismissal, so nothing is granted by default
        and the caller must treat a non-match as no."""
        seen: list = []
        ask_bridge.set_presenter(_presenter("", seen))
        execute_tool_outcome("screenshot", {})
        options = seen[0][1]
        # **"not the default" was never about the index.** The claim is that a
        # dismissed prompt grants nothing, and that every refusal-shaped option
        # sits after every allow-shaped one. The last index used to hold the
        # refusal; it now holds "No, and stop this turn", which is if anything
        # more conservative, so an index assertion would have inverted the test.
        assert options[0] == permissions.ALLOW_ONCE_CHOICE, options
        refusals = {permissions.DENY_CHOICE, permissions.CANCEL_CHOICE}
        first_refusal = min(options.index(o) for o in refusals if o in options)
        assert set(options[:first_refusal]) == {
            permissions.ALLOW_ONCE_CHOICE, permissions.ALLOW_SESSION_CHOICE}, options
        assert refusals & set(options), options


class TestDecideRecordsWhatItWasTold:
    def test_allow_once_is_recorded_as_a_session_grant(self):
        ask_bridge.set_presenter(_presenter(permissions.ALLOW_ONCE_CHOICE))
        execute_tool_outcome("screenshot", {})
        assert permissions.session_grant("screenshot", "*") is None, (
            "the one-shot grant was not consumed by being used"
        )

    def test_a_refusal_is_recorded_so_the_question_stays_answered(self):
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE))
        execute_tool_outcome("screenshot", {})
        assert permissions.evaluate("screenshot", "*") == \
            permissions.Decision.DENY_SESSION


class TestTheOptionsMeanWhatTheySay:
    """**"Allow for this session" asked again on the very next call.**

    `decide()` short-circuited on a rule already on record, but the set it
    checked was only the three *refusals*. A session **grant** was written to
    `_grants` and then never read, so choosing the option recorded a decision
    that changed nothing and put the same question back on screen immediately.

    Measured before the fix, with a presenter that always picks the session
    option: **three calls, three prompts**, two of them on the same channel, and
    `_grants` holding the same rule twice.

    The inbound gateway surfaced this first — a phone channel with a session
    grant re-prompted on every message — but it is not the gateway's bug, and
    `decide()` is shared by every tool, so the fix is here rather than there.

    `ALLOW_ONCE` is deliberately **not** short-circuited, and that is the test
    that stops this being over-fixed into "the prompt never appears".
    """

    @staticmethod
    def _answer(monkeypatch, choice):
        """Patch the two `ask_bridge` seams `decide()` uses, so no UI is needed."""
        from shani_chronoa import ask_bridge

        asked = []
        monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)

        def fake_ask(question, options, timeout=0.0):
            asked.append(question)
            assert choice in options, (
                f"{choice!r} was not offered; the test would be asserting "
                f"behaviour nobody can reach: {options!r}")
            return choice

        monkeypatch.setattr(ask_bridge, "ask", fake_ask)
        return asked

    @staticmethod
    def _clear():
        from shani_chronoa import permissions
        permissions.clear(session_only=True)

    def test_a_session_grant_does_not_ask_again(self, monkeypatch):
        from shani_chronoa import permissions

        self._clear()
        asked = self._answer(monkeypatch, permissions.ALLOW_SESSION_CHOICE)
        try:
            results = [permissions.decide("act", "res", "key", "d")
                       for _ in range(3)]
        finally:
            self._clear()
        assert len(asked) == 1, (
            f"the person was asked {len(asked)} times after choosing "
            "'Allow for this session' - the option recorded a decision and then "
            "ignored it")
        assert results == [permissions.Decision.ALLOW_SESSION] * 3, results

    def test_allow_once_still_asks_every_time(self, monkeypatch):
        """"This once" means once. Without this, the fix above is a regression."""
        from shani_chronoa import permissions

        self._clear()
        asked = self._answer(monkeypatch, permissions.ALLOW_ONCE_CHOICE)
        try:
            results = [permissions.decide("act", "res", "key", "d")
                       for _ in range(3)]
        finally:
            self._clear()
        assert len(asked) == 3, (
            f"asked {len(asked)} times; 'Allow this once' must not suppress the "
            "question, or the grant outlives the thing it was granted for")
        assert results == [permissions.Decision.ALLOW_ONCE] * 3, results

    def test_a_denial_still_settles_it(self, monkeypatch):
        """The behaviour the short-circuit was originally written for."""
        from shani_chronoa import permissions

        self._clear()
        asked = self._answer(monkeypatch, permissions.DENY_CHOICE)
        try:
            results = [permissions.decide("act", "res", "key", "d")
                       for _ in range(3)]
        finally:
            self._clear()
        assert len(asked) == 1, asked
        assert results == [None, None, None], results

    def test_a_session_grant_is_scoped_to_its_own_resource(self, monkeypatch):
        """Answering for one channel must not answer for another."""
        from shani_chronoa import permissions

        self._clear()
        asked = self._answer(monkeypatch, permissions.ALLOW_SESSION_CHOICE)
        try:
            permissions.decide("act", "phone", "key", "d")
            permissions.decide("act", "phone", "key", "d")
            permissions.decide("act", "laptop", "key", "d")
        finally:
            self._clear()
        assert len(asked) == 2, (
            f"asked {len(asked)} times; the second channel must still be asked")

    def test_an_identical_rule_is_not_recorded_twice(self, monkeypatch):
        """One rule per decision, not one per call - measured growing per message."""
        from shani_chronoa import permissions

        self._clear()
        self._answer(monkeypatch, permissions.ALLOW_SESSION_CHOICE)
        try:
            for _ in range(5):
                permissions.decide("act", "res", "key", "d")
            recorded = [r for r in permissions.rules() if r[1] == "res"]
        finally:
            self._clear()
        assert len(recorded) == 1, (
            f"the same rule is recorded {len(recorded)} times: {recorded!r} - it "
            "grew by one entry per approved call for the whole session, and "
            "evaluate() walks the list on every check")

    def test_recording_an_identical_rule_preserves_order(self):
        """Replacing rather than appending must not reorder anything."""
        from shani_chronoa import permissions

        permissions.clear()
        try:
            permissions.add_rule("a", "*", "deny_once")
            permissions.add_rule("b", "*", "allow_once")
            permissions.add_rule("a", "*", "deny_once")   # duplicate, first entry
            assert permissions.rules() == [("a", "*", "deny_once"),
                                           ("b", "*", "allow_once")], (
                f"order changed: {permissions.rules()!r}")
        finally:
            permissions.clear()
