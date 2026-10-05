"""The wait for the model is a *state*, not a sentence.

`assistd` carries `VoiceCaptureState::Queued` — "waiting for the GPU to free up
before transcribing" — and today Chronoa's `speech_gate` produced that wait
without producing anything anyone could see: the orb simply paused between
listening and thinking, which is indistinguishable from the microphone having
stopped working.

These tests hold two things shut: that every assistant state has a colour, an
icon, a label and a CSS rule, so adding one cannot leave a state invisible; and
that the gate's answer is reachable as a state at all.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import speech_gate  # noqa: E402
from shani_chronoa.gui import AssistantState  # noqa: E402
from shani_chronoa.gui import style as style_module  # noqa: E402
from shani_chronoa.gui import widgets  # noqa: E402


def _source(module) -> str:
    import inspect
    return inspect.getsource(module)


def test_every_state_has_a_colour_an_icon_and_a_label():
    """A state with no style renders as the previous state's, which is how a
    visible state becomes an invisible one."""
    for state in AssistantState:
        assert state in widgets._STATE_STYLE, f"{state} has no colour or icon"
        colour, icon = widgets._STATE_STYLE[state]
        assert colour.startswith("#") and len(colour) == 7, f"{state}: {colour!r}"
        assert icon and icon.endswith("-symbolic"), f"{state}: {icon!r}"
        assert widgets._STATE_LABELS.get(state), f"{state} has no label"


def test_the_queued_state_is_distinguishable_from_listening_and_thinking():
    """It sits between them - the microphone is open, the accelerator is busy -
    so it needs its own colour *and* its own icon, or it is one of the other two
    to anyone who cannot separate hues.

    A first version here deliberately reused listening's green. That is the
    reading that felt right, and `tests/test_window_ux.py` refused it: no two
    states may share a colour, because colour alone excludes colourblind users.
    The existing invariant is right and this file's preference was wrong.
    """
    colours = {state: widgets._STATE_STYLE[state][0] for state in AssistantState}
    assert colours[AssistantState.QUEUED] not in (
        colours[AssistantState.LISTENING], colours[AssistantState.THINKING])
    assert widgets._STATE_STYLE[AssistantState.QUEUED][1] != widgets._STATE_STYLE[AssistantState.LISTENING][1]


def test_every_state_has_a_css_rule():
    """The orb paints itself from CSS classes, so a state without one is a state
    that renders unstyled whatever colour the table says.

    `IDLE` is the exception and is named rather than skipped: the resting orb is
    painted by the base `.chronoa-orb` rule, so `state-idle` has nothing to add.
    """
    css = _source(style_module)
    for state in AssistantState:
        if state is AssistantState.IDLE:
            assert ".chronoa-orb {" in css, "the base rule the idle orb relies on"
            continue
        assert f".chronoa-orb.state-{state.value}" in css, f"{state} has no CSS rule"


def _rule_body(css: str, selector: str) -> str:
    """The text between `selector {` and its closing brace."""
    start = css.index(selector)
    return css[start: css.index("}", start)]


def test_every_state_that_animates_can_stop_animating():
    """A pulsing orb is the point of some states, so each of those needs its own
    reduced-motion rule - and the test decides *which* by reading whether the
    rule animates at all.

    A first version asked every state for a rule instead, on the theory that one
    missing rule was cheap to notice; it was not, because most states do not
    animate and demanding a rule for them produced a test that could only fail.
    """
    css = _source(style_module)
    marker = ".reduce-motion .chronoa-orb"
    reduced = css[css.index(marker):] if marker in css else ""
    animated = []
    for state in AssistantState:
        if state is AssistantState.IDLE:
            continue
        body = _rule_body(css, f".chronoa-orb.state-{state.value}")
        if "animation:" not in body:
            continue
        animated.append(state)
        assert f".reduce-motion .chronoa-orb.state-{state.value}" in reduced, (
            f"{state} pulses and so needs a reduced-motion rule")
    # The pulse is the point of these two, so the list must not quietly empty out
    # and leave the test passing vacuously.
    assert {AssistantState.LISTENING, AssistantState.QUEUED} <= set(animated), animated


def test_the_gate_can_be_asked_and_answers_without_waiting():
    """The state is only reachable if the gate's answer is cheap to get."""
    busy = {"value": True}
    gate = speech_gate.Gate(lambda: busy["value"], timeout=0.0)
    gate.run()
    assert gate.last_reason, "the gate answered nothing, so nothing can be shown"
    busy["value"] = False
    gate.run()
    assert "idle" in gate.last_reason


def test_the_label_says_what_is_happening_not_that_something_is():
    """A person waiting needs to know it is the model they are waiting for."""
    label = widgets._STATE_LABELS[AssistantState.QUEUED]
    assert "model" in label.lower()
    assert widgets._STATE_LABELS[AssistantState.THINKING] != label, (
        "queued and thinking share a label, so the wait is invisible")