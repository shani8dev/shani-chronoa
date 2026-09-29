"""Bridge that lets a tool ask the user something and wait for the answer.

A skill normally runs in a sandbox subprocess and cannot touch the interface.
Asking a question is the one thing that cannot be done that way: the question
has to appear in the window the user is looking at, and the answer has to come
back before the turn can continue. So `ask_user` is a *local* tool - it runs in
this process, and this module is how it reaches the interface.

The alternative - a skill shelling out, or writing to a file the GUI polls - was
rejected because both turn a conversational turn into a timeout race. Here the
caller blocks on an event that only the user's click can set.

Threading, which is the whole difficulty:

- The assistant's tool loop runs on `AsyncBridge`'s background event loop, so
  `ask()` is called *off* the GTK thread. Constructing or touching a widget
  there is undefined behaviour at best.
- The widget is therefore built by a `GLib.idle_add` callback, which GLib runs
  on the main loop regardless of which thread queued it.
- The calling thread then waits on a `threading.Event`. The GTK callback sets
  it. That is the only cross-thread hand-off, and it is one-way.

Failure direction is the point of this module. **No one present is not an
answer.** If no bridge is installed - a headless MCP session, a trigger rule
firing with nobody at the keyboard - `ask()` returns a refusal the model can
read and act on. It does not pick an option, does not return empty, and does
not block forever.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: (question, options) -> chosen option, or "" if the user dismissed it.
#: Installed by the application. Absent means nobody is listening.
Presenter = Callable[[str, list[str]], "threading.Event"]

#: How long to wait before giving up. A question with no answer must not hang
#: the turn forever: an unanswered prompt would leave the tool loop blocked with
#: no way for the model to recover.
DEFAULT_TIMEOUT_SECONDS = 180.0

#: Questions on screen at once. A second is enough for one real conversation; a
#: pile is not a conversation, and the user cannot answer what they cannot read.
#:
#: Past this, a question is refused outright rather than queued. Queueing is what
#: makes the pile grow, and a queued prompt is one the user will never get to -
#: it sits behind a window they have already stopped looking at. `assistd` bounds
#: the same thing for its confirmation prompts with `MAX_PENDING_CONFIRMS = 32`.
#:
#: The refusal is a refusal and not a timeout, and `ask` says so: "" means "no
#: answer was given", which is true, and the caller logs which of the three it was.
MAX_PENDING = 4

#: Prompts currently on screen. Guarded, because the callers are on whichever
#: threads the tool loop and the MCP server happen to use.
_pending: "list[str]" = []
_pending_lock = threading.Lock()

_presenter: Optional[Presenter] = None


def pending_count() -> int:
    """How many questions are on screen right now."""
    with _pending_lock:
        return len(_pending)


def set_presenter(fn: Optional[Presenter]) -> None:
    """Install (or clear) the presenter the `ask_user` tool calls."""
    global _presenter
    _presenter = fn


def has_presenter() -> bool:
    return _presenter is not None


def ask(question: str, options: list, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Put `question` to the user and return the chosen option.

    Returns "" when there is nobody to answer, when the user dismisses the
    prompt, or when the wait times out - all three are "no answer was given",
    and the caller must not treat any of them as a choice.
    """
    if _presenter is None:
        return ""
    with _pending_lock:
        if len(_pending) >= MAX_PENDING:
            logger.warning(
                "Refusing a question: %d are already on screen, which is the "
                "most a person can read at once", len(_pending))
            return ""
        _pending.append(question)
    try:
        done = _presenter(question, list(options))
    except Exception as exc:  # noqa: BLE001 - a broken presenter is not a choice
        logger.error("ask_user presenter failed: %s", exc)
        return ""
    finally:
        with _pending_lock:
            if question in _pending:
                _pending.remove(question)
    if not isinstance(done, threading.Event):
        # A presenter that returns the wrong type used to raise AttributeError
        # out of the tool call, taking the turn with it. Anything that is not a
        # completed wait is "no answer" - the one conversion this module exists
        # to guarantee.
        logger.error("ask_user presenter returned %s, not an event; treating it "
                     "as no answer", type(done).__name__)
        return ""
    if not done.wait(timeout):
        logger.warning("ask_user timed out after %.0fs with no answer", timeout)
        return ""
    return getattr(done, "chronoa_answer", "") or ""


def make_event() -> "tuple[threading.Event, Callable[[str], None]]":
    """A fresh event plus the callback the GTK side calls with the answer.

    Returning both together is deliberate: the answer has nowhere to live except
    the event object, so a caller that made its own event would have to invent
    somewhere to stash it, and every implementation would do that differently.
    """
    done = threading.Event()
    done.chronoa_answer = ""  # type: ignore[attr-defined]

    def resolve(answer: str) -> None:
        done.chronoa_answer = answer  # type: ignore[attr-defined]
        done.set()

    return done, resolve
