"""Command palette: filter, move and paint, with no engine calls.

The view owns the keystrokes; this module only keeps the match list and draws
into a Canvas through `text`/`box`. `entries` is a list of ``(name, help)``
pairs, normally `commands.palette_commands()`. Importing that table is deferred
and defensive so a caller can supply its own entries before the command table
exists.
"""

from __future__ import annotations


def _bounds(rect):
    if all(hasattr(rect, name) for name in ("x", "y", "w", "h")):
        return rect.x, rect.y, rect.w, rect.h
    x, y, w, h = rect
    return x, y, w, h


def _write(canvas, row, col, value):
    draw = getattr(canvas, "text", None)
    if callable(draw):
        draw(row, col, value)


def _frame(canvas, rect, title):
    draw = getattr(canvas, "box", None)
    if callable(draw):
        draw(rect, title)


def _default_entries():
    """Read the command table only if the caller supplied no entries."""
    try:
        from shani_chronoa.tui import commands

        builder = getattr(commands, "palette_commands", None)
        if callable(builder):
            return [tuple(pair) for pair in builder()]
        return [("/" + name, cmd.help) for name, cmd in commands.COMMANDS.items()]
    except Exception:  # noqa: BLE001 - the palette works without the table
        return []


def _display(name: str) -> str:
    return name if name.startswith("/") else "/" + name


class Palette:
    """A selectable filter over ``(name, help)`` command entries."""

    def __init__(self, entries: "list[tuple[str, str]] | None" = None) -> None:
        source = entries if entries is not None else _default_entries()
        self.entries = [(str(name), str(help)) for name, help in source]
        self.query = ""
        self.matches = list(range(len(self.entries)))
        self.index = 0

    def filter(self, query: str) -> "list[tuple[str, str]]":
        """Recompute matches on name/help; a leading slash is ignored."""
        self.query = str(query or "")
        needle = self.query.strip().lstrip("/").lower()
        self.matches = [
            i
            for i, (name, help) in enumerate(self.entries)
            if needle in name.lower() or needle in help.lower()
        ]
        self.index = 0
        return [self.entries[i] for i in self.matches]

    def move(self, delta: int) -> str:
        """Move the selection with wrap-around; return the selected name."""
        if self.matches:
            self.index = (self.index + delta) % len(self.matches)
        return self.selected()

    def selected(self) -> str:
        """The selected command name, or ``''`` when nothing matches."""
        if not self.matches:
            return ""
        return self.entries[self.matches[self.index]][0]

    def render(self, canvas, rect, query: str, index: int) -> None:
        """Paint the query and the visible matches into ``rect``."""
        if query != self.query:
            self.filter(query)
        x, y, w, h = _bounds(rect)
        if w <= 0 or h <= 0:
            return
        _frame(canvas, rect, "COMMANDS")
        _write(canvas, y + 1, x + 1, ("> " + str(query))[: max(0, w - 2)])
        visible = h - 2
        if visible <= 0:
            return
        count = len(self.matches)
        selected = min(max(0, int(index)), max(0, count - 1))
        start = max(0, min(selected, count - visible))
        for offset in range(visible):
            row = y + 2 + offset
            position = start + offset
            if position >= count:
                _write(canvas, row, x + 1, " " * (w - 2))
                continue
            name, help = self.entries[self.matches[position]]
            marker = "› " if position == selected else "  "
            line = marker + _display(name) + "  " + help
            _write(canvas, row, x + 1, line[: max(0, w - 2)])
