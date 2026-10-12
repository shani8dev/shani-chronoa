"""A menu item is a name, not a sentence: the five dropdowns on the mode strip.

**Measured, 2026-10-12: all five dropdowns labelled their items with the long
explanatory line instead of the short word.** The tables had carried both
columns since they were written - `_STYLES`, `_RULES`, `_HANDS`, `_MODES` are all
`(key, word, line, icon)` - and every consumer bound the word as `_word` and
threw it away:

    for key, _word, line, _icon in self._RULES:
        menu.append(line, f"app.answer-rules::{key}")

The consequence was measured on the built window, not predicted: **twelve**
`Gtk.Label`s over the 30-character ceiling that `tests/test_ui_layout_contract.py`
enforces, all of them menu items, and `test_the_main_window_wraps_or_ellipsises_
its_long_text` red with them. A menu item can neither wrap nor ellipsise - there
is no `set_wrap` on a `GtkPopoverMenuItem` and nothing to ellipsise against - so a
sentence in that column is a sentence the dropdown cannot fit.

The existing layout check does catch it, and this file exists because an
indirect witness is a slow one: it fails with a list of twelve sentences and no
hint that the cause is one loop bound in five places. These assert the cause.

**The words and the lines are genuinely different data, and the control proves
it.** `assert labels == words` would pass just as happily if every table had been
written down with the word and the line holding the same short string. So
`test_the_lines_the_fix_dropped_are_really_longer` asserts the opposite - that
the lines the menus no longer show *do* exceed the ceiling - and fails if they
ever stop. A control that cannot fail is not a control; if someone shortens the
lines to match the words, this test says so rather than quietly becoming
tautological.

The explanation is not lost. `_draw` already sets the chip's tooltip to
`f"{what}: {word}. {line}"`, so the sentence for the *current* selection is one
hover away on the chip itself - measured, not assumed, by
`test_the_chip_still_explains_the_current_choice`.
"""

import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.gui.window import _ModeStrip  # noqa: E402

#: The ceiling `tests/test_ui_layout_contract.py` enforces on every chip label,
#: named here so the two files cannot drift apart by one character.
LONG_TEXT = 30

#: The five tables, and the attribute each one's menu hangs off the chip.
#: Read from `_ModeStrip` rather than restated, so a sixth dropdown that
#: forgets to be listed here fails the "all five" assertion instead of quietly
#: going unchecked.
DROPDOWNS = (
    ("reply style", "_STYLES", "_style_chip"),
    ("answer rules", "_RULES", "_rules_chip"),
    ("hands free", "_HANDS", "_hands_chip"),
    ("answer mode", "_MODES", "_answer_chip"),
    ("answer model", None, "_model_chip"),     # built from disk, not a constant
)


class _Config:
    """The settings the strip reads, and nothing else."""

    def __init__(self) -> None:
        self.privacy_mode = False
        self.wake_word_enabled = False
        self.barge_in_vad_enabled = False
        self.reply_style = "ordinary"
        self.answer_mode = "local-cloud"
        self.model = "small"


def _labels_of(menu):
    """Every item's `label` attribute, in order."""
    return [menu.get_item_attribute_value(i, "label").get_string()
            for i in range(menu.get_n_items())]


@pytest.fixture(scope="module")
def gapp():
    """One registered application, because GIO exports one per process."""
    app = Gtk.Application(application_id="test.chronoa.menulabels",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    config = _Config()
    for name in ("answer-mode", "answer-rules", "answer-model",
                 "hands-free", "reply-style"):
        act = Gio.SimpleAction.new_stateful(
            name, GLib.VariantType.new("s"), GLib.Variant("s", ""))
        act.connect("change-state",
                    lambda a, v: config.__setattr__(name.replace("-", "_"), ""))
        app.add_action(act)
    yield app
    del app


@pytest.fixture
def strip(gapp):
    s = _ModeStrip(gapp, _Config())
    yield s
    s.unparent()


def _rows_of(strip_, table_name):
    """The rows a dropdown is built from, as `(key, word, line, icon)`."""
    if table_name is not None:
        return getattr(_ModeStrip, table_name)
    return strip_._model_rows()


def test_every_dropdown_labels_its_items_with_the_word(strip):
    """The cause, asserted directly: the word is the label, not the line.

    Five dropdowns, and the one that is built from disk is included because it is
    the one that cannot be edited into compliance by fixing a typo - its rows
    come from `local_llm.SPECS`, so a new tier with a long name would put a long
    label back without anybody touching this repository's window code.
    """
    checked = 0
    for what, table_name, chip_attr in DROPDOWNS:
        chip = getattr(strip, chip_attr, None)
        if chip is None:
            pytest.fail(f"{what}: the strip has no {chip_attr}")
        menu = chip.get_menu_model()
        assert menu is not None, (
            f"{what}: get_menu_model() returned None - the dropdown has nothing "
            f"to open. Measured on the model chip once already.")
        rows = _rows_of(strip, table_name)
        assert rows, f"{what}: the table is empty, so there is nothing to check"
        assert _labels_of(menu) == [r[1] for r in rows], (
            f"{what}: the menu labels are not the table's words")
        checked += 1
    assert checked == len(DROPDOWNS)


def test_no_dropdown_label_can_exceed_the_window(strip):
    """The ceiling itself, on the real menus rather than on the table.

    Asserting it on the table would pass even if a consumer rebuilt the label
    out of something else - `f"{word}: {note}"` is exactly the kind of edit that
    keeps every column short and makes the menu long again.
    """
    for what, _table, chip_attr in DROPDOWNS:
        menu = getattr(strip, chip_attr).get_menu_model()
        too_long = [t for t in _labels_of(menu) if len(t) > LONG_TEXT]
        assert not too_long, (
            f"{what}: a menu item can neither wrap nor ellipsise, so a label "
            f"over {LONG_TEXT} characters cannot shrink: {too_long}")


def test_the_lines_the_fix_dropped_are_really_longer(strip):
    """**The control: the two columns are not the same data.**

    Without this, `test_every_dropdown_labels_its_items_with_the_word` is a
    claim that the menus show the words - and it would still be true if the
    tables had been written down with word and line holding the same string, in
    which case dropping the line would have lost nothing and fixed nothing.

    So: at least one table must have a line over the ceiling that its word is
    not. If somebody edits the sentences down to match the names, this fails and
    says the distinction has gone - rather than the file quietly becoming
    tautological.
    """
    witnesses = []
    for what, table_name, _chip in DROPDOWNS:
        for key, word, line, _icon in _rows_of(strip, table_name):
            if len(line) > LONG_TEXT and word != line:
                witnesses.append((what, key, len(word), len(line)))
    assert witnesses, (
        "no dropdown has a line longer than the ceiling, so this file's claim "
        "that the menus used to be sentences cannot be checked against "
        "anything - the tables have been edited")


def test_the_chip_still_explains_the_current_choice(strip):
    """The explanation was moved, not deleted.

    `_draw` sets the chip's tooltip to `f"{what}: {word}. {line}"`, so the
    sentence for the selection is on the chip rather than in the menu. If a
    later edit drops the `line` half of that f-string - a tidy-up that reads as
    obvious - the menu goes terse *and* the sentence disappears, and this is the
    only thing that notices.
    """
    chip = strip._rules_chip
    tooltip = chip.get_tooltip_text() or ""
    word, line = _ModeStrip._RULES[0][1], _ModeStrip._RULES[0][2]
    assert word in tooltip, (
        f"the chip tooltip does not name the current choice: {tooltip!r}")
    assert line in tooltip, (
        f"the chip tooltip has lost the explanation: {tooltip!r}")


def test_every_declared_tier_word_fits_the_ceiling(strip, monkeypatch):
    """**The dropdown built from data is the one the menu assertions cannot see.**

    Measured on this machine: `local_llm.installed()` is **empty**, so the model
    menu has exactly one row - the hardcoded empty-disk fallback - and all six
    declared tiers are absent from it. Every assertion above therefore checks the
    model dropdown against a row nobody will ever download, and a tier with a long
    key would put a long label back on every machine that *has* it installed while
    this suite stayed green.

    So this puts every declared tier on disk for the duration and asks the **real**
    `_model_rows` what it would then build. Not a re-derivation of the word rule
    copied out of the source and evaluated here - a copy tests the copy, and goes
    stale silently the first time that line is reformatted.
    """
    from shani_chronoa import local_llm

    specs = getattr(local_llm, "SPECS", {})
    assert specs, (
        "SPECS is empty, so this test walks nothing and passes without checking "
        "anything - if the tiers moved, point this at the new source")

    monkeypatch.setattr(local_llm, "installed", lambda: set(specs))
    rows = strip._model_rows()

    # The control: the monkeypatch actually applied. Without it `_model_rows`
    # returns the single empty-disk row, every word passes, and this test is the
    # vacuous check it was written to avoid.
    assert len(rows) == len(specs), (
        f"expected all {len(specs)} declared tiers, got {len(rows)} rows - the "
        f"installed() patch did not take, so this test checked nothing")

    too_long = [(key, word) for key, word, _line, _icon in rows
                if len(word) > LONG_TEXT]
    assert not too_long, (
        f"a tier whose name cannot shrink would put a long label in the menu on "
        f"every machine that installs it: {too_long}")

    # And the line is still built for every tier, so the chip tooltip has
    # something to say about a selection that is not the fallback row.
    empty = [(k, l) for k, _w, l, _i in rows if not l.strip()]
    assert not empty, f"a tier with no description leaves the tooltip silent: {empty}"


def test_this_files_control_can_fail(strip):
    """**The control's own control.**

    `test_the_lines_the_fix_dropped_are_really_longer` asserts a property of the
    data. This asserts the *comparison* in it can distinguish the two columns:
    drive it over a row built so the word is short and the line is a sentence,
    and a row where they are identical, and check the predicate keeps only the
    first. A predicate that accepted both would let the control above pass on
    tables whose lines had been flattened onto their words.
    """
    def is_the_witness(word, line):
        return len(line) > LONG_TEXT and word != line

    assert is_the_witness("Local only", "Local only: nothing leaves this machine")
    assert not is_the_witness("Local only", "Local only")
    assert not is_the_witness(
        "Local only: nothing leaves this machine", "Local only: nothing leaves this machine")
    assert not is_the_witness("short", "also short")