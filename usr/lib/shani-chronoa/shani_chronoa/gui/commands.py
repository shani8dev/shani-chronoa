"""Slash commands: a `/name` the composer runs without asking the model.

kimi-cli registers a `/compact` and friends through a decorator
(`soul/slash.ts:34,37`) and cline offers a `SlashCommandMenu` in the composer.
Chronoa's composer takes prose only, so *every* command is a skill the model has
to guess at — which on a 0.6–1.7B local model is a coin flip, and turns
"clear this conversation" into a conversation about clearing it.

**Two rules, both load-bearing.**

1. **A command only exists if something here runs it.** No placeholder
   `/compact` that pretends to condense a history, no `/export` that exports
   nothing. Every entry below is wired to a real action, and the tests walk the
   registry and refuse a command whose action is missing.
2. **A command is not a shortcut around the permission layers.** `/undo` runs
   the same skill the voice path runs, through the same whitelist and the same
   consent keys. The only thing a command changes is that the model is not asked
   to translate it first.

Deliberately absent: anything that would need a model round of its own, and
anything that only makes sense for a keyboard in a repo. This is a voice
assistant first; the command bar exists for the keyboard path, not to replace
speaking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Command:
    """One `/name`: what it says it does, and what actually happens."""

    name: str
    summary: str
    #: `(window, argument) -> str or None`. A returned string is shown to the
    #: person as the outcome; None means "the command did its work silently and
    #: the turn continues as normal".
    run: Callable[[object, str], Optional[str]]
    #: True when the composer should clear the field afterwards. False for
    #: commands that consume the text themselves.
    consumes: bool = True


#: Filled in by `install()`. Module-level because the composer's menu and the
#: test that walks it must see the same table.
_COMMANDS: "dict[str, Command]" = {}


def register(command: Command) -> Command:
    _COMMANDS[command.name] = command
    return command


def commands() -> "dict[str, Command]":
    return dict(_COMMANDS)


def names() -> "list[str]":
    return sorted(_COMMANDS)


def lookup(text: str) -> "tuple[Optional[Command], str]":
    """`(command, argument)` for a line of composer text, or `(None, text)`.

    Only a slash **at the very start** counts. "/etc/passwd is on disk" and a
    sentence that happens to begin with a fraction both fall through to being
    ordinary text, because a command bar that eats prose is worse than none.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("/") or " " not in stripped and not stripped[1:].isalnum():
        return None, stripped
    name, _, argument = stripped.partition(" ")
    command = _COMMANDS.get(name[1:].lower())
    if command is None:
        return None, stripped
    return command, argument.strip()


# --- the commands themselves -------------------------------------------------


def _new(window, argument: str) -> Optional[str]:
    """Go through the *application's* own reset action, so a `/new` and
    Ctrl+N cannot diverge: same store call, same status line, same "the
    previous one is in Conversations"."""
    app = getattr(window, "_app", None)
    if app is None:
        return "This window cannot start a new conversation."
    app.activate_action("reset-conversation", None)
    return None


def _diff(window, argument: str) -> Optional[str]:
    window._show_surface("diff")
    return None


def _undo(window, argument: str) -> Optional[str]:
    """Runs the real skill, so the same consent key and sandbox apply."""
    from shani_chronoa.tools import execute_tool_outcome
    return execute_tool_outcome("undo_last_change", {}).text


def _diagnostics(window, argument: str) -> Optional[str]:
    window._show_surface("diagnostics")
    return None


def _help(window, argument: str) -> Optional[str]:
    """Open the capability list.

    `argument` is the text after the command, so `/help what can you do?` fills
    the composer with the capability prompt instead of discarding it. Typed on
    its own it still opens the window, which is the answer to "what can you do"
    and is not a turn the model has to answer. The prompt string itself lives in
    `capabilities.help_prompt()` rather than here, so the command, the chip and
    the empty-transcript suggestions cannot name three different things.
    """
    text = (argument or "").strip()
    if text:
        from shani_chronoa import capabilities as caps
        window.set_input_text(caps.help_prompt() if text.endswith("?") else text)
        return None
    window.open_help()
    return None


def _tasks(window, argument: str) -> Optional[str]:
    """Toggle the task card's own visibility, so a person can see what is
    outstanding without it taking space every other turn."""
    card = getattr(window, "_task_card", None)
    if card is None:
        return "There is no task list on this window."
    card.refresh()
    return None if card.get_visible() else "Nothing outstanding right now."


def install() -> None:
    """Register the built-ins. Idempotent."""
    register(Command("new", "Start a fresh conversation", _new))
    register(Command("diff", "Show what Chronoa changed, file by file", _diff))
    register(Command("undo", "Put the last file change back", _undo))
    register(Command("tasks", "Show or hide the task list", _tasks))
    register(Command("diagnostics", "Open the diagnostics panel", _diagnostics))
    register(Command("help", "What can Chronoa do?", _help))


install()
