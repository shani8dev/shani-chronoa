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


def blocked_reason(tool: str) -> str:
    """Why `tool` cannot run right now, or "" if it can.

    Read-only tools are unaffected, so planning can still inspect the machine -
    which is the entire reason to be in this mode.
    """
    if not _enabled:
        return ""
    from shani_chronoa.capabilities import gated_by

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

    if not gated_by(tool, description):
        return ""

    why = reason()
    because = f" You entered plan mode {why}." if why else " You are in plan mode."
    return (
        f"Plan mode is on, so {tool} was not run.{because} Plan mode lets "
        f"Chronoa read the machine and work out what it would do, without "
        f"changing anything - say so and describe the steps instead, or ask to "
        f"leave plan mode if the user wants them carried out."
    )
