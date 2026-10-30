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

import contextvars
import logging
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

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

#: The turn engine's approval hook, bound to one turn's context. When a turn
#: is running, `ask()` routes its question here instead of to the GTK
#: presenter, so a shell-side person can answer a tool's question through
#: `dev.shani.chronoa.Agent.Approve` and the blocked tool loop resumes. Unbound
#: in the GTK app's own turns, where the presenter answers everything.
#:
#: **A context variable, not a global.** `set_agent_resolver` used to install
#: one callable process-wide, which routed a question asked by the *window's*
#: turn to the shell - and the shell would draw an approval card for a turn it
#: had never started, while the window waited for a click that never came. The
#: resolver belongs to the turn that installed it: `AgentService.Turn` binds it,
#: the coroutine and its tool threads inherit it (the assistant copies the
#: context into the executor), and no other turn can see it.
TurnResolver = Callable[[str, list, object, str],
                        "Optional[tuple[threading.Event, Callable[[str], None]]]"]

_turn_resolver: "contextvars.ContextVar[Optional[TurnResolver]]" = (
    contextvars.ContextVar("chronoa_turn_resolver", default=None))


@contextmanager
def turn_resolver(fn: TurnResolver) -> "Iterator[None]":
    """Bind `fn` as the approval resolver for the current turn's context."""
    token = _turn_resolver.set(fn)
    try:
        yield
    finally:
        _turn_resolver.reset(token)


def has_agent_resolver() -> bool:
    """Whether the current context belongs to an agent turn."""
    return _turn_resolver.get() is not None


def has_any_presenter() -> bool:
    """Whether a question can be put to someone, in the window or a shell.

    The gate that used to read `has_presenter()` alone refused every question
    in a session whose person was looking at the shell instead of the GTK
    window - the resolver was there, and the check never asked about it.
    """
    return has_presenter() or has_agent_resolver()


_presenter: Optional[Presenter] = None

#: (prompt, placeholder) -> entered text, or "" if the user dismissed it.
#: Installed by the application. Absent means nobody is listening.
TextPresenter = Callable[[str, str], "threading.Event"]
_text_presenter: Optional[TextPresenter] = None


def set_text_presenter(fn: Optional[TextPresenter]) -> None:
    """Install (or clear) the text-input presenter for free-form feedback."""
    global _text_presenter
    _text_presenter = fn


def has_text_presenter() -> bool:
    return _text_presenter is not None


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


#: What a question is about, shown beside it while it is asked: a file write's
#: `(path, before, after)` or a command's `{"command": ...}`. Keyed by the
#: question text, so the presenter chain - three layers that each take
#: `(question, options)` - did not have to change shape to carry it.
_previews: "dict[str, object]" = {}


def preview_for(question: str):
    """The preview `ask()` was given for `question`, or None."""
    return _previews.get(question)


def ask(question: str, options: list, timeout: float = DEFAULT_TIMEOUT_SECONDS,
        preview=None, kind: str = "greet") -> str:
    """Put `question` to the user and return the chosen option.

    `preview` is what the answer is about, drawn under the question - so "may
    it edit greet.py?" is asked with the edit on screen, not after it.

    `kind` is the face the companion wears while it waits, a `notice()` kind -
    the default is a tool's own question, and `permissions.decide` passes the
    approval face because a gate is not a question the model chose to ask. It
    rides here, at the one chokepoint every question passes, rather than at each
    caller: `ask_user`, a plan, the phone and the browser all come through here,
    and a producer added later gets the face by default instead of by memory.

    Returns "" when there is nobody to answer, when the user dismisses the
    prompt, or when the wait times out - all three are "no answer was given",
    and the caller must not treat any of them as a choice.
    """
    resolver = _turn_resolver.get()
    if resolver is not None:
        # A shell-side turn: route the question to the agent's approval
        # channel instead of to the GTK presenter. The resolver returns the
        # event the tool loop waits on and has already emitted the `approval`
        # event; `Approve` sets it from whatever thread it was called on.
        routed = resolver(question, list(options), preview, kind)
        if routed is not None:
            done, _resolve = routed
            return _answer_of(done, timeout)
        # A turn that declines to route falls through to the presenter below.
    if _presenter is None:
        return ""
    with _pending_lock:
        if len(_pending) >= MAX_PENDING:
            logger.warning(
                "Refusing a question: %d are already on screen, which is the "
                "most a person can read at once", len(_pending))
            return ""
        _pending.append(question)
        if preview is not None:
            _previews[question] = preview
    # **The one place a face goes for a question.** Emitted before the presenter
    # is called, so the expression is up as the widget appears rather than after
    # the answer - the face is for the *waiting*, and there is none left by the
    # time `ask` returns. No mascot listening is a no-op (`mascot_events`).
    from shani_chronoa import mascot_events
    mascot_events.emit(kind, question)
    try:
        done = _presenter(question, list(options))
    except Exception as exc:  # noqa: BLE001 - a broken presenter is not a choice
        logger.error("ask_user presenter failed: %s", exc)
        return ""
    finally:
        with _pending_lock:
            if question in _pending:
                _pending.remove(question)
    try:
        return _answer_of(done, timeout)
    finally:
        # **Kept until the answer, not until the presenter returns.** The
        # presenter only *queues* the widget for the GTK thread and returns at
        # once, so dropping the preview there removed it before the window
        # drew the question - measured: the approval appeared with no diff.
        with _pending_lock:
            _previews.pop(question, None)


def _answer_of(done, timeout: float) -> str:
    """The chosen option once `done` is set, or "" for anything else.

    A routed question gets one extra step: the event carries a
    `chronoa_closed` callback, called however the wait ends, so the shell is
    told to take the card down when the tool stopped waiting - and a late
    `Approve` no longer looks like it answered anything.
    """
    if not isinstance(done, threading.Event):
        # A presenter that returns the wrong type used to raise AttributeError
        # out of the tool call, taking the turn with it. Anything that is not a
        # completed wait is "no answer" - the one conversion this module exists
        # to guarantee.
        logger.error("ask_user presenter returned %s, not an event; treating it "
                     "as no answer", type(done).__name__)
        return ""
    try:
        if not done.wait(timeout):
            logger.warning("ask_user timed out after %.0fs with no answer", timeout)
            return ""
        return getattr(done, "chronoa_answer", "") or ""
    finally:
        closed = getattr(done, "chronoa_closed", None)
        if callable(closed):
            try:
                closed()
            except Exception:  # noqa: BLE001 - cleanup must not change the answer
                logger.debug("ask_user close hook failed", exc_info=True)


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


def ask_for_text(prompt: str, placeholder: str = "",
                 timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Put `prompt` to the user and return the entered text.

    Returns "" when there is nobody to answer, when the user dismisses the
    prompt, or when the wait times out - all three mean no answer was given.
    """
    if _text_presenter is None:
        return ""
    try:
        done = _text_presenter(prompt, placeholder)
    except Exception as exc:  # noqa: BLE001 - a broken presenter is not a choice
        logger.error("ask_for_text presenter failed: %s", exc)
        return ""
    if not isinstance(done, threading.Event):
        logger.error("ask_for_text presenter returned %s, not an event; "
                     "treating it as no answer", type(done).__name__)
        return ""
    if not done.wait(timeout):
        logger.warning("ask_for_text timed out after %.0fs with no answer", timeout)
        return ""
    return getattr(done, "chronoa_answer", "") or ""
