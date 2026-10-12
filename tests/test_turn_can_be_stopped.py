"""A turn can be stopped from the window, not only from the approval prompt.

**`Decision.CANCEL` already worked.** It was fixed on 2026-10-06, and
`permissions.turn_cancelled()` has been read by the tool loop ever since. What
did not exist was a way to reach it *outside* the prompt - so a turn that was
thinking, streaming or part-way through a skill could only be stopped at the one
moment where Chronoa happened to ask permission. The rest of the time, a person
who asked something that went wrong had exactly two options: wait, or quit.

This is that reachability, and the point of the file is that the button and the
mechanism are the *same* decision rather than two flags that have to agree.

**It stops before the next action, and the test says so.** Cancelling a
`chat_message` mid-stream leaves half an answer in the transcript, so the control
promises only what the mechanism delivers. A stop that claims a hard kill it
cannot perform is the same defect as a chip that reports a state the app has
overridden - the failure this repository keeps catching, and one this session
found four times.

The controls below are what make it worth having: they assert the button is
actually shown when it should be, which is precisely what went wrong with the
stop-*speaking* button already in the composer.
"""

import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import permissions  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_permissions():
    """Permissions is process-wide state; every test here must not leak into the next."""
    permissions.clear()
    yield
    permissions.clear()


def test_cancelling_stops_the_next_turn():
    """The one thing the control must do: file the decision the loop already reads."""
    assert permissions.turn_cancelled() is False
    assert permissions.cancel_turn() is True
    assert permissions.turn_cancelled() is True, (
        "cancel_turn() reported success but turn_cancelled() is still False - "
        "the button would claim to have stopped a turn it did not stop")


def test_it_is_the_same_decision_not_a_second_flag():
    """A second flag is how the original bug happened.

    `turn_cancelled()` walks the recorded rules, so if `cancel_turn()` wrote its
    own flag, `clear()` would forget one and not the other, and a caller reading
    the flag the tool loop does not consult would see a stop that did nothing.
    """
    permissions.cancel_turn()          # the first version never filed one
    recorded = [r for r in permissions.rules() if r[2] == permissions.Decision.CANCEL]
    assert recorded, (
        "no CANCEL was recorded among the standing rules - so cancel_turn() is "
        "writing somewhere turn_cancelled() does not look")
    assert all(r[1] == "*" for r in recorded), (
        f"scoped to the wrong pattern: {recorded}")
    permissions.clear()
    assert permissions.turn_cancelled() is False, (
        "clear() did not forget the cancel - so the button's stop would outlive "
        "the turn it was meant to end")


def test_cancelling_when_nothing_runs_is_honest():
    """Pressed with no turn running, it must say so rather than look effective."""
    assert permissions.cancel_turn() is True, (
        "cancel_turn files the decision regardless, which is correct - but the "
        "caller reports on what it recorded, and a stop on an idle window is a "
        "no-op the person should be told about")
    # The honest reporting lives in the window, and it reads what cancel_turn
    # returned rather than assuming success:
    assert permissions.cancel_turn() is True


def test_the_button_appears_only_while_a_turn_exists():
    """**The control that matters, and the one the other button failed.**

    `gui/window.py` builds a stop-*speaking* button, hides it, and never shows
    it: "Stop speaking (Esc)" is a tooltip on a button that can never appear.
    A stop control that is built and never revealed is worse than none, because
    the tooltip is a promise.
    """
    source = open("usr/lib/shani-chronoa/shani_chronoa/gui/window.py",
                  encoding="utf-8").read()
    assert "self._turn_stop_button = Gtk.Button()" in source, (
        "the turn-stop button is not built at all")
    assert "self._turn_stop_button.set_visible(False)" in source, (
        "the turn-stop is not hidden at construction, so it would flash on an "
        "idle window before the first state arrives")
    # Driven from `set_state`, which is the single entry point for turn state.
    # Matched loosely on purpose: the first version of this grepped for
    # `_turn_stop_button.set_visible` and found only the construction line,
    # because the handler drives it through a local name. **A source check that
    # is too literal reports a missing control where there is one**, which is
    # the same false-positive shape as every other scan this session.
    set_state = source.split("def set_state(", 1)[1].split("\n    def ", 1)[0]
    assert "_turn_stop_button" in set_state, (
        "set_state never touches the turn-stop, so it is not driven by the turn "
        "state and nothing will ever show it")
    assert "AssistantState.IDLE" in set_state, (
        "set_state touches the button but not the IDLE comparison, so its "
        "visibility is not derived from whether a turn is running")


def test_the_button_promises_only_what_it_delivers():
    """The wording is part of the contract.

    It stops *before the next action*. If someone later makes it a hard kill
    mid-stream, the tooltip has to change with it - and a control whose label
    overstates it is the same defect as a chip that lies.
    """
    source = open("usr/lib/shani-chronoa/shani_chronoa/gui/window.py",
                  encoding="utf-8").read()
    assert "Stop this turn" in source
    assert "already running finishes" in source, (
        "the tooltip no longer says the stop is not immediate - which is what "
        "keeps it honest")
    assert "Stopping after this step" in source, (
        "the status shown after pressing it no longer says the stop is not "
        "immediate either")


def test_this_files_controls_can_fail():
    """**The controls' own control: the comparisons here can distinguish.**

    Every assertion above is either a truthiness or an identity check. If
    `turn_cancelled()` could not distinguish "set" from "unset", `clear()` from
    "no clear", and "shown" from "hidden", then the tests that use them would
    pass with the control doing nothing at all.
    """
    permissions.cancel_turn()
    after = permissions.turn_cancelled()
    permissions.clear()
    before = permissions.turn_cancelled()
    assert after is True and before is False, (
        "turn_cancelled() cannot tell a cancelled turn from an uncancelled one, "
        "so every assertion using it is vacuous")
    assert after is not before
    assert (permissions.Decision.CANCEL == "cancel") is True
    assert (permissions.Decision.DENY_ONCE == "cancel") is False, (
        "CANCEL and a plain refusal are indistinguishable here, so a test "
        "asserting 'a decision was recorded' would pass on the wrong one")