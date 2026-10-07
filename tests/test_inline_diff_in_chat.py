"""A file Chronoa rewrote shows its change in the chat, not only in a panel.

The Diff panel had the before and after of every write; the chat had a tool card
reading `path=notes.txt` and "Replaced the text". So the moment a person is
looking - right after asking for the change - showed nothing of it. And the panel
was stale besides: the window builds a panel once and keeps it, and Diff loaded
the undo ring only at build, so a second write never appeared in it.

Driven through the real `edit_file` skill, so the before-image comes from the
real undo ring rather than from a fixture that agrees with the card by
construction.
"""

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()


@pytest.fixture()
def edit(gsettings_env, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from shani_chronoa.config import ChronoaConfig
    ChronoaConfig().set("file-edit-enabled", "true")
    from shani_chronoa.skills import edit_file

    def run(path, old, new):
        return edit_file._run({"path": str(path), "old_string": old, "new_string": new})
    return run


def _walk(widget, out=None):
    out = [] if out is None else out
    out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _diff_lines(widget):
    return [w.get_text() for w in _walk(widget) if isinstance(w, Gtk.Label)
            and {"inline-diff-add", "inline-diff-del"} & set(w.get_css_classes())]


def _note(tmp_path):
    note = tmp_path / "home" / "notes.txt"
    note.write_text("milk\neggs\nbread\n")
    return note


def test_the_card_shows_the_changed_lines(edit, tmp_path):
    from shani_chronoa.gui import blocks
    note = _note(tmp_path)
    out = edit(note, "eggs", "a dozen eggs")
    card = blocks.ToolCallCard("edit_file", {"path": str(note)}, out, True)
    assert card.inline_diff is not None, out
    assert _diff_lines(card) == ["- eggs", "+ a dozen eggs"]


def test_a_refused_write_shows_no_diff(gsettings_env, tmp_path, monkeypatch):
    """Control: with the consent key off nothing is written, so nothing is shown."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from shani_chronoa.gui import blocks
    from shani_chronoa.skills import edit_file
    note = _note(tmp_path)
    out = edit_file._run({"path": str(note), "old_string": "eggs", "new_string": "x"})
    card = blocks.ToolCallCard("edit_file", {"path": str(note)}, out, True)
    assert card.inline_diff is None
    assert note.read_text() == "milk\neggs\nbread\n"


def test_a_second_result_does_not_stack_a_second_diff(edit, tmp_path):
    from shani_chronoa.gui import blocks
    note = _note(tmp_path)
    out = edit(note, "eggs", "a dozen eggs")
    card = blocks.ToolCallCard("edit_file", {"path": str(note)}, "", True)
    card.update(out, True)
    card.update(out, True)
    assert sum(isinstance(w, blocks.InlineDiff) for w in _walk(card)) == 1


def test_show_full_diff_opens_the_panel(edit, tmp_path):
    from shani_chronoa.gui import blocks
    note = _note(tmp_path)
    opened = []
    out = edit(note, "eggs", "a dozen eggs")
    card = blocks.ToolCallCard("edit_file", {"path": str(note)}, out, True,
                               on_show_diff=lambda: opened.append(True))
    buttons = [b for b in _walk(card.inline_diff) if isinstance(b, Gtk.Button)]
    assert [b.get_label() for b in buttons] == ["Show full diff"]
    buttons[0].emit("clicked")
    assert opened == [True]


def test_a_directory_wide_replace_links_to_the_panel_instead_of_guessing(edit, tmp_path):
    from shani_chronoa.gui import blocks
    card = blocks.ToolCallCard("find_and_replace", {"path": str(tmp_path / "home")},
                               "Replaced 2 occurrence(s)", True, on_show_diff=lambda: None)
    assert card.inline_diff is None
    assert any(isinstance(b, Gtk.Button) and b.get_label() == "Show what changed"
               for b in _walk(card))


def test_the_diff_panel_rereads_when_shown_again(edit, tmp_path):
    from shani_chronoa.gui.surfaces import diff
    note = _note(tmp_path)
    edit(note, "eggs", "a dozen eggs")
    page = diff.build(None)
    assert sorted(p.name for p in page.view._files) == ["notes.txt"]
    other = tmp_path / "home" / "todo.txt"
    other.write_text("a\n")
    edit(other, "a", "b")
    page.emit("showing")
    assert sorted(p.name for p in page.view._files) == ["notes.txt", "todo.txt"]


def _transcript():
    from shani_chronoa.gui.widgets import TranscriptView
    return TranscriptView()


def test_a_tool_card_survives_the_reply_that_follows_it(edit, tmp_path):
    """The real order: the skill runs, its card is added, then the reply lands.

    `_replace_blocks` removed every child of the turn, so the card - and the
    diff in it - vanished the moment the answer arrived. Every earlier test
    built a card on its own and never sent a reply after it.
    """
    from shani_chronoa.gui import blocks
    note = _note(tmp_path)
    out = edit(note, "eggs", "a dozen eggs")
    view = _transcript()
    view.add_user_turn("make it a dozen eggs")
    view.add_tool_call("edit_file", {"path": str(note)}, out, True)
    view.add_assistant_turn("Done - a dozen eggs.")
    view.add_assistant_turn("Done - a dozen eggs, as asked.")      # a revised reply
    cards = [w for w in _walk(view) if isinstance(w, blocks.ToolCallCard)]
    assert len(cards) == 1, "the tool card did not survive the reply"
    assert _diff_lines(cards[0]) == ["- eggs", "+ a dozen eggs"]
    texts = [w.get_label() for w in _walk(view) if isinstance(w, Gtk.Label)]
    assert any("as asked" in (t or "") for t in texts), "the revised reply is not shown"
    assert not any(t == "Done - a dozen eggs." for t in texts), "the old reply text stayed"


def test_a_notice_row_survives_the_reply_too():
    view = _transcript()
    view.add_user_turn("hi")
    view.add_notice_row("Older messages were shortened to fit.")
    view.add_assistant_turn("Hello.")
    texts = [w.get_label() for w in _walk(view) if isinstance(w, Gtk.Label)]
    assert "Older messages were shortened to fit." in texts


def test_hunk_headers_count_both_sides():
    """`@@ -3,2 +3,3 @@` for lines 3-4 replaced by three new ones. It read
    `@@ -1,2 +0,3 @@`: the old side came from a removed line's -1."""
    from pathlib import Path
    from shani_chronoa.gui.surfaces import diff
    f = diff.compute_file_diff(Path("g.md"), "a\nb\nc\nd\ne\n", "a\nb\nC1\nC2\nC3\ne\n")
    assert [(h.old_start, h.old_count, h.new_start, h.new_count) for h in f.hunks] == [(3, 2, 3, 3)]
    f = diff.compute_file_diff(Path("g.md"), "a\nb\n", "a\nb\nc\n")
    assert [(h.old_start, h.old_count, h.new_start, h.new_count) for h in f.hunks] == [(3, 0, 3, 1)]


def test_the_diff_panel_lays_out_as_two_columns(edit, tmp_path):
    """The count toolbar belongs to the right column, and the diff scroller
    expands - it was a third column over the file name, and one line tall."""
    from shani_chronoa.gui.surfaces import diff
    note = _note(tmp_path)
    edit(note, "eggs", "a dozen eggs")
    view = diff.build(None).view
    kids = []
    child = view.get_first_child()
    while child is not None:
        kids.append(child)
        child = child.get_next_sibling()
    assert len(kids) == 2, [type(k).__name__ for k in kids]
    assert view._diff_scrolled.get_vexpand()
    texts = [w.get_label() for w in _walk(view) if isinstance(w, Gtk.Label)]
    assert "a dozen eggs" in texts and "eggs" in texts
