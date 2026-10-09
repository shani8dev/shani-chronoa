"""X11: window control through `xdotool` (EWMH), with `wmctrl` for two verbs.

`xdotool windowstate` (3.20211022+, what Arch ships) sets maximized and
fullscreen; older xdotool lacks it, so `wmctrl -b` is the fallback, and with
neither those two verbs say what to install rather than doing something nearby.
States come from `xprop` when it is present.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from typing import List, Optional

from shani_chronoa.windows import Backend, Window, WindowError

_TIMEOUT = 10


def _run(*argv: str, check: bool = True) -> str:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise WindowError(f"{argv[0]} did not answer within {_TIMEOUT}s") from None
    except OSError as exc:
        raise WindowError(f"could not run {argv[0]}: {exc}") from None
    if check and proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise WindowError(f"{argv[0]} {argv[1]} failed" + (f": {detail[-1]}" if detail else ""))
    return proc.stdout


@functools.lru_cache(maxsize=1)
def _has_windowstate() -> bool:
    try:
        out = subprocess.run(["xdotool", "help"], capture_output=True, text=True, timeout=_TIMEOUT)
        return "windowstate" in (out.stdout + out.stderr)
    except (OSError, subprocess.TimeoutExpired):
        return False


def _wid(wid) -> str:
    text = str(wid).strip()
    if not re.fullmatch(r"(0x[0-9a-fA-F]+|\d+)", text):
        raise WindowError(f"{wid!r} is not an X11 window id; list the windows to get one")
    return text


def _xprop(wid: str) -> str:
    if not shutil.which("xprop"):
        return ""
    try:
        return _run("xprop", "-id", wid, "_NET_WM_STATE", "_NET_WM_DESKTOP", "_NET_WM_PID", check=False)
    except WindowError:
        return ""


def _int_after(text: str, key: str) -> Optional[int]:
    m = re.search(rf"{key}\(CARDINAL\) = (\d+)", text)
    return int(m.group(1)) if m else None


class X11Backend(Backend):
    name = "X11"

    def list(self) -> List[Window]:
        ids = [x for x in _run("xdotool", "search", "--onlyvisible", "--name", "", check=False).split() if x]
        active = _run("xdotool", "getactivewindow", check=False).strip()
        out = []
        for wid in ids:
            title = _run("xdotool", "getwindowname", wid, check=False).strip()
            geo = _run("xdotool", "getwindowgeometry", "--shell", wid, check=False)
            g = dict(line.split("=", 1) for line in geo.splitlines() if "=" in line)
            props = _xprop(wid)
            states = props.split("_NET_WM_STATE", 1)[1].split("\n", 1)[0] if "_NET_WM_STATE(ATOM)" in props else ""
            desk = _int_after(props, "_NET_WM_DESKTOP")
            out.append(Window(
                id=wid, title=title,
                app_id=_run("xdotool", "getwindowclassname", wid, check=False).strip(),
                pid=_int_after(props, "_NET_WM_PID") or 0,
                workspace=-1 if desk is None or desk == 0xFFFFFFFF else desk,
                focused=wid == active,
                minimized="_NET_WM_STATE_HIDDEN" in states,
                maximized="_NET_WM_STATE_MAXIMIZED_VERT" in states and "_NET_WM_STATE_MAXIMIZED_HORZ" in states,
                fullscreen="_NET_WM_STATE_FULLSCREEN" in states,
                x=int(g.get("X", 0)), y=int(g.get("Y", 0)),
                width=int(g.get("WIDTH", 0)), height=int(g.get("HEIGHT", 0))))
        return out

    def focus(self, wid):
        _run("xdotool", "windowactivate", "--sync", _wid(wid))

    def close(self, wid):
        # windowclose asks politely (WM_DELETE_WINDOW); windowkill would not.
        _run("xdotool", "windowclose", _wid(wid))

    def minimize(self, wid):
        _run("xdotool", "windowminimize", "--sync", _wid(wid))

    def _state(self, wid: str, on: bool, xdo: str, wm: str) -> None:
        wid = _wid(wid)
        if _has_windowstate():
            _run("xdotool", "windowstate", "--add" if on else "--remove", xdo, wid)
        elif shutil.which("wmctrl"):
            _run("wmctrl", "-i", "-r", wid, "-b", f"{'add' if on else 'remove'},{wm}")
        else:
            raise WindowError("this xdotool is too old to set window states (it has no windowstate), "
                              "and wmctrl is not installed")

    def maximize(self, wid, on=True):
        self._state(wid, on, "MAXIMIZED_VERT,MAXIMIZED_HORZ", "maximized_vert,maximized_horz")

    def fullscreen(self, wid, on=True):
        self._state(wid, on, "FULLSCREEN", "fullscreen")

    def move(self, wid, x, y):
        _run("xdotool", "windowmove", "--sync", _wid(wid), str(int(x)), str(int(y)))

    def resize(self, wid, width, height):
        if int(width) < 1 or int(height) < 1:
            raise WindowError("width and height must be positive")
        _run("xdotool", "windowsize", "--sync", _wid(wid), str(int(width)), str(int(height)))

    def set_workspace(self, wid, index):
        n = _run("xdotool", "get_num_desktops", check=False).strip()
        if n.isdigit() and not 0 <= int(index) < int(n):
            raise WindowError(f"workspace {index} does not exist; there are {n} (0 to {int(n) - 1})")
        _run("xdotool", "set_desktop_for_window", _wid(wid), str(int(index)))
