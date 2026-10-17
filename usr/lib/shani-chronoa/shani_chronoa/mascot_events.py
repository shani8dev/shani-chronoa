"""The mascot's event bus: one listener, fire-and-forget.

`mascot.Mascot.notice(kind, text)` has existed since the companion was drawn -
a face for a landline call, a bubble, and a map of nine event kinds - but only
`call` ever reached it. The reason is structural rather than an oversight: the
decisions worth a face are made *where the mascot is not*. A permission is
decided off the tool loop's thread, a turn ends in `conversation`, a tool is
about to run in `tools`, and none of those modules holds the `Mascot` object -
threading it through every one of them would be a second wiring beside each and
would invert the dependency, the decision layer importing the overlay.

So the bus is a single module-level slot. The mascot installs itself when its
window is built and clears the slot when the window goes; every producer calls a
named function here and learns nothing back. A producer that fires with no
mascot - the companion is off by default, and on a compositor with no layer
surface it never opens at all - is a **no-op**, which is the right answer and
the reason `emit` never raises.

**The kinds are `notice()`'s own vocabulary, named here rather than at the call
sites.** `mascot_art` draws one expression per kind and `tests/test_mascot.py`
pins that every kind maps to a real expression; a producer that spelled
`"build-ok"` inline would be a second place that string lives, and a rename
would leave one of them pointing at a face that no longer exists. `BY_NAME` is
the one table, and the step label is the one kind the mascot routes to a bubble
instead of a face - a tool running is not a reaction, it is *what is happening*.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: name -> the `notice()` kind it shows (or `"step"`, which `Mascot` routes to
#: `say()` rather than a face). Every value except `"step"` must be a key of
#: `mascot._EVENT_FACE`; `tests/test_mascot_events.py` asserts it, so this table
#: and the face map cannot drift apart in silence.
BY_NAME = {
    "question": "greet",        # a tool is asking the person something
    "approval": "meeting",      # a gated call is waiting on a decision
    "granted":  "build-ok",     # the person allowed it
    "refused":  "build-fail",   # the person refused it, or nobody answered
    "finished": "build-ok",     # the turn answered
    "error":    "build-fail",   # the turn failed
    "step":     "step",         # a tool is running - a bubble, not a face
}

#: The installed listener, or None. One at a time: there is one companion.
_listener = None


def install(listener) -> None:
    """Make `listener(kind, text)` the one place events go. Idempotent."""
    global _listener
    _listener = listener


def uninstall(listener=None) -> None:
    """Clear the listener. Passing one clears it only if it is still the one.

    The argument is what lets `Mascot.stop()` clear its own hook without
    clobbering a newer mascot that has already installed itself over it - the
    same shape as `ask_bridge.set_presenter`, and needed for the same reason: a
    window destroyed late must not unlisten its replacement.
    """
    global _listener
    if listener is None or listener is _listener:
        _listener = None


def installed() -> bool:
    """Whether a mascot is listening right now."""
    return _listener is not None


def emit(kind: str, text: str = "") -> None:
    """Hand `(kind, text)` to the mascot, if there is one. **Never raises.**

    A producer is a decision, a turn, a tool - none of which may fail because
    the overlay is off, half-drawn, or raising. A listener that throws is a
    bug in the listener, logged here and dropped; the call the producer was
    making proceeds either way.
    """
    fn = _listener
    if fn is None:
        return
    try:
        fn(kind, text)
    except Exception:  # noqa: BLE001 - a face is never worth a failed turn
        logger.debug("mascot event %r could not be delivered", kind, exc_info=True)


# --- the named producers ----------------------------------------------------
#
# Each is `emit(BY_NAME[name], ...)` so a call site says *what happened* and the
# name->kind choice stays in the table above. 

def question(text: str = "") -> None:
    """A tool is asking the person something (an ask_user, a plan, the phone).

    **No direct caller today (measured: 0 call sites).** `ask_bridge.ask` is
    the one chokepoint every question passes and it emits the face its own
    `kind` argument carries straight to `emit` - by name this wrapper would
    be a second route to the same face on the same line it happens. It is the
    named form of `BY_NAME["question"]`, kept so a producer that holds the
    whole decision can say what happened without naming a face; the table
    row is wired through the chokepoint even where this wrap is not.
    """
    emit(BY_NAME["question"], text)


def approval(text: str = "") -> None:
    """A gated call is waiting on a decision (`permissions.decide`).

    **No direct caller today (measured: 0 call sites).** The gate reaches the
    face through `ask(kind="meeting")`, which carries the approval kind to the
    same chokepoint the question does; this wrapper is the named form of
    `BY_NAME["approval"]` for a body that decides without asking through
    `ask_bridge`.
    """
    emit(BY_NAME["approval"], text)


def settled(granted: bool, text: str = "") -> None:
    """The answer to an approval: allowed, or refused / nobody answered."""
    emit(BY_NAME["granted"] if granted else BY_NAME["refused"], text)


def finished(text: str = "") -> None:
    """The turn answered."""
    emit(BY_NAME["finished"], text)


def error(text: str = "") -> None:
    """The turn failed."""
    emit(BY_NAME["error"], text)


def step(text: str = "") -> None:
    """A tool is running: its own words, as a bubble rather than a face."""
    emit(BY_NAME["step"], text)
