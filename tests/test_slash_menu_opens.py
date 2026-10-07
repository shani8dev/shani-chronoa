"""The slash-command menu is reachable from the keyboard, and pressing a row does something.

`tests/test_context_meter_and_commands.py` covers the *registry*: six commands,
each with a runnable action, `/undo` going through the real skill so the
permission layers are not bypassed. It cannot tell whether the menu is
**reachable**, and that is the shape of dead control this repo keeps meeting: a
popover built, wired to the entry's `changed` signal, and never once shown by a
person.

So this drives the real window in a real main loop and inserts a real `/`
through `insert_text()` — the same call a keystroke makes, and the same reason
the tests here cannot use `set_text()`, which emits no `changed` and would prove
nothing about the hook. Three things are checked, in order of how badly a false
pass would hurt:

1. typing `/` pops the menu up, and it lists the commands;
2. pressing a row **runs something** — `/diagnostics` changes which surface the
   content view is showing, so a menu whose buttons are painted and inert
   fails here;
3. typing an argument (`/help me`) takes the menu back down, because a popover
   left up over a sentence is the menu arguing with the person typing it.
"""

import sys
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.app import ChronoaApplication  # noqa: E402
from shani_chronoa.gui import commands as slash  # noqa: E402

_ACTIVATE_MS = 150
_PROBE_MS = 500


def _in_a_live_window(steps, monkeypatch):
    """Run `steps` on one real window, a main-loop turn apart.

    The popover is parented in an idle callback (`GLib.idle_add` in the
    window's own setup), so this cannot be done without running the loop: a
    popover with no parent does not pop up, and a test that concluded
    "the menu never opens" from that would be measuring its own harness.
    """
    monkeypatch.setattr(ChronoaApplication, "_open_setup",
                        lambda self, *_a: None, raising=True)
    app = ChronoaApplication()
    app.hold()
    results, box, cursor = [], {}, {"i": 0}

    def step():
        if cursor["i"] >= len(steps):
            app.quit()
            return False
        fn = steps[cursor["i"]]
        cursor["i"] += 1
        try:
            results.append(fn(app.window))
        except BaseException as exc:                      # noqa: BLE001
            box["error"] = exc
            app.quit()
            return False
        GLib.timeout_add(_PROBE_MS, step)
        return False

    GLib.timeout_add(_ACTIVATE_MS, lambda: (app.activate(), False)[1])
    GLib.timeout_add(_ACTIVATE_MS + _PROBE_MS, step)
    GLib.timeout_add(20000, lambda: (app.quit(), False)[1])
    try:
        app.run(["slashmenu-probe"])
    finally:
        app.release()
    if "error" in box:
        raise box["error"]
    assert len(results) == len(steps), (
        f"only {len(results)} of {len(steps)} steps ran, so the rest of this "
        f"file's assertions were skipped")
    return results


def _type(window, text):
    """What a keystroke does: `insert_text` emits `changed`, `set_text` does not.

    The clear goes through `set_text("")` deliberately: it is the one call that
    does *not* emit `changed`, so clearing cannot be mistaken for the menu
    closing for the right reason. (`delete_selection` is GTK3 - the first
    version of this helper used it and raised on the first real run.)
    """
    entry = window._input_entry
    entry.set_text("")
    # `insert_text(text, position)` with position -1 appends, which is where a
    # keystroke's text lands on an empty field. (Two arities wrong in a row here:
    # `delete_selection` is GTK3 and `insert_text` needs the position.)
    entry.insert_text(text, -1)
    return text


def _menu_labels(window):
    labels, stack = [], []
    child = window._command_list.get_first_child()
    while child is not None:
        stack.append(child)
        child = child.get_next_sibling()
    while stack:
        node = stack.pop(0)
        if isinstance(node, Gtk.Label):
            text = node.get_text().strip()
            if text.startswith("/"):
                labels.append(text)
        sub = node.get_first_child()
        while sub is not None:
            stack.append(sub)
            sub = sub.get_next_sibling()
    return sorted(set(labels))


def _row_texts(button):
    """The visible text inside a row button.

    `get_accessible_property()` does not exist in this PyGObject — a probe that
    called it once read every name as `""` and nearly reported a nameless UI —
    so rows are matched on the labels they actually contain.
    """
    found, stack = [], []
    child = button.get_first_child()
    while child is not None:
        stack.append(child)
        child = child.get_next_sibling()
    while stack:
        node = stack.pop(0)
        if isinstance(node, Gtk.Label):
            found.append(node.get_text().strip())
        sub = node.get_first_child()
        while sub is not None:
            stack.append(sub)
            sub = sub.get_next_sibling()
    return " ".join(found)


def _press(window, wanted):
    """Click the first button whose own labels mention `wanted`."""
    stack, rows = [], []
    child = window._command_list.get_first_child()
    while child is not None:
        stack.append(child)
        child = child.get_next_sibling()
    while stack:
        node = stack.pop(0)
        if isinstance(node, Gtk.Button):
            rows.append(_row_texts(node))
            if wanted in rows[-1]:
                node.emit("clicked")
                return True, rows[-1]
        sub = node.get_first_child()
        while sub is not None:
            stack.append(sub)
            sub = sub.get_next_sibling()
    return False, rows


def test_typing_a_slash_opens_the_menu_and_its_rows_do_something(monkeypatch):
    def type_slash(window):
        _type(window, "/")
        return None

    def read_menu(window):
        return (bool(window._command_menu.get_visible()), _menu_labels(window))

    def type_argument(window):
        _type(window, "/help me")
        return None

    def read_closed(window):
        return bool(window._command_menu.get_visible())

    def press_diagnostics(window):
        pressed, rows = _press(window, "diagnostics")
        # A row **completes** the command rather than running it - cline's
        # behaviour, and the reason the menu is a discovery aid and not a
        # one-click macro. Asserting the click alone would have been the
        # vacuous check this file was written to avoid: the window pre-builds
        # every surface in an idle callback, so `"diagnostics" in
        # window._surface_pages` is true from startup and stayed green with
        # `/diagnostics` neutered (measured: 3 runs, 3 passes, command dead).
        return (pressed, rows, window._input_entry.get_text())

    def submit_it(window):
        window._submit_input()
        # `get_visible_page()` is this libadwaita's spelling; there is no
        # `get_visible_child` (the suggestion names it).
        visible = window._content_view.get_visible_page()
        return (visible is not None,
                visible is getattr(window, "_surface_pages", {}).get("diagnostics"))

    results = _in_a_live_window(
        [type_slash, read_menu, type_argument, read_closed, press_diagnostics,
         submit_it],
        monkeypatch)
    # Steps 0, 2 and 4 are actions and return None; read the rest by hand rather
    # than destructuring, so adding a step later cannot silently shift three
    # values into the wrong names.
    menu_up, labels = results[1]
    menu_down = results[3]
    pressed, rows, completed_text = results[4]
    something_shown, is_diagnostics = results[5]

    assert menu_up, (
        "typing '/' left the command menu closed: the popover is built and "
        "wired to the entry's `changed` signal, and nothing ever opens it")
    assert labels, "the menu is up and lists nothing"
    for name in slash.names():
        assert any(row == f"/{name}" or row.startswith(f"/{name}") for row in labels), (
            f"/{name} is registered but the open menu does not list it: {labels}")

    assert not menu_down, (
        "the menu stayed up over '/help me', so it is left arguing with "
        "whoever is typing a sentence")
    assert pressed, f"no menu row for /diagnostics; rows were {rows}"
    assert completed_text.strip() == "/diagnostics", (
        f"pressing the row left the field as {completed_text!r}; a row is "
        f"meant to complete the command, not run it silently")
    assert something_shown and is_diagnostics, (
        "the completed command was submitted and the Diagnostics surface did "
        "not come up: the menu offers commands that do nothing")


def test_the_menu_does_not_stay_up_for_an_ordinary_sentence(monkeypatch):
    """The negative case on its own, so it cannot pass by the row test failing.

    A popover that is up for any text at all is not a discovery aid, it is an
    overlay, and the check above would still pass if the *up* condition were
    "any text".
    """
    def type_word(window):
        _type(window, "how much disk")
        return None

    def read(window):
        return bool(window._command_menu.get_visible())

    results = _in_a_live_window([type_word, read], monkeypatch)
    up = results[-1]
    assert not up, "the command menu opened for a sentence that has no slash in it"