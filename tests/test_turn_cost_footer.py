"""A turn says what it cost, because the number was already being computed.

`assistant.turn_stats()` times every model call, sums the token usage each
backend reports, and returns calls, tokens, model seconds, wall seconds and
whether the turn overran its budget. **Measured: zero callers outside its own
definition.** So every turn since the assistant was written produced a cost and
showed it to nobody — the same class as the stop-speaking button that is built,
hidden and never revealed.

These check the footer states only what it measured. The one thing it must never
do is print money: `turn_stats()`' own docstring draws that line, because when
cloud fallback is on a per-call price depends on the provider and the model, and
a wrong figure invented at the display layer would be worse than none.

They also check it is **silent** rather than showing a zero. "0.0s" on a turn
that has not run a model call yet is a claim, and this repository keeps catching
controls that make claims.
"""

import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402


def _transcript(stats):
    """A transcript whose footer handler answers with `stats`."""
    view = TranscriptView()
    view.set_turn_footer_handler(lambda: stats)
    return view


def _footer_label(view):
    """The footer's text, or None. Walks the real tree.

    The first version reached two levels down by hand and **returned None for
    every case** - which meant the three silence tests were passing vacuously
    while the three that wanted a footer all failed. A test that cannot find the
    thing it is testing looks exactly like a test asserting its absence, so this
    now searches rather than assumes a shape.
    """
    found = []

    def walk(node):
        if isinstance(node, Gtk.Label):
            classes = {c for c in node.get_css_classes()}
            if "dim-label" in classes and node.get_label():
                found.append(node.get_label())
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(view._rows)
    return found[0] if found else None


def test_a_turn_with_cost_gets_a_footer():
    view = _transcript({"model_calls": 2, "wall_seconds": 4.31,
                        "model_seconds": 4.0, "over_budget": False,
                        "usage": {"prompt_tokens": 900, "completion_tokens": 304},
                        "budget_seconds": 120})
    view.add_assistant_turn("Here is the answer.")
    said = _footer_label(view)
    assert said is not None, "a turn that ran two model calls shows no cost"
    assert "2 model calls" in said
    assert "4.3s" in said, f"the duration is not shown: {said!r}"
    assert "1,204 tokens" in said, f"the tokens are not shown: {said!r}"


def test_it_never_prints_money():
    """The line `turn_stats()` draws, checked where the rendering happens.

    Cloud fallback makes a per-call price depend on the provider and the model,
    so a figure invented at the display layer would be a number nobody can
    defend - and a wrong price is worse than no price.
    """
    view = _transcript({"model_calls": 1, "wall_seconds": 1.0,
                        "over_budget": False, "usage": {"prompt_tokens": 10}})
    view.add_assistant_turn("An answer.")
    said = _footer_label(view) or ""
    for forbidden in ("$", "£", "€", "cost", "price", "USD", "pence"):
        assert forbidden not in said, (
            f"the footer invented a price: {said!r} contains {forbidden!r}. "
            f"turn_stats() deliberately reports what was measured and leaves "
            f"pricing to whoever knows it.")


def test_no_handler_means_no_footer_at_all():
    """With nothing attached, a turn carries nothing extra.

    The empty state is the one that was actually reachable before this change -
    `window.py` passed no handler at all - so if this case ever grew a label the
    panel would fill with zeroes on every turn.
    """
    view = TranscriptView()
    view.add_assistant_turn("An answer.")
    assert _footer_label(view) is None, (
        "a transcript with no footer handler still drew one")


def test_a_turn_with_no_model_call_is_silent_not_zero():
    """**The silence case, which is the one a zero would break.**

    Before the first model call `turn_stats()` reports `model_calls: 0`, and a
    footer that rendered that would say "0.0s" about a turn that has not
    happened. It says nothing instead.
    """
    view = _transcript({"model_calls": 0, "wall_seconds": 0.0,
                        "over_budget": False, "usage": None})
    view.add_assistant_turn("An answer.")
    assert _footer_label(view) is None, (
        "a turn with no model call drew a footer - which would read as '0.0s' "
        "about a turn that had not happened")


def test_an_overrunning_turn_says_so():
    view = _transcript({"model_calls": 3, "wall_seconds": 130.0,
                        "over_budget": True, "usage": {"prompt_tokens": 5}})
    view.add_assistant_turn("A long answer.")
    said = _footer_label(view)
    assert said and "over its time budget" in said, (
        f"a turn that overran does not say it did: {said!r}")


def test_one_call_is_not_plural():
    """The word agrees with the number.

    Trivial, and the kind of thing that is written once and never checked again.
    """
    view = _transcript({"model_calls": 1, "wall_seconds": 0.9,
                        "over_budget": False, "usage": None})
    view.add_assistant_turn("Short.")
    said = _footer_label(view) or ""
    assert "1 model call" in said and "1 model calls" not in said, said


def test_a_handler_that_raises_draws_nothing():
    """The footer is decoration and must not be able to break a turn.

    `turn_stats()` reaches the live assistant, and a read that raises - an
    assistant mid-teardown, say - must not take the transcript with it.
    """
    view = TranscriptView()

    def boom():
        raise RuntimeError("no assistant")

    view.set_turn_footer_handler(boom)
    view.add_assistant_turn("An answer.")
    assert _footer_label(view) is None