"""The palette draws a caption naming what kind of thing each row is.

**The groups were never drawn.** `Entry.group` was used for the dedup key and
for the search, and for nothing else - so opening the palette showed 260 rows
in one flat list, with nothing to say which was a panel, which a skill and
which a command. Measured on a real capture of the palette opened on a fresh
install: "Conversation / the main window", "Quick ask / one question, no
conversation", "Conversations / Remembering", "Compare / Remembering" - four
kinds of thing, one list, no headings.

The captions are rows in the same list, which is why the keyboard navigation
had to change: the row at index *n* is not the entry at index *n*. The
captions are not destinations, so they must not be selectable either.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402 - after require

from shani_chronoa.gui import palette as palette_module


def _entry(title, subtitle="", *, group="", run=None):
    return palette_module.Entry(
        title, subtitle, group=group,
        run=run if run is not None else (lambda: None))


def _palette(entries):
    palette = palette_module.CommandPalette()
    palette.set_entries(entries)
    return palette


def _captions(palette):
    """The captions the palette drew, top to bottom."""
    out = []
    row = palette._list.get_first_child()
    while row is not None:
        child = row.get_child()
        if isinstance(child, Gtk.Label):
            out.append(child.get_text())
        row = row.get_next_sibling()
    return out


def test_the_palette_draws_a_caption_naming_each_group():
    palette = _palette([_entry("Compare", group="Panels"),
                        _entry("Battery left?", group="Skills")])
    assert _captions(palette) == ["Panels", "Skills"]


def test_a_caption_is_not_a_destination():
    """A heading that can be highlighted and activated does nothing.

    The caption's row is not selectable, so asking the list to select it leaves
    the selection where it was - on the first entry. That is the property worth
    asserting: the caption cannot take the selection, so Enter can never land
    on it.
    """
    ran = []
    palette = _palette([_entry("Compare", group="Panels",
                               run=lambda: ran.append("compare"))])
    caption = palette._list.get_row_at_index(0)
    assert caption.get_selectable() is False, "the caption was selectable"
    assert caption.get_activatable() is False, "the caption was activatable"
    palette._list.select_row(caption)
    assert palette._selected_index() == 0, "the selection moved onto the caption"
    palette.activate_selected()
    assert ran == ["compare"], f"Enter ran {ran}"


def test_the_arrow_keys_step_over_the_captions():
    """Down moves one *entry* at a time, whatever captions are in between.

    The captions are rows in the list, so counting the list's own rows would
    land the selection on a heading - and a heading that is selected cannot be
    activated, so Enter would do nothing at all.
    """
    ran = []
    palette = _palette([_entry("A", group="One", run=lambda: ran.append("a")),
                        _entry("B", group="One", run=lambda: ran.append("b")),
                        _entry("C", group="Two", run=lambda: ran.append("c"))])
    palette._move(1)
    palette.activate_selected()
    assert ran == ["b"], f"Down ran {ran}"
    palette._move(1)                       # across the "Two" caption
    palette.activate_selected()
    assert ran == ["b", "c"], f"Down across a caption ran {ran}"


def test_a_search_keeps_the_rows_of_one_group_together():
    """A caption is only true of the rows under it.

    The three entries match at three different strengths, so ordering by
    strength alone would put one Skills row, a Tools row and another Skills
    row - and the "Skills" caption would sit over a Tools row.
    """
    entries = [
        _entry("Battery", group="Skills"),
        _entry("battery thing", group="Tools"),
        _entry("notes", subtitle="about the battery", group="Skills"),
    ]
    hits = palette_module.filter_entries(entries, "battery")
    assert [e.group for e in hits] == ["Skills", "Skills", "Tools"]


def test_the_group_that_matched_best_comes_first():
    """A search for a page must not be buried under the skills that mention it.

    The Settings group holds the exact title match and the Skills group holds
    a mention in a prompt, and the caller's order puts Skills first - so
    ordering the groups by the caller's list would hide the page behind every
    skill that merely mentions it.
    """
    entries = [
        _entry("notes", subtitle="about the privacy settings", group="Skills"),
        _entry("Privacy", group="Settings"),
    ]
    hits = palette_module.filter_entries(entries, "privacy")
    assert [e.group for e in hits] == ["Settings", "Skills"]
