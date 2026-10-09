"""One window API over the compositors Chronoa runs on.

`list`, `find`, `focus`, `close`, `minimize`, `maximize`, `fullscreen`, `move`,
`resize` and `set_workspace`, each answered by whichever backend this session
actually has:

- **GNOME** (`gnome.py`) - mutter gives other programs no window control on
  Wayland and locks `org.gnome.Shell.Eval`, so it goes through the
  `chronoa-windows@shani.dev` Shell extension this package ships. Enabling that
  extension is the permission; it is never enabled behind the person's back.
- **KWin** (`kwin.py`) - Plasma's own scripting API over D-Bus.
- **The accessibility bus** (`atspi.py`) - GNOME without the extension: close,
  minimize and maximize on GTK4 apps, which offer those as AT-SPI actions.
- **X11** (`x11.py`) - `xdotool` (and `wmctrl` where a verb needs it).

Input tools (`ydotool`, `wtype`, `dotool`) are deliberately *not* a backend
here. They can press Alt+F4, but only on whatever window has focus, and they
cannot list windows or say which one that is - so "close the Slack window" would
become "close whatever is in front", the wrong-window failure this package
exists to rule out. They live in `skills/input_control.py`.

GNOME, Plasma and X11 are the desktops Shanios ships, so those are the backends.
A backend never guesses: an operation it cannot do raises `WindowError` saying so, rather than doing something
nearby and reporting success.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple


class WindowError(Exception):
    """An operation that did not happen, with the sentence saying why."""


@dataclass
class Window:
    id: str
    title: str = ""
    app_id: str = ""
    pid: int = 0
    workspace: int = -1
    focused: bool = False
    minimized: bool = False
    maximized: bool = False
    fullscreen: bool = False
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    def matches(self, needle: str) -> bool:
        needle = (needle or "").strip().lower()
        return not needle or needle in f"{self.title} {self.app_id}".lower()


class Backend:
    """What every backend implements. Ids are strings, opaque to callers."""

    name = "?"

    def list(self) -> List[Window]:
        raise NotImplementedError

    def focus(self, wid: str) -> None:
        raise NotImplementedError

    def close(self, wid: str) -> None:
        raise NotImplementedError

    def minimize(self, wid: str) -> None:
        raise WindowError(f"{self.name} cannot minimize a window")

    def maximize(self, wid: str, on: bool = True) -> None:
        raise WindowError(f"{self.name} has no maximized state")

    def fullscreen(self, wid: str, on: bool = True) -> None:
        raise WindowError(f"{self.name} cannot make a window fullscreen")

    def move(self, wid: str, x: int, y: int) -> None:
        raise WindowError(f"{self.name} cannot move a window to a position")

    def resize(self, wid: str, width: int, height: int) -> None:
        raise WindowError(f"{self.name} cannot resize a window")

    def set_workspace(self, wid: str, index: int) -> None:
        raise WindowError(f"{self.name} cannot move a window to another workspace")


def _desktop() -> str:
    return " ".join(os.environ.get(k, "") for k in
                    ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION")).lower()


def is_wayland() -> bool:
    return (os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY")))


def detect() -> Tuple[Optional[Backend], str]:
    """(backend, "") for this session, or (None, why there is none).

    KDE and GNOME are named in the desktop variables, and X11 is what is left
    when no Wayland display exists.
    """
    desktop = _desktop()
    if is_wayland():
        if "kde" in desktop or "plasma" in desktop or os.environ.get("KDE_FULL_SESSION"):
            from shani_chronoa.windows import kwin
            return kwin.available()
        if "gnome" in desktop or "ubuntu" in desktop:
            from shani_chronoa.windows import gnome
            return gnome.available()
        # Another Wayland desktop: the accessibility bus is the one route that
        # does not depend on the compositor.
        from shani_chronoa.windows import atspi
        return atspi.available(
            f"this Wayland desktop ({desktop.strip() or 'unnamed'}) has no window "
            "control Chronoa uses - GNOME and Plasma do")
    if os.environ.get("DISPLAY"):
        if shutil.which("xdotool"):
            from shani_chronoa.windows.x11 import X11Backend
            return X11Backend(), ""
        return None, "this is X11, and xdotool is not installed"
    return None, "no display is set - this is a headless login or a service without a desktop"


def find(windows: List[Window], *, window_id: str = "", title_contains: str = "") -> Window:
    """The one window an id or title names, or `WindowError` saying why not.

    An ambiguous title is an error listing the candidates. Picking the first
    would act on the wrong window and look exactly like success.
    """
    if window_id:
        for w in windows:
            if w.id == str(window_id):
                return w
        raise WindowError(f"no open window has id {window_id}; list the windows again")
    if not (title_contains or "").strip():
        raise WindowError("give a window_id from list_windows, or title_contains to match by title")
    hits = [w for w in windows if w.matches(title_contains)]
    if not hits:
        raise WindowError(f"no open window's title or application contains {title_contains!r}")
    if len(hits) > 1:
        names = "\n".join(f"    {w.id}: {(w.title or w.app_id)[:60]}" for w in hits[:8])
        raise WindowError(
            f"{len(hits)} windows match {title_contains!r}, so nothing was done - guessing "
            f"would act on the wrong one. Narrow it, or pass a window_id:\n{names}")
    return hits[0]


def describe(w: Window) -> str:
    state = [s for s, on in (("focused", w.focused), ("minimized", w.minimized),
                             ("maximized", w.maximized), ("fullscreen", w.fullscreen)) if on]
    where = f"{w.width}x{w.height}+{w.x}+{w.y}" if w.width else ""
    ws = f"workspace {w.workspace}" if w.workspace >= 0 else ""
    extra = ", ".join(p for p in (ws, where, *state) if p)
    return f"{w.id:>12}  {(w.app_id or '(no app id)')[:24]:<24} {(w.title or '(no title)')[:60]}" + (
        f"  [{extra}]" if extra else "")


__all__ = ["Window", "WindowError", "Backend", "detect", "find", "describe", "is_wayland"]
