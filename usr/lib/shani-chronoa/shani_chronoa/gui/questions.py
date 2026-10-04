"""Questions a tool asks the person (ask_user, permission prompts): shown in the window, answerable by voice."""

import logging
import re
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import GLib



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
