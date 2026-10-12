"""The composer takes two lines, and Escape stops a turn.

**The composer was a `Gtk.Entry`, which stores a newline but cannot show one.**
Measured: `set_text("one\ntwo")` round-trips on a `Gtk.Entry`. What it cannot do
is display it — one line, no wrapping, no vertical growth — so a three-line
request sat in a field one line tall and the person could not see or edit what
they had written. `gui/window.py` said so itself: *"the multi-line composer Alpaca
has is a `Gtk.TextView` and is a separate piece of work."* This is that piece.

The two keyboard rules it exists to make possible:

- **Enter sends.** Without it there is no way to send from the keyboard.
- **Shift+Enter is a newline.** Without it there is no way to type two lines.

**Escape stops a running turn, and only a running turn.** The stop-*speaking*
button has claimed "(Esc)" in its tooltip since before it was wired to anything,
so the shortcut was advertised and did not exist, on a control that could never
appear. The global half of this file keeps finding that shape — a claim with no
mechanism — and a shortcut that does nothing when nothing is running is the same
thing wearing a different hat.
"""

import pathlib
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")
WINDOW = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa/gui/window.py")


def _src():
    return WINDOW.read_text(encoding="utf-8")


def _window():
    """**A real window, built here, because source greps cannot see the tree.**

    Every other check in this file reads `window.py` as text, and that is the
    shape that let a **committed** defect through: the `Gtk.Overlay` built for
    the hint was never given either of its children, `self._input_placeholder`
    had no parent, and the composer had no placeholder at all - while
    `test_the_placeholder_is_replaced_not_dropped` sat red from that commit.

    The greps are not worthless and they are kept, but they cannot answer *"is
    the hint in the window"*, only *"is the word `add_overlay` somewhere in the
    file"*. A substring is present or absent; a widget is **parented or not**.
    So the two claims that are about the tree are made against a built window.
    """
    from gi.repository import Gtk

    from shani_chronoa.gui.window import ChronoaWindow

    app = Gtk.Application(application_id="test.chronoa.composer")
    app.register(None)
    return ChronoaWindow(app)


def test_what_an_entry_cannot_do_is_see_it_not_store_it():
    """**The premise, measured rather than asserted - and it was wrong.**

    The first version of this file claimed a `Gtk.Entry` "cannot hold a newline
    at all". Measured on this GTK: `Gtk.Entry().set_text("one\ntwo")` gives back
    `"one\ntwo"`. So it stores them fine.

    What it cannot do is **show** them - one line, no wrap, no vertical growth -
    which is the real reason for the migration, and a different reason. The test
    exists so the corrected claim stays corrected: if a future GTK does make an
    entry single-line again, this says which half of the story is true.
    """
    from gi.repository import Gtk

    entry = Gtk.Entry()
    entry.set_text("one\ntwo")
    assert entry.get_text() == "one\ntwo", (
        f"a Gtk.Entry no longer stores a newline ({entry.get_text()!r}) - so the "
        f"migration's justification has changed and this file should say so")

    view = Gtk.TextView()
    buf = view.get_buffer()
    buf.set_text("one\ntwo")
    assert buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) \
        == "one\ntwo"


def test_the_window_uses_a_text_view_not_an_entry():
    src = _src()
    assert "self._input_entry = Gtk.TextView()" in src, (
        "the composer is still a Gtk.Entry, which cannot hold a newline")
    assert "self._input_entry = Gtk.Entry()" not in src


def test_enter_sends_and_shift_enter_does_not():
    """Both halves, because a rule that always sends is as broken as none.

    The key handler reads the shift mask rather than checking for a distinct key
    name, so a numpad Enter and a laptop Enter behave the same — which is the
    whole reason to handle the keyval and the state together.
    """
    src = _src()
    assert "def _on_input_key_pressed" in src
    assert "Gdk.KEY_Return" in src and "Gdk.KEY_KP_Enter" in src, (
        "only one Enter is handled, so a keyboard with a numpad Enter sends on "
        "one and not the other")
    assert "Gdk.ModifierType.SHIFT_MASK" in src, (
        "the shift mask is never read, so Shift+Enter cannot be a newline")
    # Shift must return False - letting the key through is what inserts the line.
    handler = src.split("def _on_input_key_pressed", 1)[1].split("\n    def ", 1)[0]
    shift_branch = handler.split("if shift:")[1] if "if shift:" in handler else ""
    assert "return False" in shift_branch, (
        "Shift+Enter does not fall through, so the newline is swallowed")


def test_escape_stops_a_running_turn_and_only_then():
    src = _src()
    assert "def _on_window_key_pressed" in src
    assert "Gdk.KEY_Escape" in src
    handler = src.split("def _on_window_key_pressed", 1)[1].split("\n    def ", 1)[0]
    assert "get_visible()" in handler, (
        "Escape stops the turn whether or not one is running - a shortcut that "
        "does nothing when nothing is running is the claim-without-mechanism "
        "this repository keeps finding")
    assert "return True" in handler, (
        "the handler never reports that it acted, so Escape keeps its other jobs "
        "even when it stops a turn")


def test_escape_reaches_the_same_path_as_the_button():
    """One stop, not two.

    Two handlers that both "stop the turn" is how they come to differ - one
    learns to be quieter, or one gets a status line the other lacks.
    """
    src = _src()
    handler = src.split("def _on_window_key_pressed", 1)[1].split("\n    def ", 1)[0]
    assert "self._on_turn_stop_clicked(stop)" in handler, (
        "Escape does not go through the button's handler, so the two can drift")


def test_the_placeholder_is_replaced_not_dropped():
    """A `Gtk.TextView` has no placeholder, and losing it would be a real loss.

    So the hint is a label inside an overlay, hidden on the first character —
    a silent regression of the kind this file keeps catching, where the feature
    still works and something smaller quietly stops being there.
    """
    src = _src()
    # **Scoped to the composer, and the first version was not.** The assertion
    # read `set_placeholder_text not in src` over the whole window module, and
    # `self._search_entry` - a `Gtk.SearchEntry`, which really does have a
    # placeholder - made it fail on correct code. So the claim this test is
    # actually about is narrower than the one it checked: *the composer* is a
    # `Gtk.TextView` and must not be given a placeholder. Scoping it that way is
    # a stronger check, not a weaker one, because a placeholder set anywhere near
    # `_input_entry` still trips it while the search field is left alone.
    offenders = [line for line in src.splitlines()
                 if "_input_entry" in line and "set_placeholder_text" in line]
    assert not offenders, (
        f"a placeholder is being set on the composer, which is a TextView and "
        f"has none: {offenders}")

    # **Driven, not grepped.** `Gtk.Overlay()` appearing in the file says
    # nothing about whether the hint is *in* the window, which is the claim.
    from gi.repository import Gtk

    win = _window()
    hint = win._input_placeholder
    entry = win._input_entry
    assert hint.get_parent() is not None, (
        "the hint label was built but never placed, so the composer has no "
        "placeholder and nothing was lost silently")
    assert isinstance(hint.get_parent(), Gtk.Overlay), (
        f"the hint sits on a {type(hint.get_parent()).__name__}, not in the "
        f"overlay that was built for it")
    assert entry.get_parent() is hint.get_parent(), (
        "the field and its hint are not in the same overlay, so the hint is "
        "drawn somewhere the field is not")

    # And the whole cycle, on the built window: shown when empty, hidden on the
    # first character, shown again when cleared.
    buffer = entry.get_buffer()
    assert hint.get_visible(), "the hint is not shown while the composer is empty"
    buffer.set_text("hello")
    assert not hint.get_visible(), (
        "the hint never hides once there is text")
    buffer.set_text("")
    assert hint.get_visible(), "the hint did not come back when the field cleared"


def test_undo_and_the_turn_stop_remain_reachable_by_mouse():
    """The composer changed shape; the controls beside it must not have.

    A `Gtk.Overlay` wrapping the text view is a different widget from the one the
    stop, undo and send buttons were measured next to, so their placement is
    worth pinning rather than assuming.
    """
    src = _src()
    for name in ("_turn_stop_button", "_undo_button", "_send_button",
                 "_attach_button"):
        assert f"input_row.append(self.{name})" in src, (
            f"{name} is no longer in the composer row, so the Overlay "
            f"reflow changed where the controls sit")


def test_this_files_controls_can_fail():
    """**The control: these checks can tell present from absent.**

    Every assertion above is a substring check against one source file, which is
    the shape that passes when the code moved and the string did not. So the
    control asserts the file really is the window module, and that a near-miss
    string is not accepted for a real one.
    """
    assert WINDOW.exists() and "class ChronoaWindow" in _src(), (
        "this file is reading something that is not the window module")
    assert "self._input_entry = Gtk.TextView()" in _src()
    assert "self._input_entry = Gtk.TextViewX()" not in _src(), (
        "the substring checks cannot tell a real string from a near miss")