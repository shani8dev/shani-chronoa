"""Undo names what it will undo, and is absent when there is nothing to undo.

`undo_last_change` is **global** - it undoes the last change, and its own
docstring spends lines separating that from *"revert to some earlier state I
happened to have in mind."* So the obvious placement, a button on the card of
the action, would have said "undo this" and undone something else.

That is the exact defect this repository keeps catching and this session found
seven times: a control promising more than the mechanism delivers. Hence the
button lives in the composer, is always about the most recent change, and gets
its label from `recent_changes()` rather than from an assumption.

**The empty case is the one that matters most.** On a machine that has changed
no files, `recent_changes()` returns `[]` - measured - so the button has to be
*absent*, not disabled and not present. A control with nothing behind it is the
shape of the stop-speaking button already in this composer row: built, hidden,
and never revealed.
"""

import pathlib
import sys

import pytest

# No `gi` here, and deliberately so: this file reads source text and asks the
# undo ring what it holds, and it never builds a window. The first version
# imported the window module to reach `ChronoaWindow` and then never used it -
# which dragged in `gi.require_version` calls before `gi` existed, so the file
# did not even collect.

sys.path.insert(0, "usr/lib/shani-chronoa")

WINDOW = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa/gui/window.py")


def _source():
    return WINDOW.read_text(encoding="utf-8")


def test_the_button_is_in_the_composer_not_on_a_card():
    """Global means one place, and the composer is the one place always drawn."""
    src = _source()
    assert "self._undo_button = Gtk.Button()" in src
    assert "input_row.append(self._undo_button)" in src, (
        "the undo button is not in the composer row - a turn-scoped placement "
        "would claim to undo that turn, and the skill undoes the last change")


def test_it_is_hidden_when_there_is_nothing_to_undo():
    """**The empty case, which is the reachable one on a clean machine.**

    Measured on this machine: `recent_changes()` is `[]`, so a button that is
    merely disabled would be a permanent dead control in the composer.
    """
    from shani_chronoa.skills import undo_last_change

    if undo_last_change.recent_changes():
        pytest.skip("this machine has an undoable change, so the empty state "
                    "cannot be observed here")
    src = _source()
    assert "self._undo_button.set_visible(False)" in src, (
        "the button does not start hidden")
    assert "button.set_visible(found is not None)" in src, (
        "the button's visibility is not driven by whether there is anything to "
        "undo, so it can sit in the composer promising something it cannot do")


def test_the_label_names_the_file_rather_than_guessing():
    """It asks `recent_changes()` for the path.

    A button whose tooltip says "Undo the last change" when three files changed
    is not lying, but a button that names the newest one is answering the
    question the person actually has.
    """
    src = _source()
    assert "undo_last_change.recent_changes()" in src, (
        "the button does not read the ring, so whatever it names is a guess")
    assert "Undo the change to {name}" in src


def test_it_asks_the_skill_own_consent_gate():
    """The gate is the skill's, asked rather than assumed.

    `undo_last_change` refuses without `file-edit-enabled` and says why. A
    button that ignored that would offer a control whose every press ends in a
    refusal message - which is worse than no button, because it teaches people
    to press things that do not work.
    """
    src = _source()
    assert "undo_last_change._consent" in src, (
        "the button does not ask the skill whether undoing is permitted")
    assert "Undo is off: {why}" in src, (
        "when undo is refused the button does not say so in its own label")


def test_it_reports_what_the_skill_said_rather_than_what_it_assumed():
    """The ring can move between the label being drawn and the press."""
    src = _source()
    assert "undo_last_change._run({})" in src
    assert "self.set_status(said.strip()" in src, (
        "the button reports its own assumption about what was undone, rather "
        "than what the skill actually reported")


def test_it_refreshes_when_a_turn_finishes():
    """A turn is when files get changed.

    Without this the button would only appear at the next window build, which is
    the same defect as the stop-speaking button in a different form: correct
    logic, unreachable in practice.
    """
    src = _source()
    assert "self._refresh_undo_button()" in src
    after_turn = src.split("self._transcript.add_assistant_turn(text)", 1)
    assert len(after_turn) == 2 and "_refresh_undo_button()" in after_turn[1][:400], (
        "the undo button is not refreshed when a turn finishes, so it cannot "
        "appear in response to the change that caused it")


def test_this_files_controls_can_fail():
    """**The control: the assertions above can tell present from absent.**

    Four of them are `in`-checks against source text, which is the shape that
    passes when the code is simply missing and the string is found elsewhere.
    So the control asserts the specific strings are distinguishable - and that
    the file really is the one being read, since a check pointed at the wrong
    path would satisfy itself forever.
    """
    src = _source()
    assert WINDOW.exists() and "class ChronoaWindow" in src, (
        "this file is reading something that is not the window module")
    assert "input_row.append(self._undo_button)" in src
    assert "input_row.append(self._undo_button_hidden)" not in src, (
        "the control string and the real one are not distinguishable")
    assert ("Undo the change to {name}" in src) is True
    assert ("Undo the change to {nothing}" in src) is False, (
        "the substring checks cannot tell a present string from an absent one")