"""Ctrl+K: one list of everywhere you can go, and the keyboard to get there.

244 skills, every panel, every slash command, and the mode switches - with the
sidebar search filtering *only* panels and sitting somewhere you have to be.

The ranking and the filter are pure functions and are tested as such. The parts
that can only be checked against a built widget - Enter actually running the
entry, the arrow keys moving - are driven through the real palette.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk  # noqa: E402

from shani_chronoa.gui import palette as palette_module  # noqa: E402


def _search_for(palette, text):
    """Type `text` into the palette.

    **`set_text()` alone does not filter**, and the signal is emitted here on
    purpose: `Gtk.SearchEntry.set_text` assigns the text without emitting
    `search-changed`, so a test that only calls it sees the unfiltered list and
    concludes the filtering is broken. Typing emits it; that is what this does.
    """
    palette._search.set_text(text)
    # **`emit` takes no arguments for this signal**, measured:
    # `TypeError: 0 parameters needed for signal search-changed; 1 given`. The
    # connected handler reads the entry off the closure, so it does not need one.
    palette._search.emit("search-changed")
    return palette


def _palette(entries):
    palette = palette_module.CommandPalette()
    palette.set_entries(entries)
    return palette


def _entry(title, subtitle="", *, run=None, **kwargs):
    """`run` is keyword-only on purpose.

    The first version took it as the second positional, so `_entry("Encryption",
    "TPM2 and LUKS")` made the *string* the callback - `collect()` only checks
    `run is not None`, so it passed, and the test was quietly about nothing.
    """
    return palette_module.Entry(title, subtitle,
                                run=run if run is not None else (lambda: None),
                                **kwargs)


# -- what it offers --------------------------------------------------------


def test_an_entry_with_nothing_to_run_is_never_offered():
    """A row that reports nothing when chosen is a button wired to nothing."""
    assert palette_module.collect([palette_module.Entry("Dead end")]) == []


def test_two_entries_with_the_same_title_collapse():
    """A panel can also be a slash command; two identical rows read as a bug."""
    entries = palette_module.collect([_entry("Same"), _entry("Same")])
    assert len(entries) == 1


def test_the_same_title_in_two_groups_is_kept_twice():
    entries = palette_module.collect([_entry("Files", group="Panels"),
                                       _entry("Files", group="Commands")])
    assert len(entries) == 2


# -- ranking ---------------------------------------------------------------


def test_an_empty_query_keeps_the_order_the_caller_chose():
    """Opening the palette should show the common things, not 244 skills A-Z."""
    entries = [_entry("Zebra"), _entry("Alpha")]
    assert [e.title for e in palette_module.filter_entries(entries, "")] == \
        ["Zebra", "Alpha"]


def test_a_prefix_match_outranks_a_substring_match():
    entries = [_entry("My Alpine trip"), _entry("Alpine")]
    assert [e.title for e in palette_module.filter_entries(entries, "alpine")] \
        == ["Alpine", "My Alpine trip"]


def test_a_subtitle_can_match():
    entries = [_entry("Encryption", "TPM2 and LUKS")]
    assert palette_module.filter_entries(entries, "luks")


def test_a_typo_shows_nothing_rather_than_something_else():
    """Silently falling back to the full list makes a typo look like a search."""
    assert palette_module.filter_entries([_entry("Panels")], "qqqq") == []


def test_matching_ignores_case():
    assert palette_module.filter_entries([_entry("Panels")], "PANELS")


# -- the widget ------------------------------------------------------------


def test_the_first_entry_starts_selected():
    palette = _palette([_entry("One"), _entry("Two")])
    assert palette.activate_selected() is True


def test_enter_runs_the_highlighted_entry():
    ran = []
    palette = _palette([_entry("One", run=lambda: ran.append("one")),
                        _entry("Two", run=lambda: ran.append("two"))])
    palette._select(1)
    assert palette.activate_selected() is True
    assert ran == ["two"], f"Enter ran {ran}"


def test_the_arrow_keys_move_the_selection_and_wrap():
    ran = []
    palette = _palette([_entry("A", run=lambda: ran.append("a")),
                        _entry("B", run=lambda: ran.append("b")),
                        _entry("C", run=lambda: ran.append("c"))])
    palette._move(1)
    palette._move(1)
    palette.activate_selected()
    assert ran == ["c"]
    palette._move(1)                       # wraps back to the first
    palette.activate_selected()
    assert ran == ["c", "a"], f"Down did not wrap: {ran}"


def test_moving_up_from_the_top_wraps_to_the_end():
    ran = []
    palette = _palette([_entry("A", run=lambda: ran.append("a")),
                        _entry("B", run=lambda: ran.append("b"))])
    palette._move(-1)
    palette.activate_selected()
    assert ran == ["b"]


def test_an_empty_palette_says_so_and_activates_nothing():
    palette = _palette([_entry("Panels")])
    _search_for(palette, "zzzz")
    assert palette.visible_entries() == []
    assert palette.activate_selected() is False, (
        "an empty palette claimed it did something")
    assert palette._empty.get_visible(), "nothing says the search found nothing"


def test_escape_closes_the_palette():
    closed = []
    palette = palette_module.CommandPalette(on_close=lambda: closed.append(True))
    palette.set_entries([_entry("One")])
    palette._on_key(None, Gdk.KEY_Escape, 0, 0)
    assert closed, "Escape did not close the palette"


def test_typing_filters_what_is_offered():
    palette = _palette([_entry("Panels"), _entry("Encryption")])
    _search_for(palette, "enc")
    assert [e.title for e in palette.visible_entries()] == ["Encryption"]
