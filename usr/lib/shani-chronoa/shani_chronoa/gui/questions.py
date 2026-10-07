"""Questions a tool asks the person (ask_user, permission prompts): shown in the window, answerable by voice."""

import logging
import re
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import GLib, Gtk


logger = logging.getLogger(__name__)

def _norm_words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", text.lower().replace("\u2019", "'"))


def match_spoken_option(spoken: str, options: list) -> str:
    """The option a spoken answer names, else the words as said.

    Strict on purpose, because some options grant permissions: the answer
    must equal an option or begin with ALL of it ("allow this once, please"),
    after case and punctuation are dropped, and exactly one option may match.
    Anything else is passed through as said, which a permission prompt reads
    as no - the same as an unrecognised typed answer.
    """
    said = _norm_words(spoken)
    hits = []
    for option in options:
        want = _norm_words(option)
        if want and said[: len(want)] == want:
            hits.append(option)
    # "allow this once" must not also count as a shorter option it begins with
    if len(hits) > 1:
        longest = max(len(_norm_words(h)) for h in hits)
        hits = [h for h in hits if len(_norm_words(h)) == longest]
    return hits[0] if len(hits) == 1 else spoken.strip()


def make_question_presenter(window_getter):
    """Build the `ask_bridge` presenter that asks on the main window.

    This is the missing half of the permission-prompt path. `permissions.decide()`
    and the `ask_user` skill both call `ask_bridge.ask()`, and `ask_bridge` is
    only ever useful once something installs a presenter - `has_presenter()` is
    literally `_presenter is not None`, so with nothing installed every gated
    tool silently refuses and the questions are never asked. Nothing in the
    application did install one, which is the whole reason both features were
    dead in the running app.

    The threading is the whole difficulty and it is not negotiable:
    `ask()` is called from the assistant's tool loop, which runs on
    `AsyncBridge`'s background thread, and constructing or touching a widget off
    the GTK thread is undefined behaviour. So this presenter does only the two
    things that are safe off-thread - it makes the event, and it queues the
    widget work with `GLib.idle_add`, which GLib runs on the main loop whatever
    thread queued it. The calling thread blocks on that event; the GTK side sets
    it. One hand-off, one direction, and the answer lives on the event object
    because that is the only place `ask_bridge` reads it from
    (`make_event()` returns the pair precisely so no implementation invents its
    own stash).

    `window_getter` is a callable, not a window, because the window does not
    exist yet when the presenter is installed: `do_startup` runs before
    `do_activate` builds it. It is resolved per question instead. A `None` window
    means nobody is present, and is answered immediately with `""` rather than
    held for the full timeout on an answer that cannot arrive.

    **Capacity is one.** `ask_bridge` admits four prompts at once; this window
    has a single question row, and a second prompt supersedes the first -
    resolving it as no answer - rather than queueing behind it. That is the same
    call `ask_bridge` makes at its own limit (refuse, do not queue), it fails in
    the safe direction, and in the shipped app both prompts originate on the one
    tool loop thread, so they are sequential in any case.
    """
    from shani_chronoa import ask_bridge

    def _show(window, question, options, resolve) -> bool:
        """Runs on the GTK thread: build the prompt, or fail honestly."""
        try:
            window.show_question(question, options, resolve)
        except Exception as exc:  # noqa: BLE001 - a prompt that cannot appear is not a choice
            logger.warning("question could not be put on screen: %s", exc)
            resolve("")
        return GLib.SOURCE_REMOVE

    def present(question: str, options: list) -> "threading.Event":
        """Queue `question` for the window and hand back the event to wait on."""
        done, resolve = ask_bridge.make_event()
        window = window_getter()
        if window is None:
            # No window is not a question anyone can answer, and "nobody
            # answered" is what "" already means everywhere else in this module.
            # Resolving now is what keeps a headless-ish call from sitting on a
            # 120-second wait for a prompt that will never appear.
            resolve("")
            return done
        # `list(options)`: ask_bridge already copies, and show_question reads the
        # list on the GTK thread long after this call has returned.
        GLib.idle_add(_show, window, question, list(options), resolve)
        return done

    return present


def make_text_presenter(window_getter):
    """The `ask_for_text` presenter, built the way the question one is.

    Same threading: off the GTK thread we only make the event and queue the
    widget call with `GLib.idle_add`. The only difference from
    `make_question_presenter` is what the answer *is* - a typed sentence rather
    than one of the buttons - so it reuses the same question row and the same
    "type instead of tapping" path the main window already answers questions
    with.

    **`ask_bridge.set_text_presenter` had no caller in the tree**, so the one
    free-form prompt — "Provide feedback" in a permission question — resolved
    to `""` every time: a dead control. Installed next to its question sibling
    in `application.py`, so the option gate in `permissions.options_with_cancel()`
    now sees a presenter and shows the choice.
    """
    from shani_chronoa import ask_bridge

    def _show(window, prompt, placeholder, done, resolve) -> bool:
        """Runs on the GTK thread: put the prompt on screen, or fail honestly."""
        try:
            label = Gtk.Label(label=prompt)
            label.set_wrap(True)
            label.set_xalign(0.0)
            window._clear_question_widgets()
            window._question_widgets.append(label)
            window._question_row.append(label)
            window._question_row.set_visible(True)
            window._input_entry.set_placeholder_text(
                placeholder or "Type your answer…")
            # `_resolve_question` clears the row and resets the placeholder when
            # the typed answer arrives, so this only has to hand it the answer.
            window._pending_question = resolve
        except Exception as exc:  # noqa: BLE001 - a prompt that cannot appear is not a choice
            logger.warning("text prompt could not be put on screen: %s", exc)
            resolve("")
        return GLib.SOURCE_REMOVE

    def present(prompt: str, placeholder: str) -> "threading.Event":
        done, resolve = ask_bridge.make_event()
        window = window_getter()
        if window is None:
            resolve("")
            return done
        GLib.idle_add(_show, window, prompt, placeholder, done, resolve)
        return done

    return present
