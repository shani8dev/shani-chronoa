"""A tool call says whether it worked, rather than showing an icon and hoping.

Whether a call succeeded was carried by one glyph and nothing else:

    icon = Gtk.Image.new_from_icon_name(
        "object-select-symbolic" if ok else "dialog-warning-symbolic")

**Measured: the card set no accessible label on itself.** The `update_property`
calls in that module belong to a copy button, the expand toggle and a label -
never to the row. So a screen reader announced `file write, path=/tmp/report.pdf`
and never said whether the file was written.

And it is not only for screen readers. The two icons are the same size, the same
weight and in the same place, so telling a succeeded call from a failed one is a
skill you have to already have. That is the claim-without-a-mechanism shape this
repository keeps finding - and it was hiding in the most-used element in the app,
on every tool call ever made, while two dead controls and a lying chip were
much louder and much rarer.

**Both icons do ship in both themes**, checked here rather than assumed. That is
the same check that found `audio-input-microphone-high-symbolic` present in Yaru
and absent from Adwaita, which is a real instance of the risk in this codebase.
"""

import pathlib
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")

BLOCKS = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa/gui/blocks.py")


def _src():
    return BLOCKS.read_text(encoding="utf-8")


def test_the_row_carries_an_accessible_label():
    src = _src()
    card = src.split("class ToolCallCard", 1)[1].split("\nclass ", 1)[0]
    assert "AccessibleProperty.LABEL" in card, (
        "the card sets no accessible label on itself, so a screen reader never "
        "hears whether the call worked - only the copy button and the toggle "
        "have one")
    assert "this worked" in card and "this did not work" in card, (
        "the label does not say which of the two happened")


def test_a_failed_call_says_so_in_visible_words():
    """Not only to a screen reader. The two icons are the same size and weight."""
    src = _src()
    assert 'label="did not work"' in src, (
        "a failed call is still distinguished only by an icon a person has to "
        "already know how to read")


def test_a_succeeded_call_is_not_labelled_as_a_failure():
    """The label follows `ok`, so it cannot appear on the happy path.

    Asserted by construction rather than by reading: a label added above the
    `if not ok:` guard would claim every call failed, which is the opposite
    error and just as quiet.
    """
    src = _src()
    guard = src.split("if not ok:", 1)
    assert len(guard) == 2, "the visible label is not inside an `if not ok:` guard"
    tail = guard[1]
    assert tail.split("summary = ", 1)[0].count('label="did not work"') == 1, (
        "the label is not between the guard and the summary")


def test_the_icon_carries_a_tooltip():
    """Both states, so a pointer user gets the answer without the words being seen."""
    src = _src()
    assert '"This worked" if ok else "This did not work"' in src, (
        "the icon's tooltip does not state which of the two happened")


def test_both_icons_exist_in_both_themes():
    """The theme risk, measured rather than assumed.

    `audio-input-microphone-high-symbolic` is present in Yaru and absent from
    Adwaita on this machine, so a symbolic name in this codebase is a guess
    about another package's contents and the guess is sometimes wrong. Here the
    guess is right, which is worth recording as much as a wrong one would be.
    """
    for icon in ("object-select-symbolic", "dialog-warning-symbolic"):
        found = list(pathlib.Path("/usr/share/icons").rglob(f"{icon}.svg"))
        assert found, f"{icon} ships in no installed theme at all"
        # /usr/share/icons/<theme>/... - index 4, not 3. The first version read
        # index 3, which is the literal "icons", so every icon looked like it
        # lived in one theme and this test failed for a reason that had nothing
        # to do with the icons.
        themes = {p.parts[4] for p in found}
        assert len(themes) >= 2, (
            f"{icon} exists only under {themes}, so a theme without it would "
            f"draw a blank box - and for the failure icon that would make a "
            f"failed call look like a successful one")


def test_a_card_can_be_built_and_says_the_right_thing():
    """Driven, not read. The string checks above could all pass on a comment."""
    from shani_chronoa.gui.blocks import ToolCallCard

    good = ToolCallCard("file_write", {"path": "/tmp/a"}, "written", True)
    bad = ToolCallCard("file_write", {"path": "/tmp/a"}, "not permitted", False)

    def labels(widget):
        out = []
        stack = [widget]
        while stack:
            node = stack.pop()
            if isinstance(node, Gtk.Widget):
                try:
                    value = node.get_accessible_role()
                except Exception:                        # noqa: BLE001
                    value = None
                if value is not None:
                    out.append(node)
                child = node.get_first_child()
                while child is not None:
                    stack.append(child)
                    child = child.get_next_sibling()
        return out

    def text_of(widget):
        found = []
        stack = [widget]
        while stack:
            node = stack.pop()
            if isinstance(node, Gtk.Label) and node.get_label():
                found.append(node.get_label())
            child = node.get_first_child() if isinstance(node, Gtk.Widget) else None
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return found

    assert "did not work" not in " ".join(text_of(good)), (
        "a successful call is labelled as a failure")
    assert "did not work" in " ".join(text_of(bad)), (
        f"a failed call does not say so in words: {text_of(bad)}")
    assert len(labels(good)) > 0 and len(labels(bad)) > 0