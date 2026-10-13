"""Plan mode: the assistant may look, but not touch.

"Disk is nearly full - what would you do about it?" is a question that needs
reading the filesystem, listing directories, checking services. It does not need
to delete anything, and a user asking it would rather not have to grant delete
permission to be answered.

There is no way to express that as a permission. Chronoa's consent keys are
global and persistent: the alternative to switching one on is a session with no
way to change the machine at all, which is also not what was asked for.

So this is a mode, and it is enforced in `execute_tool` rather than asked for in
the system prompt. A prompt instruction is a promise the model may or may not
keep; a check in the dispatch path is a fact. gemini-cli takes the same shape -
`isPlanMode()` tested inside the edit tool's execution, with separate
enter/exit tools - and the reason it is worth copying is precisely that it is not
a prompt.

## Which tools it blocks, and why that is deliberately too many

Exactly the tools that have a consent key, because that is already the
maintained answer to "does this change something". Deriving a second list here
would be a second source of truth that drifts from the first.

It is therefore broader than it strictly needs to be: `screenshot` and `notify`
carry consent keys and so are refused in plan mode, even though neither changes
the machine. That is the safe direction to be wrong in. The cost is that a plan
cannot include "and I'd take a screenshot to show you" - which is the correct
answer to give anyway, since taking one is not part of planning.

## It is visible or it is dangerous

A mode that silently removes capabilities would be worse than not having one:
the assistant would begin refusing actions the user had explicitly permitted, and
the refusal would look like a bug. So the state is readable from anywhere, is set
through this module rather than by poking a global, and the window shows it.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_enabled = False
#: Why it was turned on, for the refusal message. "what would you do" is a
#: different situation from a misfire, and the model should not have to guess
#: which one it is in.
_reason: Optional[str] = None


def is_enabled() -> bool:
    """Whether the assistant is currently forbidden from changing anything."""
    return _enabled


def reason() -> str:
    """Why plan mode is on, or "" if it is not."""
    return _reason or ""


def set_enabled(on: bool, why: str = "") -> bool:
    """Turn plan mode on or off. Returns the new state.

    `why` is shown to the user and quoted back in refusals, so the refusal
    explains itself instead of appearing as an unexplained denial.
    """
    global _enabled, _reason
    with _lock:
        _enabled = bool(on)
        _reason = (why or "").strip() if on else None
    logger.info("Plan mode %s%s", "on" if _enabled else "off",
                f" ({_reason})" if _reason else "")
    return _enabled


#: What this turn was *asked* to do and was refused. **The plan, as data.**
#:
#: Plan mode's refusal tells the model to describe the steps in prose, and that
#: prose is the only record: nothing could be ticked off, resumed, or corrected,
#: and approving it meant asking again in a new turn and hoping the model
#: re-derived the same steps from its own text.
#:
#: This is the same information in the shape `goals.PlannedStep` wants, so the
#: turn can store a real run instead. Reset per turn by `begin_turn()`.
_refusals: "list[tuple[str, dict]]" = []

#: Set when the user answers "Keep planning" in this turn, and cleared by
#: `begin_turn()`. **One answer stands for the turn**, because asking again for
#: every remaining step would turn "what would you do about this?" into ten
#: dialogs - which is the opposite of what they asked for. The silence is the
#: answer, and the steps still land in the stored plan.
_keep_planning = False


def begin_turn() -> None:
    """Start collecting this turn's plan. Called at the top of every turn.

    **Reset here rather than read at the end**, because the refusal list is
    per-turn state on a module that outlives turns - a plan would otherwise
    accumulate every step the session ever refused.
    """
    global _refusals, _keep_planning
    with _lock:
        _refusals = []
        _keep_planning = False


def record_refusal(tool: str, arguments: "Optional[dict]" = None) -> None:
    """Note that `tool` was refused, so the turn can store it as a step.

    **Recorded even when the tool was refused for another reason** - consent, an
    unknown tool, a limit. Any refusal is a thing the user asked for and did not
    get, and a plan that quietly drops the steps it could not justify is a
    plan that lies about its own coverage.

    The arguments are copied. They are the caller's dict and the turn carries on
    after this, so holding the reference would let a later mutation rewrite the
    step that was recorded.
    """
    with _lock:
        _refusals.append((str(tool),
                          dict(arguments) if isinstance(arguments, dict) else {}))


def refusals() -> "list[tuple[str, dict]]":
    """`(tool, arguments)` for everything this turn was refused, in order."""
    with _lock:
        return [(tool, dict(args)) for tool, args in _refusals]


#: The options offered when plan mode meets a call it would otherwise refuse.
#: **"Do it now" first**, because the common answer to "what would you do?"
#: followed by silence is yes, and a dialog that leads with "no" teaches people
#: that plan mode is a wall rather than a question.
ASK_OPTIONS = ("Do it now", "Keep planning")


def ask_to_proceed(tool: str, arguments: "Optional[dict]" = None) -> bool:
    """Put the refused call to the user, and report whether they allowed it.

    **Plan mode asks; it does not only describe.** The refusal used to tell the
    model to write out the steps in prose and hand the decision back to it -
    which is the one party with an interest in being wrong about what you want.
    Now the person is asked, with the actual tool and the actual arguments, and
    a yes runs the call immediately through the ordinary dispatch path.

    **Everything else is still refused.** No presenter, a dismissed prompt, a
    timeout, or an answer that is not one of the two words - all of them mean
    "no answer was given", and this app's own rule is that an unanswered
    question is never consent. Only a literal "Do it now" proceeds.

    **"Keep planning" records the step** so it ends up in the stored plan; a
    refusal with no record would be a step the plan does not mention, which is
    the failure this whole change exists to remove.
    """
    detail = ""
    if isinstance(arguments, dict):
        keys = ", ".join(sorted(str(k) for k in arguments)[:6])
        if keys:
            detail = f" It wanted: {keys}."

    from shani_chronoa import ask_bridge

    global _keep_planning
    if _keep_planning:
        # Already answered for this turn. Recorded, not run, and not asked.
        record_refusal(tool, arguments)
        return False

    if not ask_bridge.has_presenter():
        # Nobody to ask is not permission. Same answer `ask_user` gives.
        record_refusal(tool, arguments)
        return False

    answer = ask_bridge.ask(
        f"Plan mode is on, so {tool} was not run. Run it now?{detail}",
        list(ASK_OPTIONS))
    if answer == ASK_OPTIONS[0]:
        return True
    # **"Keep planning" is a standing answer for the rest of this turn.** It is
    # about whether this turn should act at all, and the model may well try
    # nine more tools before it describes anything. Asking each time would make
    # plan mode noisier than not using it.
    with _lock:
        _keep_planning = True
    record_refusal(tool, arguments)
    return False


def blocked_reason(tool: str) -> str:
    """Why `tool` cannot run right now, or "" if it can.

    Read-only tools are unaffected, so planning can still inspect the machine -
    which is the entire reason to be in this mode.
    """
    if not _enabled:
        return ""
    from shani_chronoa.capabilities import gated_by  # noqa: F401 - read below

    description = ""
    try:
        from shani_chronoa import tools

        for schema in tools.TOOLS:
            function = schema.get("function", {})
            if function.get("name") == tool:
                description = function.get("description", "") or ""
                break
    except Exception as exc:  # noqa: BLE001 - a lookup failure must not grant
        logger.debug("Could not read the schema for %s while in plan mode: %s",
                     tool, exc)

    # **Two maintained lists, not one.** The premise here is "a consent key is
    # the maintained answer to does this change something" - and it is wrong
    # for exactly the tools that matter most. Measured: plan mode blocked 92
    # tools and allowed `write_text_file` and `apply_patch`, so entering plan
    # mode did not stop the model writing your files. Both are in
    # `MUTATING_TOOLS`, which this function was never reading.
    from shani_chronoa.capabilities import MUTATING_TOOLS, TALKS_ONLY

    if tool in TALKS_ONLY:
        # **Asking a question is not touching the machine.** Without this, plan
        # mode became unusable the moment it started consulting
        # `MUTATING_TOOLS` - which contains `ask_user`, because a question has
        # an effect: it appears. Measured: adding `MUTATING_TOOLS` to this
        # function blocked 133 tools instead of 92, and the first thing it took
        # away was the ability to ask the user anything.
        return ""
    if tool in MUTATING_TOOLS:
        gated = tool          # any reason is enough; the refusal text is shared
    else:
        gated = gated_by(tool, description)
    if not gated:
        return ""

    why = reason()
    because = f" You entered plan mode {why}." if why else " You are in plan mode."
    return (
        f"Plan mode is on, so {tool} was not run.{because} Plan mode lets "
        f"Chronoa read the machine and work out what it would do, without "
        f"changing anything - say so and describe the steps instead, or ask to "
        f"leave plan mode if the user wants them carried out."
    )
