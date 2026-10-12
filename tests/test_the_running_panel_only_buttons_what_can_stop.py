"""A row in "What's running" offered a button labelled with a state.

Measured on the built panel:

    Local model server   your RAM - the local model server. Stopped.
                         [BUTTON] Stopped

Three defects in two lines, from `label="Stop" if item.running else "Stopped"`
followed by `set_sensitive(item.running)`:

* The button is labelled **Stopped** - a state, not an action. A control says
  what is true rather than what it does.
* It restates `{state}`, which is already in the row's own subtitle.
* It is **disabled**, so pressing it does nothing at all.

A stopped item keeps no suffix now, which is the same shape as a row that cannot
be stopped: the subtitle says "Stopped" and, where there is a reason, the row
names it. That is the rule this repo applies everywhere - `gui/surfaces/
devices.py` states it directly: **a button on the other states would open
Settings and change nothing, which is a worse dead end than the sentence it
replaced.** A disabled button is that, in miniature.

Shallow probes get this wrong: `common.row(title, subtitle, suffix)` nests the
suffix inside its own box, so walking only a row's *direct* children reports a
running item as having no button too. The walk here goes through the tree.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui.surfaces import processes  # noqa: E402


class _Item:
    def __init__(self, running, stop=True, why_not=None):
        self.label = "Local model server"
        self.holds = "your RAM"
        self.detail = "the local model server"
        self.running = running
        self.stop = (lambda *a: None) if stop else None
        self.why_not = why_not


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(row):
    return [w for w in _walk(row) if isinstance(w, Gtk.Button)]


def _a_row(item):
    return processes._Page.__new__(processes._Page)._row(item)


def test_a_running_item_gets_a_button_that_says_what_it_does():
    found = _buttons(_a_row(_Item(running=True)))
    assert [b.get_label() for b in found] == ["Stop"], (
        f"a running, stoppable item should offer one 'Stop', found "
        f"{[b.get_label() for b in found]}")
    assert found[0].get_sensitive(), "the only button on screen must work"


def test_a_stopped_item_offers_no_button():
    """**The defect.** No suffix at all, so the row cannot carry a disabled
    control that says what is true instead of doing anything."""
    assert not _buttons(_a_row(_Item(running=False))), (
        "a stopped item still carries a button - it was labelled with a state "
        "and disabled, so it was a control that could do nothing")


def test_a_row_that_cannot_be_stopped_names_the_reason():
    """The other no-button case, and the one the file already handled: the
    reason a control is missing is more useful than its absence."""
    row = _a_row(_Item(running=True, stop=False,
                      why_not="stopping it would drop the reply you are hearing"))
    assert not _buttons(row)

    said = []
    for widget in _walk(row):
        if isinstance(widget, Gtk.Label) and widget.get_text():
            said.append(widget.get_text())
    text = " ".join(said)

    assert "stopping it would drop the reply" in text, (
        f"the row says nothing about why it cannot be stopped: {text!r}")


def test_a_stopped_item_is_not_told_it_cannot_be_stopped():
    """**The control for the fix, and the trap in it.**

    With no button on a stopped row, the `button is None` branch's fallback
    - "this one cannot be stopped without quitting Chronoa" - would now fire on
    a stopped item too, where it is false. The subtitle says "Stopped", and the
    row must not add a claim that contradicts it.
    """
    row = _a_row(_Item(running=False))
    said = []
    for widget in _walk(row):
        if isinstance(widget, Gtk.Label) and widget.get_text():
            said.append(widget.get_text())
    text = " ".join(said)

    assert "Stopped" in text, f"the row no longer says it is stopped: {text!r}"
    assert "cannot be stopped" not in text, (
        f"a stopped item is told it cannot be stopped, which is not true - it is "
        f"already stopped: {text!r}")
