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
        assert options == [permissions.ALLOW_ONCE_CHOICE,
                           permissions.ALLOW_SESSION_CHOICE,
                           permissions.DENY_CHOICE]

    def test_the_refusal_is_offered_as_an_option_not_the_default(self):
        """Ordering carries meaning: the two allow choices come first, but the
        outcome is identical for a dismissal, so nothing is granted by default
        and the caller must treat a non-match as no."""
        seen: list = []
        ask_bridge.set_presenter(_presenter("", seen))
        execute_tool_outcome("screenshot", {})
        assert seen[0][1][-1] == permissions.DENY_CHOICE


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
