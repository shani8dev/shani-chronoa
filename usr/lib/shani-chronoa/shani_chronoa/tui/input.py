"""Keystrokes for the terminal client: line editing, continuation, history.

Nothing here decides what a typed line means. Slash commands and ``@path``
tokens are completed from :mod:`shani_chronoa.tui.commands` and sent on as
plain text; the engine owns every decision.
"""

from __future__ import annotations

import builtins
import os

try:
    import readline
except ImportError:  # pragma: no cover - no line editing on some platforms
    readline = None  # type: ignore[assignment]

from shani_chronoa.tui import commands

PROMPT = "chronoa> "
_CONTINUE = "\\"


def read_multiline(prompt: str = PROMPT, reader=None) -> str:
    """Read one logical line, continuing after a trailing backslash.

    The reader defaults to :func:`builtins.input` and receives ``prompt`` on
    each physical line. Continuation lines are joined with ``\\n`` with the
    trailing backslash removed. EOFError propagates, including mid-continuation.
    """
    if reader is None:
        reader = builtins.input
    parts: list[str] = []
    while True:
        line = reader(prompt)
        if line.endswith(_CONTINUE):
            parts.append(line[:-1])
        else:
            parts.append(line)
            return "\n".join(parts)


def completer(text: str, state: int):
    """GNU readline entry point: complete ``/commands`` and ``@paths``."""
    if readline is None:
        return None
    # CPython exposes get_endidx(); readline's C API calls it endpos.
    get_end = getattr(readline, "get_endpos", None) or readline.get_endidx
    buffer = readline.get_line_buffer()[: get_end()]
    token = buffer[max(buffer.rfind(" "), buffer.rfind("\t")) + 1 :]
    # Once a slash path has a space, complete the whole path so subcommands
    # such as `/settings get` are offered, not just the first word.
    if buffer.lstrip().startswith("/") and " " in buffer:
        token = buffer.strip()
    if token.startswith("/"):
        matches = commands.completions(token)
    elif "@" in token:
        matches = commands.path_completions(token)
    else:
        return None
    if 0 <= state < len(matches):
        return matches[state]
    return None


class Input:
    """Install readline editing and persist the session's input history."""

    def __init__(self, history_path=None) -> None:
        self.history_path = history_path

    def install(self) -> None:
        if readline is None:
            return
        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")
        # '/' and '@' must stay inside the token for the completer above.
        delims = readline.get_completer_delims()
        readline.set_completer_delims("".join(c for c in delims if c not in "/@"))
        # Alt+Enter appends the backslash that read_multiline treats as a join.
        readline.parse_and_bind(r'"\e\C-m": "\C-e\\"')
        if self.history_path and os.path.isfile(self.history_path):
            readline.read_history_file(self.history_path)

    def close(self) -> None:
        if readline is None or not self.history_path:
            return
        directory = os.path.dirname(os.path.abspath(self.history_path))
        os.makedirs(directory, exist_ok=True)
        readline.write_history_file(self.history_path)

    def read(self, prompt: str = PROMPT) -> str:
        return read_multiline(prompt)
