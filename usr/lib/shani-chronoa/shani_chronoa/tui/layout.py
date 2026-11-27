"""Pure geometry and text helpers for the full-screen TUI.

No terminal I/O and no imports from the rest of the TUI: a caller measures the
screen, asks for rectangles, and paints them itself. Rects are (x, y, w, h),
zero-based, with w/h==0 meaning "not present".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


def layout(
    width: int,
    height: int,
    *,
    sidebar_w: int = 24,
    detail_w: int = 30,
    detail_open: bool = False,
) -> dict[str, Rect]:
    """Return the six named rectangles for a terminal of this size.

    Widths: >=100 gives a side-by-side detail pane; 70-99 makes detail a
    full-height overlay on chat; <70 collapses the sidebar to zero width.
    Heights below 6 are clamped so chat always keeps three rows. `detail` is
    zero-sized unless `detail_open` is true.
    """
    width = max(1, int(width))
    height = max(6, int(height))
    middle_h = height - 3
    input_y, footer_y = height - 2, height - 1

    rects = {
        "header": Rect(0, 0, width, 1),
        "sidebar": Rect(0, 1, 0, middle_h),
        "chat": Rect(0, 1, width, middle_h),
        "detail": Rect(0, 0, 0, 0),
        "input": Rect(0, input_y, width, 1),
        "footer": Rect(0, footer_y, width, 1),
    }

    if width >= 100:
        side = max(0, min(int(sidebar_w), width - 1))
        if detail_open:
            side = min(side, width - max(0, int(detail_w)) - 1)
        detail = max(0, min(int(detail_w), width - side - 1))
        rects["sidebar"] = Rect(0, 1, side, middle_h)
        chat_w = width - side - (detail if detail_open else 0)
        rects["chat"] = Rect(side, 1, chat_w, middle_h)
        if detail_open:
            rects["detail"] = Rect(width - detail, 1, detail, middle_h)
    elif width >= 70:
        side = max(0, min(int(sidebar_w), width - 1))
        rects["sidebar"] = Rect(0, 1, side, middle_h)
        rects["chat"] = Rect(side, 1, width - side, middle_h)
        if detail_open:
            rects["detail"] = Rect(side, 1, width - side, middle_h)
    elif detail_open:
        rects["detail"] = Rect(0, 1, width, middle_h)

    return rects


def wrap(text: str, width: int) -> list[str]:
    """Word-wrap text into lines no wider than `width`; split overlong words."""
    width = max(1, int(width))
    if not text:
        return []
    lines: list[str] = []
    for source in str(text).split("\n"):
        words = source.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            while len(word) > width:
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:width])
                word = word[width:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
    return lines


def clamp_scroll(total: int, viewport: int, scroll: int) -> int:
    """Clamp a scroll offset into [0, total - viewport]."""
    total, viewport, scroll = int(total), max(0, int(viewport)), int(scroll)
    return max(0, min(scroll, max(0, total - viewport)))
