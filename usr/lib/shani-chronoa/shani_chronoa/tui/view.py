"""The full multi-pane Chronoa view: shell, not brain.

This module lays out panes, routes keys and paints frames. Every answer,
command, approval and consent decision still goes through the app's existing
`handle`/`ask`/`capability` code; the panes only read Inventory sections through
the bounded loader and prefill input lines. No engine import, no new D-Bus
method and no dependency beyond the standard library.
"""

from __future__ import annotations

import os
from typing import Optional

from shani_chronoa.tui import commands, layout, modal, panels
from shani_chronoa.tui.canvas import Canvas
from shani_chronoa.tui.fullscreen import PROMPT
from shani_chronoa.tui.loader import PanelLoader
from shani_chronoa.tui.palette import Palette

SECTIONS = ("status", "conversations", "skills", "capabilities", "models")
DETAIL_SECTIONS = (
    "status",
    "conversations",
    "skills",
    "capabilities",
    "models",
    "routines",
    "triggers",
    "privacy",
    "memory",
    "senses",
)
_SIDEBAR_COMMANDS = ("/conversations ", "/skills ", "/models ", "/status ")
_FOCUS = ("input", "sidebar", "detail")


class FullScreenView:
    """Panes, focus, overlays and one buffered frame per dirty state."""

    def __init__(self, app, screen, stream, *, color: bool = False) -> None:
        self.app = app
        self.screen = screen
        self.stream = stream
        self.color = color
        self.canvas: Optional[Canvas] = None
        self.rects: dict = {}
        self.focus = "input"
        self.scroll = 0
        self.detail_scroll = 0
        self.sidebar_index = 0
        self.detail_section = "status"
        self.detail_open = False
        self.loader = PanelLoader(app.client, SECTIONS)
        self.palette: Optional[Palette] = None
        self.palette_query = ""
        self.modal = None
        self.completion: list = []
        self.completion_index = 0
        self.dirty = True
        self._size = (0, 0)
        self._answer = None

    # -- lifecycle --------------------------------------------------------

    def enter(self) -> None:
        self.screen.enter()
        self.loader.start()
        self.draw()

    def exit(self) -> None:
        self.loader.stop()
        self.screen.exit()

    def refresh_panels(self) -> None:
        for section in SECTIONS:
            self.loader.request(section, force=True)
        self.dirty = True

    def toggle_detail(self) -> None:
        self.detail_open = not self.detail_open
        self.focus = "detail" if self.detail_open else "input"
        if self.detail_open:
            self.loader.request(self.detail_section)
        self.dirty = True

    def open_palette(self) -> None:
        self.palette = Palette(commands.palette_commands())
        self.palette_query = ""
        self.palette.filter("")
        self.dirty = True

    # -- drawing ----------------------------------------------------------

    def draw(self) -> None:
        if not self.screen.isatty:
            return
        width, height = self.screen.size()
        if (width, height) != self._size:
            self._size = (width, height)
            self.screen.on_resize()
        rects = layout.layout(width, height, detail_open=self.detail_open)
        self.rects = rects
        canvas = Canvas(height, width, color=self.color)
        self._draw_header(canvas, rects["header"], width)
        self._draw_sidebar(canvas, rects["sidebar"])
        self._draw_chat(canvas, rects["chat"])
        if self.detail_open and rects["detail"].w:
            self._draw_detail(canvas, rects["detail"])
        input_rect = rects["input"]
        canvas.text(
            input_rect.y,
            input_rect.x,
            PROMPT + self.app.editor.value[: max(0, input_rect.w - len(PROMPT))],
        )
        canvas.text(
            rects["footer"].y,
            rects["footer"].x,
            "/help commands · @file attach · /quit exit · Tab focus · PgUp/PgDn scroll",
        )
        if self.completion and self.focus == "input" and self.modal is None:
            self._draw_completion(canvas, input_rect)
        if self.palette is not None:
            self.palette.render(
                canvas,
                layout.Rect(
                    min(8, max(0, width // 6)),
                    2,
                    max(20, width - 2 * min(8, max(0, width // 6))),
                    min(14, max(4, height - 6)),
                ),
                self.palette_query,
                self.palette.index,
            )
        if self.modal is not None:
            self.modal.render(canvas, rects["chat"])
        row = input_rect.y
        col = (
            input_rect.x
            + len(PROMPT)
            + min(self.app.editor.cursor, input_rect.w - len(PROMPT))
        )
        cursor = (row, col) if self.focus == "input" and self.modal is None else None
        self.screen.commit(
            canvas,
            cursor=cursor,
            visible=cursor is not None,
            force_rows=(row, rects["footer"].y),
        )
        self.dirty = False

    def _draw_header(self, canvas, rect, width: int) -> None:
        text = (
            f" Chronoa · {self.app.model} · {self.app.state} · focus: {self.focus}"
            "    Ctrl+P palette · Ctrl+O details"
        )
        canvas.text(rect.y, rect.x, text[:width])

    def _draw_sidebar(self, canvas, rect) -> None:
        if not rect.w:
            return
        canvas.box(rect, "CHRONOA")
        lines = panels.sidebar_lines(self.loader, rect.w - 2)
        for offset, line in enumerate(lines[: max(0, rect.h - 2)]):
            canvas.text(rect.y + 1 + offset, rect.x + 1, line)

    def _draw_chat(self, canvas, rect) -> None:
        canvas.box(rect, "CHAT")
        viewport = max(1, rect.h - 2)
        total = len(self.app.transcript.lines)
        self.scroll = min(max(self.scroll, 0), max(0, total - viewport))
        start = max(0, total - viewport - self.scroll)
        for offset, line in enumerate(
            self.app.transcript.lines[start : start + viewport]
        ):
            canvas.text(rect.y + 1 + offset, rect.x + 1, line[: max(0, rect.w - 2)])

    def _draw_detail(self, canvas, rect) -> None:
        canvas.box(rect, self.detail_section.upper())
        entry = self.loader.snapshot(self.detail_section)
        data = entry.get("data") if isinstance(entry, dict) else None
        if entry and entry.get("error"):
            data = {"error": entry["error"]}
        lines = panels.detail_lines(self.detail_section, data, rect.w - 2)
        viewport = max(1, rect.h - 2)
        start = max(0, min(self.detail_scroll, max(0, len(lines) - viewport)))
        for offset, line in enumerate(lines[start : start + viewport]):
            canvas.text(rect.y + 1 + offset, rect.x + 1, line)

    def _draw_completion(self, canvas, input_rect) -> None:
        height = min(5, len(self.completion))
        top = max(1, input_rect.y - height - 1)
        for index, match in enumerate(self.completion[:height]):
            marker = "›" if index == self.completion_index else " "
            canvas.text(top + index, input_rect.x, f"{marker} {match}"[: input_rect.w])

    # -- key routing ------------------------------------------------------

    def run(self) -> int:
        self.enter()
        try:
            while not self.app.stop:
                ready = self.loader.drain()
                if ready:
                    status = ready.get("status")
                    data = status.get("data") if isinstance(status, dict) else None
                    items = data.get("items") if isinstance(data, dict) else None
                    if isinstance(items, dict) and items.get("llm_model"):
                        self.app.model = str(items["llm_model"])
                    self.dirty = True
                if self.dirty:
                    self.draw()
                action = self.screen.read_key(timeout=0.1)
                if action == "eof":
                    break
                if action == "idle":
                    continue
                self.handle(action)
        finally:
            self.exit()
        return 0

    def handle(self, action: str) -> None:
        if self.modal is not None:
            answer = self.modal.handle(action)
            if answer is not None:
                self._answer = answer
                self.modal = None
            self.dirty = True
            return
        if self.palette is not None:
            self._handle_palette(action)
            return
        if action in ("tab", "shift_tab"):
            delta = 1 if action == "tab" else -1
            self.focus = _FOCUS[(_FOCUS.index(self.focus) + delta) % len(_FOCUS)]
            if self.focus == "detail" and not self.detail_open:
                self.focus = "input" if delta == -1 else "detail"
                self.detail_open = self.focus == "detail"
                if self.detail_open:
                    self.loader.request(self.detail_section)
            self.dirty = True
            return
        if action == "control:p":
            self.open_palette()
            return
        if action == "control:o":
            self.toggle_detail()
            return
        if action == "control:r":
            self.refresh_panels()
            return
        if self.focus == "sidebar":
            self._handle_sidebar(action)
        elif self.focus == "detail":
            self._handle_detail(action)
        else:
            self._handle_input(action)
        self.dirty = True

    def _handle_palette(self, action: str) -> None:
        if action.startswith("text:"):
            self.palette_query += action[5:]
            self.palette.filter(self.palette_query)
        elif action == "backspace":
            self.palette_query = self.palette_query[:-1]
            self.palette.filter(self.palette_query)
        elif action in ("up", "down"):
            self.palette.move(-1 if action == "up" else 1)
        elif action == "enter":
            selected = self.palette.selected()
            if selected:
                self.app.editor.set("/" + selected.lstrip("/") + " ")
            self.palette = None
            self.focus = "input"
        elif action in ("escape", "interrupt"):
            self.palette = None
        self.dirty = True

    def _handle_sidebar(self, action: str) -> None:
        if action in ("up", "down"):
            self.sidebar_index = max(
                0, self.sidebar_index + (-1 if action == "up" else 1)
            )
        elif action == "enter":
            index = min(self.sidebar_index, len(_SIDEBAR_COMMANDS) - 1)
            self.app.editor.set(_SIDEBAR_COMMANDS[index])
            self.focus = "input"
        elif action in ("pageup", "pagedown"):
            self.sidebar_index = max(
                0, self.sidebar_index + (-5 if action == "pageup" else 5)
            )
        elif action == "escape":
            self.focus = "input"

    def _handle_detail(self, action: str) -> None:
        if action in ("up", "down"):
            self.detail_scroll = max(
                0, self.detail_scroll + (-1 if action == "up" else 1)
            )
        elif action in ("pageup", "pagedown"):
            self.detail_scroll = max(
                0, self.detail_scroll + (-10 if action == "pageup" else 10)
            )
        elif action == "escape":
            self.focus = "input"

    def _handle_input(self, action: str) -> None:
        editor = self.app.editor
        if action == "enter":
            line = editor.take()
            self.completion = []
            if line.strip():
                self.app.transcript.lines.append(PROMPT + line)
                self.scroll = 0
                self.app.handle(line)
        elif action == "interrupt":
            turn = self.app.session.current_turn if self.app.session else ""
            if turn and self.app.client.cancel(turn):
                self.app.transcript.lines.append("Turn cancelled.")
            else:
                editor.value, editor.cursor = "", 0
        elif action == "backspace":
            editor.backspace()
        elif action.startswith("text:"):
            editor.insert(action[5:])
        elif action in ("left", "right", "home", "end"):
            editor.move(action)
        elif action in ("up", "down"):
            editor.recall(action)
        elif action in ("pageup", "pagedown"):
            self.scroll = max(0, self.scroll + (-10 if action == "pageup" else 10))
        elif action == "tab":
            matches = editor.complete_next()
            self.completion = matches if len(matches) > 1 else []
            self.completion_index = 0
        elif action == "escape":
            self.completion = []
        elif action == "control":
            pass

    # -- exclusive overlays -----------------------------------------------

    def _run_modal(self, overlay) -> object:
        self.modal = overlay
        self._answer = None
        self.dirty = True
        while self._answer is None and not self.app.stop:
            if self.dirty:
                self.draw()
            action = self.screen.read_key(timeout=0.1)
            if action == "eof":
                self._answer = ""
                break
            if action == "idle":
                continue
            answer = overlay.handle(action)
            if answer is not None:
                self._answer = answer
            self.dirty = True
        self.modal = None
        self.dirty = True
        return self._answer

    def show_approval(self, event: dict) -> str:
        answer = self._run_modal(modal.ApprovalModal(event))
        return str(answer or "")

    def confirm(self, prompt: str) -> bool:
        return bool(self._run_modal(modal.ConfirmModal(prompt)))

    def secret(self, prompt: str) -> str:
        return str(self._run_modal(modal.SecretModal(prompt)) or "")
