"""`ask_for_text` puts a prompt on screen and takes an answer back.

This path was dead in the shipped app and nothing tested it. `questions.py`
called `_input_entry.set_placeholder_text(...)` on a `Gtk.TextView`, which has no
such method; the surrounding `except` logged *"text prompt could not be put on
screen"* and resolved `""` - so a text prompt asked the user nothing, answered
the tool with no answer, and did so silently.

**No `app.run()` here.** The presenter hands its widget work to `GLib.idle_add`,
and a main loop is not needed to drain it: `MainContext.iteration()` runs one
pass by hand. A file that calls `app.run()` inside pytest can *abort the
process* when a callback raises - GTK4 does not let that reach pytest - and
`test_slash_menu_opens` measured exactly that.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa import ask_bridge  # noqa: E402
from shani_chronoa.gui import questions  # noqa: E402

_WINDOW_SEQ = iter(range(1, 1000))


def _pump(predicate, timeout=10.0):
    """Run the default context until `predicate` holds, or the clock runs out.

    **`iteration(False)`, one pending source at a time** - the presenter's
    `GLib.idle_add` lands here. Returns whether the predicate came true, so a
    caller can tell "it worked" from "we waited and it did not".
    """
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _live_window():
    from shani_chronoa.gui.window import ChronoaWindow

    app = Gtk.Application(
        application_id="test.askfortext." + str(next(_WINDOW_SEQ)))
    return ChronoaWindow(app)


def test_a_text_prompt_reaches_the_window_with_its_placeholder():
    window = _live_window()
    ask_bridge.set_text_presenter(questions.make_text_presenter(lambda: window))
    answers = []

    def worker():
        answers.append(ask_bridge.ask_for_text(
            "What should the note be called?", "a short title", timeout=10))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    assert _pump(lambda: window._pending_question is not None), (
        "the text prompt never reached the window - and no error was raised, "
        "which is how this path was dead for so long")
    assert window._question_row.get_visible(), "the prompt row is not visible"
    assert window._input_placeholder.get_label() == "a short title", (
        f"the composer says {window._input_placeholder.get_label()!r} instead "
        f"of the placeholder that was asked for")

    window._resolve_question("Meeting notes")
    thread.join(timeout=5)
    assert answers == ["Meeting notes"], f"the answer did not come back: {answers}"


def test_the_row_and_the_placeholder_go_back_after_a_text_prompt():
    window = _live_window()
    ask_bridge.set_text_presenter(questions.make_text_presenter(lambda: window))
    thread = threading.Thread(
        target=lambda: ask_bridge.ask_for_text("Name it", "a title", timeout=10),
        daemon=True)
    thread.start()
    assert _pump(lambda: window._pending_question is not None)

    window._resolve_question("Done")
    thread.join(timeout=5)

    assert not window._question_row.get_visible(), (
        "the prompt row is still on screen with nothing to answer")
    assert window._pending_question is None, "the question is still pending"
    assert window._input_placeholder.get_label() == "Type a message…", (
        f"the composer still says {window._input_placeholder.get_label()!r}")


def test_the_caller_is_actually_blocked_while_the_prompt_is_up():
    """The control for the test above.

    Without it, "the placeholder was set" passes on a build where the prompt
    appeared for an instant and the tool was answered at once - and an
    `ask_for_text` that never blocks is not a prompt.
    """
    window = _live_window()
    ask_bridge.set_text_presenter(questions.make_text_presenter(lambda: window))
    answers = []

    def worker():
        answers.append(ask_bridge.ask_for_text("Name it", "a title", timeout=10))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert _pump(lambda: window._pending_question is not None)
    time.sleep(0.2)
    assert answers == [], f"the caller was not blocked: {answers}"
    assert thread.is_alive(), "the calling thread finished before being answered"
    window._resolve_question("Later")
    thread.join(timeout=5)
