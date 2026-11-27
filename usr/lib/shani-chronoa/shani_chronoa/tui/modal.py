"""Exclusive-input modals: approval, confirmation and secret entry.

Each modal is a pure key-state machine. `handle` returns a resolved answer or
None to stay open; `render` only calls Canvas `text`/`box`. A secret is kept in
memory and drawn as `*` markers, never echoed to a canvas, stdout or a log.
"""

from __future__ import annotations

_REFUSE = ("interrupt", "escape", "ctrl+c", "ctrl+d", "q", "Q")
_MOVE_BACK = ("up", "left", "tab", "shift_tab")
_MOVE_FORWARD = ("down", "right")


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


def _key(action) -> str:
    """Screen action names arrive as ``text:x``; scripted tests pass raw x."""
    value = str(action or "")
    return value[5:] if value.startswith("text:") else value


def _safe(value) -> str:
    """Keep engine text from injecting escape sequences into a canvas."""
    return "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in str(value if value is not None else "")
    )


def _lines(value, width: int) -> list:
    """Sanitised, width-clipped lines of a prompt or detail string."""
    rows = str(value if value is not None else "").splitlines() or [""]
    return [_safe(row)[: max(0, width)] for row in rows]


def _options_safe(index: int, count: int) -> str:
    try:
        from shani_chronoa.tui import approve

        return approve.options_safe(index, count)
    except Exception:  # noqa: BLE001 - the hint must never break the modal
        return f"{index + 1}/{count} (Up/Down, number, Enter, q to refuse)"


class ApprovalModal:
    """Resolve an approval event to one exact option label, or ``''`` refusal."""

    def __init__(self, event: dict) -> None:
        event = event if isinstance(event, dict) else {}
        self.text = _safe(event.get("text", ""))
        self.detail = _safe(event.get("detail", ""))
        self.kind = _safe(event.get("kind", ""))
        self.options = [str(option) for option in (event.get("options") or [])]
        self.index = 0

    def handle(self, action) -> "str | None":
        """Digit/arrow select, Enter confirms, q/Esc/Ctrl+C refuse."""
        key = _key(action)
        count = len(self.options)
        if key in _REFUSE:
            return ""
        if not count:
            return None
        if key in _MOVE_BACK:
            self.index = (self.index - 1) % count
            return None
        if key in _MOVE_FORWARD:
            self.index = (self.index + 1) % count
            return None
        if key == "enter":
            return self.options[self.index]
        if key.isdigit():
            number = int(key)
            if 1 <= number <= count:
                self.index = number - 1
            return None
        if key in self.options:
            self.index = self.options.index(key)
            return key
        return None

    def hint(self) -> str:
        """The `choice [1]:` marker the pty gates wait for."""
        return f"  choice [{self.index + 1}]: {_options_safe(self.index, len(self.options))}"

    def render(self, canvas, rect) -> None:
        x, y, w, h = _bounds(rect)
        if w <= 0 or h <= 0:
            return
        _frame(canvas, rect, "APPROVAL")
        width = max(0, w - 2)
        row = y + 1
        for line in _lines(self.text, width)[: max(0, h - 2)]:
            _write(canvas, row, x + 1, line)
            row += 1
        for offset, label in enumerate(self.options):
            if row >= y + h - 1:
                break
            marker = "›" if offset == self.index else " "
            _write(
                canvas,
                row,
                x + 1,
                (f"{marker} {offset + 1}) {label}")[:width],
            )
            row += 1
        if row < y + h:
            _write(canvas, row, x + 1, self.hint()[:width])


class ConfirmModal:
    """Yes/no prompt showing the pinned `Continue? [y/N]` line."""

    def __init__(self, prompt: str) -> None:
        self.prompt = _safe(prompt)

    def handle(self, action) -> "bool | None":
        key = _key(action)
        if key in ("y", "Y", "enter"):
            return True
        if key in ("n", "N", "escape", "interrupt", "ctrl+c", "ctrl+d"):
            return False
        return None

    def render(self, canvas, rect) -> None:
        x, y, w, h = _bounds(rect)
        if w <= 0 or h <= 0:
            return
        _frame(canvas, rect, "CONFIRM")
        width = max(0, w - 2)
        row = y + 1
        for line in _lines(self.prompt, width)[: max(0, h - 2)]:
            _write(canvas, row, x + 1, line)
            row += 1
        if row < y + h:
            _write(canvas, row, x + 1, "Continue? [y/N]"[:width])


class SecretModal:
    """Collect a secret without ever writing its value to the screen."""

    def __init__(self, prompt: str) -> None:
        self.prompt = _safe(prompt)
        self.value = ""

    def handle(self, action) -> "str | None":
        key = _key(action)
        if key == "enter":
            return self.value
        if key in ("escape", "interrupt", "ctrl+c", "ctrl+d"):
            return ""
        if key in ("backspace", "left"):
            self.value = self.value[:-1]
            return None
        if key in ("up", "down", "right", "tab", "shift_tab", "pageup", "pagedown"):
            return None
        if key:
            self.value += key  # a single key, or a scripted whole string
        return None

    def render(self, canvas, rect) -> None:
        x, y, w, h = _bounds(rect)
        if w <= 0 or h <= 0:
            return
        _frame(canvas, rect, "SECRET")
        width = max(0, w - 2)
        row = y + 1
        for line in _lines(self.prompt, width)[: max(0, h - 2)]:
            _write(canvas, row, x + 1, line)
            row += 1
        if row < y + h:
            _write(canvas, row, x + 1, ("> " + "*" * len(self.value))[:width])
