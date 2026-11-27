"""Full-screen terminal view for the Chronoa client.

Standard library only: ANSI escapes for the alternate screen, termios for raw
keys, and os.read for input. The engine still decides everything; this module
only lays out what the Agent already said and sends the person's line back.

No stateful UI framework and no second copy of a command table: the editor
asks `tui.commands` for completions, and the session in `tui.app` handles the
line exactly as it does in the simple REPL.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
from typing import List, Optional, TextIO

PROMPT = "chronoa> "
_HINT = "Enter send · Tab complete · ↑↓ history · PgUp/PgDn scroll · Ctrl+C cancel · Ctrl+D quit"


class Screen:
    """A tiny alternate-screen writer with a raw-mode input file descriptor."""

    def __init__(
        self, stream: Optional[TextIO] = None, fd: Optional[int] = None
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._fd = fd if fd is not None else sys.stdin.fileno()
        self._saved = None
        self._in_alternate = False

    @property
    def isatty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())

    def enter(self) -> None:
        if not self.isatty:
            return
        self._saved = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)
        self._stream.write("\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H")
        self._stream.flush()
        self._in_alternate = True

    def exit(self) -> None:
        if not self._in_alternate:
            return
        if self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        self._stream.write("\x1b[?25h\x1b[?1049l")
        self._stream.flush()
        self._in_alternate = False

    def size(self) -> tuple:
        try:
            size = os.get_terminal_size(self._fd)
            return max(20, size.columns), max(6, size.lines)
        except OSError:
            return 80, 24

    def draw(
        self,
        header: List[str],
        body: List[str],
        input_line: str,
        cursor: int,
        scroll: int,
    ) -> None:
        """One atomic repaint; `scroll` counts lines held above the viewport."""
        if not self._in_alternate:
            return
        width, height = self.size()
        available = max(1, height - len(header) - 3)
        shown = (
            body[-available:]
            if not scroll
            else body[
                max(0, len(body) - available - scroll) : len(body) - scroll or None
            ]
        )
        out = ["\x1b[H\x1b[J"]
        for line in header:
            out.append(line[:width] + "\n")
        out.append("─" * width + "\n")
        for line in shown:
            out.append(line[:width].rstrip() + "\n")
        out.append("─" * width + "\n")
        out.append(PROMPT + input_line[: width - len(PROMPT)])
        out.append("\x1b[?25h")
        out.append(
            f"\x1b[{height};{len(PROMPT) + min(cursor, width - len(PROMPT)) + 1}H"
        )
        self._stream.write("".join(out))
        self._stream.flush()

    def commit(self, canvas, *, cursor=None, visible=True, force_rows=()):
        """Differential paint from `tui.canvas`; the legacy `draw` stays intact."""
        if not self._in_alternate:
            return canvas
        painted = canvas.commit(
            self._stream,
            getattr(self, "_previous", None),
            cursor=cursor,
            visible=visible,
            force_rows=force_rows,
        )
        self._previous = painted
        return painted

    def on_resize(self) -> None:
        """Forget the painted frame so the next commit redraws every row."""
        self._previous = None

    def read_key(self, timeout=None) -> str:
        """One key, or `"idle"` when nothing arrived within `timeout`.

        The multi-pane view polls with a short timeout so background panel
        results can be drawn without waiting for a keystroke. The legacy
        caller keeps the blocking default.
        """
        if timeout is not None and not select.select([self._fd], [], [], timeout)[0]:
            return "idle"
        first = os.read(self._fd, 1)
        if not first:
            return "eof"
        if first in (b"\r", b"\n"):
            return "enter"
        if first == b"\x03":
            return "interrupt"
        if first == b"\x04":
            return "eof"
        if first == b"\x7f":
            return "backspace"
        if first == b"\t":
            return "tab"
        if first == b"\x1b":
            if not select.select([self._fd], [], [], 0.05)[0]:
                return "escape"
            second = os.read(self._fd, 1)
            if second != b"[":
                return "escape"
            third = os.read(self._fd, 1)
            if third == b"Z":
                return "shift_tab"
            if third in (b"5", b"6") and select.select([self._fd], [], [], 0.01)[0]:
                os.read(self._fd, 1)  # the trailing '~' of PgUp/PgDn
            return {
                b"A": "up",
                b"B": "down",
                b"C": "right",
                b"D": "left",
                b"H": "home",
                b"F": "end",
                b"5": "pageup",
                b"6": "pagedown",
            }.get(third, "escape")
        if first == b"\x10":  # Ctrl+P: the palette
            return "control:p"
        if first == b"\x0f":  # Ctrl+O: the detail pane
            return "control:o"
        if first == b"\x12":  # Ctrl+R: refresh panels
            return "control:r"
        if first[0] < 32:
            return "control"
        # The leading byte announces a UTF-8 character's width; read the rest
        # without blocking so a Latin key never waits for more input.
        width = (
            1
            if first[0] < 0x80
            else 2
            if first[0] >> 5 == 6
            else 3
            if first[0] >> 4 == 14
            else 4
            if first[0] >> 3 == 30
            else 1
        )
        raw = bytearray(first)
        for _ in range(width - 1):
            if select.select([self._fd], [], [], 0.01)[0]:
                raw += os.read(self._fd, 1)
        return "text:" + raw.decode("utf-8", "replace")


class LineEditor:
    """The input line: insert, move, history and one-token completion."""

    def __init__(self, history: Optional[List[str]] = None) -> None:
        self.value = ""
        self.cursor = 0
        self.history: List[str] = list(history or [])
        self._index = len(self.history)  # first Up recalls the newest line

    def insert(self, chars: str) -> None:
        self.value = self.value[: self.cursor] + chars + self.value[self.cursor :]
        self.cursor += len(chars)

    def backspace(self) -> None:
        if self.cursor:
            self.cursor -= 1
            self.value = self.value[: self.cursor] + self.value[self.cursor + 1 :]

    def move(self, action: str) -> None:
        if action == "left":
            self.cursor = max(0, self.cursor - 1)
        elif action == "right":
            self.cursor = min(len(self.value), self.cursor + 1)
        elif action == "home":
            self.cursor = 0
        elif action == "end":
            self.cursor = len(self.value)

    def recall(self, action: str) -> None:
        if not self.history:
            return
        self._index = max(
            0, min(len(self.history), self._index + (-1 if action == "up" else 1))
        )
        self.value = (
            self.history[self._index] if self._index < len(self.history) else ""
        )
        self.cursor = len(self.value)

    def take(self) -> str:
        line = self.value
        if line.strip():
            self.history.append(line)
        self._index = len(self.history)
        self.value, self.cursor = "", 0
        return line

    def complete(self) -> List[str]:
        """Replace the current token with the common completion; return matches."""
        from shani_chronoa.tui import commands

        token_start = max(self.value.rfind(" "), self.value.rfind("\t")) + 1
        token = self.value[token_start:]
        if token.startswith("/"):
            matches = commands.completions(token)
        elif "@" in token:
            matches = commands.path_completions(token)
            token_start += token.rfind("@") + 1
        else:
            return []
        if not matches:
            return []
        common = os.path.commonprefix(matches)
        if len(matches) == 1 and not common.endswith(" "):
            common += " "
        self.value = self.value[:token_start] + common
        self.cursor = len(self.value)
        self.matches = matches
        self._match_index = 0
        return matches

    def set(self, value: str) -> None:
        """Prefill the input line from a sidebar row or the palette."""
        self.value = str(value)
        self.cursor = len(self.value)
        self.matches = []
        self._match_index = 0

    def complete_next(self) -> "list[str]":
        """Cycle a completion popup instead of committing its common prefix."""
        if not getattr(self, "matches", None):
            return self.complete()
        token_start = max(self.value.rfind(" "), self.value.rfind("\t")) + 1
        self._match_index = (self._match_index + 1) % len(self.matches)
        self.value = self.value[:token_start] + self.matches[self._match_index] + " "
        self.cursor = len(self.value)
        return self.matches


class Transcript:
    """A TextIO the existing renderer and commands can write into."""

    def __init__(self, app) -> None:
        self.app = app
        self.lines: List[str] = []
        self.marks: List[tuple] = []
        self._partial = ""

    def write(self, text: str) -> int:
        text = str(text)
        # The approval chooser prints its prompt in place with a leading
        # carriage return and no newline. In a full-screen view that line
        # must still be visible, and it is the readiness marker a script
        # (or the person) can wait for before answering.
        if "choice [" in text:
            self.lines.append(text.strip())
            self._partial = ""
            self.app.dirty = True
            if getattr(self.app, "screen", None) is not None and self.app.screen.isatty:
                self.app.draw()  # the chooser is waiting: show its prompt now
            return len(text)
        if text.strip("\r ") == "":
            return len(text)
        self._partial += text
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            self.lines.append(line.rstrip("\r"))
        if self.lines:
            self.app.dirty = True
        return len(text)

    def flush(self) -> None:
        if self._partial:
            self.lines.append(self._partial.rstrip("\r"))
            self._partial = ""
            self.app.dirty = True

    def append_mark(self, kind: str, start: int, end: int) -> None:
        """Record which transcript rows belong to a tool card or approval."""
        self.marks.append((kind, start, end))

    def isatty(self) -> bool:
        return False
