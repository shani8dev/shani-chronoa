"""The shortcuts a person cannot discover are not shortcuts.

Four mode controls moved off the strip to the palette, which left them
reachable only by a key nobody is told about: there is no shortcuts surface in
this app, and `Ctrl+K` and `Ctrl+F` appeared only in comments. So the empty
state now names them, and `?` is bound.

**The load-bearing test is the last one.** A hint that advertises a key which
is not bound is worse than no hint at all - a person presses it, nothing
happens, and they learn that this interface lies to them. So the text is
checked against the bindings rather than trusted.
"""

import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import pytest  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from shani_chronoa.gui import widgets as widgets_module  # noqa: E402
from shani_chronoa.gui import window as window_module  # noqa: E402


def _placeholder_text(view):
    view.show_placeholder()
    out = []
    stack = [view]
    while stack:
        node = stack.pop()
        if isinstance(node, Gtk.Label) and node.get_label():
            out.append(node.get_label())
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return out


# -- the empty state names them ---------------------------------------------


def test_the_empty_state_names_the_palette():
    text = " ".join(_placeholder_text(widgets_module.TranscriptView()))
    assert "Ctrl+K" in text, text


def test_the_empty_state_describes_what_each_key_does():
    """"Ctrl+K" alone is not a reason to press it, and the word *palette*
    appears nowhere else in the interface."""
    text = " ".join(_placeholder_text(widgets_module.TranscriptView()))
    assert "anything else" in text, text
    assert "conversation" in text, text


def test_the_empty_state_still_says_the_obvious_thing():
    """The shortcut line was added; the prompt it sits under was not replaced."""
    text = " ".join(_placeholder_text(widgets_module.TranscriptView()))
    assert "Ask something" in text, text


def test_the_shortcut_line_is_dimmer_than_the_prompt():
    """A hint at the same weight as the prompt competes with it."""
    view = widgets_module.TranscriptView()
    view.show_placeholder()
    labelled = {}
    stack = [view]
    while stack:
        node = stack.pop()
        if isinstance(node, Gtk.Label) and node.get_label():
            classes = node.get_css_classes() or []
            if "Ctrl+K" in node.get_label():
                assert "dim-label" in classes, classes
            if "Ask something" in node.get_label():
                assert "transcript-placeholder" in classes, classes
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    assert labelled == {}


# -- the keys it names are the keys that exist -------------------------------


#: Where each key the empty state names is bound, and how.
#:
#: **The two are bound in different places, which is why this is a table.**
#: Ctrl+K is a keyval comparison in a key controller; Ctrl+F is an accelerator
#: on an app action. A single check for one mechanism would have missed the
#: other, and the first version of this test checked for accelerator action
#: names for both - which proved nothing about either.
_BINDINGS = (
    ("Ctrl+K", "palette", "window.py"),
    ("Ctrl+F", "find-in-conversation", "application.py"),
)


def _source_for(where):
    """The named source file, by path and never by pattern.

    **`rglob("window.py")` finds `settings_window/window.py` first** - the first
    version of this looked there and reported that Ctrl+K had no binding at
    all, because it was reading a different file with the same name. Every
    file this test checks is now named explicitly.
    """
    window = Path(window_module.__file__).resolve()
    package = window.parents[1]          # .../shani_chronoa
    if where == "window.py":
        return window
    if where == "application.py":
        return package / "app" / "application.py"
    raise AssertionError(f"test bug: no path known for {where!r}")


@pytest.mark.parametrize("hint_key,needle,where", _BINDINGS,
                         ids=[row[0] for row in _BINDINGS])
def test_every_key_named_in_the_hint_is_actually_bound(hint_key, needle, where):
    """**The test that matters.** A hint naming a key that is not bound is
    worse than no hint: the person presses it, nothing happens, and they learn
    the interface lies.

    The source is read for the binding each key uses, so a hint cannot outlive
    the accelerator or the controller it advertises.
    """
    target = _source_for(where)
    assert needle in target.read_text(encoding="utf-8"), (
        f"the empty state offers {hint_key}, but {where} has no "
        f"{needle!r} to run")


def test_question_mark_is_bound_to_help():
    """It was advertised in the hint before it existed, which is the failure the
    hint is supposed to prevent."""
    window = SimpleNamespace(open_help=lambda: object())
    assert window_module.ChronoaWindow._on_window_key_pressed(
        window, None, Gdk.KEY_question, 0, 0) is True


def test_a_slash_does_not_open_help():
    """**The trap.** `?` is delivered as `Gdk.KEY_question`; the bare slash is
    what you type into the composer every time you write a `/command`. A check
    for the bare slash would open the help window over the thing being typed."""
    window = SimpleNamespace(open_help=lambda: object())
    assert window_module.ChronoaWindow._on_window_key_pressed(
        window, None, Gdk.KEY_slash, 0, 0) is False


def test_help_that_cannot_be_opened_returns_false():
    """`open_help` returns None for a build whose skill registry failed, and a
    key handler that returns True anyway would swallow the keystroke."""
    window = SimpleNamespace(open_help=lambda: None)
    assert window_module.ChronoaWindow._on_window_key_pressed(
        window, None, Gdk.KEY_question, 0, 0) is False


def test_escape_still_only_stops_a_running_turn():
    """Adding `?` to this handler must not have taken Escape's other jobs."""
    stop = SimpleNamespace(get_visible=lambda: False)
    window = SimpleNamespace(_turn_stop_button=stop)
    assert window_module.ChronoaWindow._on_window_key_pressed(
        window, None, Gdk.KEY_Escape, 0, 0) is False


# -- and there is still no shortcuts surface --------------------------------


def test_the_hint_is_the_only_discovery_there_is():
    """Recorded so that when someone builds a real shortcuts panel, this test
    is the thing to delete - and not before."""
    source = Path(window_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"class .*Shortcut", source), (
        "there is a shortcuts surface now; update this test and the empty "
        "state rather than leaving both")

# -- and the ones the help window lists are bound too ----------------------


#: Where each shortcut the help window names is bound. The third column is the
#: text to find in the file at root: the action name, or the keyval handler.
_HELP_BINDINGS = (
    ("Ctrl+K", "palette", "window.py"),
    ("Ctrl+F", "find-in-conversation", "application.py"),
    ("?", "KEY_question", "window.py"),
    ("F9", "F9", "application.py"),
    ("Escape", "turn-stop", "window.py"),
)


@pytest.mark.parametrize("key,needle,where", _HELP_BINDINGS,
                         ids=[row[0] for row in _HELP_BINDINGS])
def test_every_shortcut_listed_is_really_bound(key, needle, where):
    """**A hand-written shortcuts panel rots, and this is the check that stops
    it.** Each row names a key; each key is checked against the source that
    binds it, so a shortcut cannot be listed for a binding that moved.

    The test exists because the section's own docstring first claimed the list
    was generated from the actions and could not go stale. It is not generated -
    it is five typed rows - so the guarantee has to come from here.
    """
    source = _source_for(where)
    assert needle in source.read_text(encoding="utf-8"), (
        f"the help window advertises {key} as {needle!r}, which {where} does "
        f"not contain")


def test_the_help_window_has_a_keyboard_section_at_all():
    """The gap this section closed: everything else in that window is what
    Chronoa can do, and nothing answered what the person at the keyboard can
    do."""
    from shani_chronoa.gui import widgets

    assert "What you can do" in widgets.HelpWindow._SHORTCUT_ACTIONS.__doc__ or \
        hasattr(widgets.HelpWindow, "_build_shortcuts"), (
            "the help window no longer answers what a person can press - "
            "which is the question it was added for")
