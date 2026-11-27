"""Buffered, differentially-painted terminal canvas for the full-screen TUI.

Only `commit` writes ANSI, and it does so with one write plus one flush. All
engine/sidebar strings pass through `sanitize` before entering the grid, so a
control sequence in a tool argument can never repaint the terminal.
"""

from __future__ import annotations

from typing import Any, Optional, TextIO

from shani_chronoa.tui.layout import Rect

_STYLES = {"bold": "\x1b[1m", "dim": "\x1b[2m"}
_RESET = "\x1b[0m"


def sanitize(text: Any) -> str:
    """Drop ESC, C0/C1 controls, DEL and NUL; keep printable Unicode."""
    return "".join(
        character
        for character in str(text)
        if ord(character) >= 0x20 and not 0x7F <= ord(character) <= 0x9F
    )


class Canvas:
    """A grid of `(character, style)` cells that can diff against a snapshot."""

    def __init__(self, rows: int, cols: int, *, color: bool = False) -> None:
        self.rows = max(0, int(rows))
        self.cols = max(0, int(cols))
        self.color = bool(color)
        self._cells: list[list[tuple[str, str]]] = []
        self.clear()

    def clear(self) -> None:
        self._cells = [[(" ", "") for _ in range(self.cols)] for _ in range(self.rows)]

    def _code(self, style: str) -> str:
        if not style:
            return ""
        if style in _STYLES:
            return _STYLES[style]
        return style if style.startswith("\x1b[") else ""

    def text(self, row: int, col: int, value: Any, style: str = "") -> None:
        """Write sanitized text, clipped to the grid."""
        row, col = int(row), int(col)
        if not 0 <= row < self.rows or col >= self.cols:
            return
        code = self._code(style) if self.color else ""
        cells = self._cells[row]
        for offset, character in enumerate(sanitize(value)):
            target = col + offset
            if 0 <= target < self.cols:
                cells[target] = (character, code)

    def box(self, rect: Rect, title: str) -> None:
        """Draw a Unicode-bordered box with an optional top-border title."""
        if rect.w < 2 or rect.h < 2:
            return
        inner = rect.w - 2
        prefix = " " + sanitize(title) + " " if title else ""
        prefix = prefix[:inner]
        self.text(rect.y, rect.x, "┌" + prefix + "─" * (inner - len(prefix)) + "┐")
        for offset in range(1, rect.h - 1):
            self.text(rect.y + offset, rect.x, "│" + " " * inner + "│")
        self.text(rect.y + rect.h - 1, rect.x, "└" + "─" * inner + "┘")

    def _render_row(self, row: list[tuple[str, str]]) -> str:
        parts: list[str] = []
        run = ""
        run_style = row[0][1] if row else ""
        for character, style in row:
            if style != run_style:
                if run:
                    parts.append(run_style + run + _RESET if run_style else run)
                run, run_style = "", style
            run += character
        if run:
            parts.append(run_style + run + _RESET if run_style else run)
        return "".join(parts)

    def diff(self, previous: Optional["Canvas"]) -> list[tuple[int, str]]:
        """Return `(row, rendered text)` for every row that changed."""
        if previous is None or previous.rows != self.rows or previous.cols != self.cols:
            return [
                (index, self._render_row(row)) for index, row in enumerate(self._cells)
            ]
        return [
            (index, self._render_row(row))
            for index, row in enumerate(self._cells)
            if row != previous._cells[index]
        ]

    def commit(
        self,
        stream: TextIO,
        previous: Optional["Canvas"],
        *,
        cursor: Optional[tuple[int, int]] = None,
        visible: bool = True,
        force_rows: tuple[int, ...] = (),
    ) -> "Canvas":
        """Write one frame in a single write/flush and return its snapshot."""
        changed = dict(self.diff(previous))
        for row in force_rows:
            if 0 <= row < self.rows and row not in changed:
                changed[row] = self._render_row(self._cells[row])

        out: list[str] = []
        for row in sorted(changed):
            out.append(f"\x1b[{row + 1};1H" + changed[row] + "\x1b[K")
        out.append("\x1b[?25h" if visible else "\x1b[?25l")
        if cursor is not None:
            out.append(f"\x1b[{max(0, cursor[0]) + 1};{max(0, cursor[1]) + 1}H")
        stream.write("".join(out))
        stream.flush()

        snapshot = Canvas(self.rows, self.cols, color=self.color)
        snapshot._cells = [row[:] for row in self._cells]
        return snapshot
