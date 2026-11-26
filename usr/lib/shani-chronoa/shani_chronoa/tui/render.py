"""Plain-text rendering of the Agent's event stream.

Standard library only: a stream, a width and two switches are enough. Replies
stream as buffered lines; a status line lives in place and never reaches
scrollback. The engine decides what to say, this module only writes it down.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional, TextIO

_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_FENCE = "-- code --"


class Renderer:
    """Turn engine events into terminal scrollback."""

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        *,
        width: Optional[int] = None,
        color: Optional[bool] = None,
        interactive: Optional[bool] = None,
    ) -> None:
        self._stream = sys.stdout if stream is None else stream
        isatty = getattr(self._stream, "isatty", lambda: False)()
        self._width = (
            shutil.get_terminal_size((80, 24)).columns
            if width is None
            else max(1, int(width))
        )
        self._color = (
            isatty and "NO_COLOR" not in os.environ if color is None else bool(color)
        )
        self._interactive = isatty if interactive is None else bool(interactive)
        self._status: Optional[str] = None
        self._word_tail = ""
        self._line = ""
        self._line_style: Optional[str] = None
        self._indent = ""
        self._at_start = True
        self._fence = False
        self._last_blank = True

    @property
    def color(self) -> bool:
        return self._color

    @property
    def interactive(self) -> bool:
        return self._interactive

    # -- low-level output -----------------------------------------------

    def _clear_status(self) -> None:
        if self._status is None:
            return
        self._stream.write("\r" + " " * len(self._status) + "\r")
        self._status = None
        self._stream.flush()

    def _emit_line(self, content: str, style: Optional[str] = None) -> None:
        """Write one completed scrollback line, clearing any in-place status."""
        self._clear_status()
        text = style + content + _RESET if self._color and style else content
        self._stream.write(text + "\n")
        self._stream.flush()
        self._last_blank = not content

    def _start_line(self) -> None:
        self._line = ""
        self._line_style = None
        self._indent = ""
        self._at_start = True
        self._fence = False

    def _flush_line(self) -> None:
        if self._line or self._fence:
            self._emit_line(self._line, self._line_style)
        self._start_line()

    def _flush_reply(self) -> None:
        """Release a held word and the buffered line in stream order."""
        if self._word_tail:
            self._push_words([self._word_tail])
            self._word_tail = ""
        if self._line or self._fence:
            self._flush_line()

    def _before_scrollback(self) -> None:
        """Keep buffered reply text ahead of any other output."""
        self._flush_reply()

    # -- reply buffering and wrapping -----------------------------------

    def _begin(self, word: str) -> None:
        """Apply the ASCII markdown marks that belong to a logical line start."""
        self._indent = ""
        self._line_style = None
        if word.startswith("```"):
            self._line = _FENCE
            self._line_style = _DIM
            self._fence = True
        elif word.startswith("#"):
            if not self._last_blank:
                self._emit_line("")
            self._line_style = _BOLD
        elif word in ("-", "*") or (word.endswith(".") and word[:-1].isdigit()):
            self._indent = " " * (len(word) + 1)

    def _push_words(self, words: list) -> None:
        for word in words:
            if self._at_start:
                self._begin(word)
                self._at_start = False
            if self._fence:
                continue
            candidate = word if not self._line else self._line + " " + word
            if self._line and len(candidate) > self._width:
                self._emit_line(self._line, self._line_style)
                self._line = self._indent + word
            else:
                self._line = candidate

    def render_text(self, text: str) -> None:
        """Buffer a reply chunk, wrap complete lines, keep explicit newlines."""
        combined = self._word_tail + str(text)
        self._word_tail = ""
        parts = combined.split("\n")
        for index, part in enumerate(parts):
            words = part.split()
            if index < len(parts) - 1:
                self._push_words(words)
                self._flush_line()
                continue
            # A chunk may end mid-word; hold the last word until the next one.
            if words and not part[-1].isspace():
                self._word_tail = words[-1]
                words = words[:-1]
            self._push_words(words)

    # -- engine events --------------------------------------------------

    def render_tool(self, event: dict) -> None:
        text = str(event.get("text") or "")
        if not text:
            return
        self._before_scrollback()
        self._emit_line("  > " + text, _DIM)

    def render_tool_done(self, event: dict) -> None:
        text = str(event.get("text") or "")
        line = "  = ok" if event.get("ok") else f"  = failed: {text}"
        self._before_scrollback()
        self._emit_line(line, _DIM)

    def render_state(self, event: dict) -> None:
        if not self._interactive:
            return
        state = str(event.get("state") or "")
        if not state:
            return
        self._clear_status()
        text = "  " + state + "..."
        style = _DIM if self._color else ""
        self._stream.write("\r" + style + text + (_RESET if style else ""))
        self._status = text
        self._stream.flush()

    def render_approval(self, event: dict) -> None:
        self._before_scrollback()
        question = str(event.get("text") or "")
        for row in question.split("\n"):
            if row:
                self._emit_line(row, _BOLD)
        detail = event.get("detail")
        if detail:
            for row in str(detail).split("\n"):
                self._emit_line("  " + row, _DIM)
        for index, label in enumerate(event.get("options") or [], 1):
            self._emit_line(f"  {index}) {label}")

    def render_notice(self, event: dict) -> None:
        text = str(event.get("text") or "")
        if not text:
            return
        self._before_scrollback()
        for row in text.split("\n"):
            self._emit_line(row, _DIM)

    def render_error(self, event: dict) -> None:
        text = str(event.get("text") or "")
        if not text:
            return
        self._before_scrollback()
        for row in text.split("\n"):
            self._emit_line(row, _BOLD)

    def render_selection(self, event: dict) -> None:
        if event.get("options"):
            self.render_approval(event)
        else:
            self.render_notice(event)

    def render_event(self, event: dict) -> None:
        """Dispatch one engine event; unknown kinds produce no output."""
        handlers = {
            "reply": lambda: self.render_text(str(event.get("text") or "")),
            "tool": lambda: self.render_tool(event),
            "tool_done": lambda: self.render_tool_done(event),
            "state": lambda: self.render_state(event),
            "approval": lambda: self.render_approval(event),
            "notice": lambda: self.render_notice(event),
            "error": lambda: self.render_error(event),
            "selection": lambda: self.render_selection(event),
            "done": self.finish,
        }
        handler = handlers.get(event.get("event"))
        if handler is not None:
            handler()

    def finish(self) -> None:
        """Flush the last buffered reply line and erase a lingering status."""
        self._flush_reply()
        self._clear_status()
