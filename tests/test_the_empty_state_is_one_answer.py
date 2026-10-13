"""The empty state had two answers to "what can I ask?", and one could not run.

`85c11ab` added a `_starter_row` of five chips to the main window's chat column,
above the transcript. `TranscriptView` already had a `SuggestionBar` of six
chips in its empty state. So for as long as both existed, the question the
first one was written to answer had **two** answers on screen - and the newer
one had never been visible.

**Why the newer one was never visible.** Its gate was
`TranscriptView.is_empty()`, and `is_empty()` asked

    len(self._rows.get_children()) == 0

`self._rows` is a `Gtk.Box`. **Both spellings are GTK3.** Measured on this box:

    len(Gtk.Box())            -> TypeError: object of type 'Box' has no len()
    Gtk.Box().get_children()  -> AttributeError: 'Box' object has no attribute
                                 'get_children'

So `is_empty()` raised `AttributeError` on *every* call, and
`_refresh_starter_row`'s `except Exception` - which fails closed, correctly -
turned the failure into "not empty". The row was permanently hidden.

**And its own test file was green throughout**, because
`test_the_empty_state_answers_what_can_i_ask.py` asserted on the *other* row:
its headline test walks the transcript for `.suggestion-chip` buttons. The
watcher was pointed at the mechanism that works.

That is this repository's most-recorded failure wearing a new costume, and it
is why this file asserts on the **built window** rather than on either method:

- `is_empty()` **returns** rather than raising (the shipped bug),
- it answers about *turns*, and answers them correctly in both directions,
- the window carries exactly **one** row of example questions,
- that row's words are **questions**, not panel titles.

**Why the panel titles were the wrong half.** Measured side by side on a real
window, at the size it opens at:

| | the starter row | the SuggestionBar |
|---|---|---|
| chips | 5 | 6 |
| words | `Conversations, Compare, My files, Meetings, Phone link` | `Battery left?, What time?, Set a timer, Which model?, Search the web, Screenshot` |
| built from | `available_surfaces()` | `capabilities.startup_suggestions()` |
| gated on | which **panels** exist | which **skills** are installed |
| shown | **never** | yes |
| fit measured | no | 149px of a 162px viewport |

"What can I ask?" is answered by questions. A list of page names answers "what
can I look at", and for an app whose pitch is 256 named skills, the chips that
name panels point a newcomer at the panels.

**So this is a removal, and it is deliberate.** `is_empty()` is still a public
question and still answers its documented question - a new empty state has
somewhere to ask - but it has no caller in the product, and the starter row's
test file is retired with its feature rather than left as orphans.

The control that matters is the last test: **a second row of chips must fail
it.** The original defect was two answers coexisting because nobody counted
them, so the count is the property rather than the identity of either row.
"""
import re
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui import ChronoaWindow  # noqa: E402
from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402

GUI = Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa/shani_chronoa/gui"


def walk(node):
    """Every descendant, depth first."""
    yield node
    child = node.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


class _App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.shani.chronoa.oneanswer")


# --- the check itself -------------------------------------------------------

def test_is_empty_runs():
    """**The shipped defect, restored verbatim, fails here.**

    Every spelling this ever reached for is GTK3. A `Gtk.Box` has no
    `get_children` and no `len()`; the only GTK4 spelling is
    `get_first_child()`, and even that answers the wrong question - `_rows`
    holds the empty state's own furniture.
    """
    view = TranscriptView()
    assert view.is_empty() is True, (
        "a fresh transcript has no turns, so is_empty() must say so rather than "
        "raise - every previous spelling raised, and every caller caught it"
    )


def test_is_empty_answers_about_turns_not_widgets():
    """Both directions, because a check that is always True is not a check.

    The wrong-but-running answer is `get_first_child() is None`: `_rows` holds
    the prompt, the shortcut hints and the `SuggestionBar` from construction, so
    that spelling says "not empty" on a brand-new window and would hide any
    empty state a future feature tried to build.
    """
    view = TranscriptView()
    assert view._rows.get_first_child() is not None, (
        "the fixture is wrong: an empty transcript really does hold its "
        "placeholder rows, so a widget-shaped check has something to be wrong "
        "about"
    )
    assert view.is_empty() is True

    view._append_turn("user", "what time is it")
    assert view.is_empty() is False, (
        "a transcript holding a turn reported itself empty, which is the failure "
        "that put example questions under a conversation that had answers in it"
    )

    view.clear()
    assert view.is_empty() is True, "clearing the transcript did not empty it"


def test_no_production_caller_was_left_behind():
    """`is_empty()` is public and has no caller, which is a decision, not drift.

    The starter row was its only caller. A public method that nothing reads is
    fine once; the failure this file guards against is the *silent* reappearance
    of a second empty-state mechanism, so the assertion is that the name is
    gone from the shipped tree rather than that it has a caller.
    """
    hits = []
    for path in sorted(GUI.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\.is_empty\(\)", line) and "def is_empty" not in line:
                hits.append(f"{path.name}:{number}: {stripped}")
    assert not hits, (
        "something calls is_empty() again. That is fine if it needs the answer, "
        "but it must not be a second empty state:\n  " + "\n  ".join(hits)
    )


# --- the window -------------------------------------------------------------

def _chips(window):
    return [w for w in walk(window)
            if isinstance(w, Gtk.Button) and w.has_css_class("suggestion-chip")]


def test_the_window_has_exactly_one_row_of_example_questions():
    """**The control that matters.**

    The defect was two answers to one question coexisting because nobody counted
    them. A test naming either row by identity would pass the moment a third
    appeared.
    """
    window = ChronoaWindow(_App())
    bars = [w for w in walk(window) if type(w).__name__ == "SuggestionBar"]
    assert len(bars) == 1, (
        f"the empty state carries {len(bars)} rows of example questions. One "
        f"question has one answer on screen; a second row is the defect this "
        f"file was written about"
    )
    assert _chips(window), (
        "the one row is present but carries no chips, so the empty state answers "
        "'what can I ask?' with nothing"
    )


def test_the_row_offers_questions_and_not_panel_titles():
    """**And the half of the defect the count cannot see.**

    One row of *panel titles* is the wrong answer just as surely as two rows of
    anything. This asserts the words, because a row of titles is perfectly
    well-formed and perfectly green to every other test in the tree.
    """
    from shani_chronoa.gui import surfaces

    window = ChronoaWindow(_App())
    titles = {str(s[0]).lower() for s in surfaces.available_surfaces().values()}
    printed = []
    for chip in _chips(window):
        child = chip.get_child()
        printed.append(child.get_text().lower()
                       if isinstance(child, Gtk.Label) else "")

    assert printed, "no chips to read"
    borrowed = [p for p in printed if p in titles]
    assert not borrowed, (
        f"the empty state offers panel titles {borrowed}. 'What can I ask?' is "
        f"answered by questions; page names answer 'what can I look at'"
    )


def test_the_retired_feature_is_gone_from_the_source():
    """Named rather than inferred, and the reason for the file's existence.

    The starter row's machinery is deleted rather than left unwired, so this is
    a source check on purpose - a method with no caller is invisible at runtime,
    and an unwired method nobody notices is the dead-code class this repository
    records more often than any other.
    """
    source = (GUI / "window.py").read_text(encoding="utf-8")
    for name in ("_STARTER_CHIPS", "_build_starter_row",
                 "_refresh_starter_row", "_on_starter_chip_clicked"):
        assert name not in source, (
            f"{name} is still in window.py. Either it is used - in which case "
            f"this file is wrong about the design - or it is a dead method, "
            f"which is the class of thing nobody notices"
        )
