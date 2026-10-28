"""niri: the compositor of ShaniOS's AI desktop (shani-desktop-niri).

niri answers over its own IPC socket ($NIRI_SOCKET), and `niri msg` is its
client: `niri msg -j windows` and `-j workspaces` say what is open, and
`niri msg action <verb> --id <window>` acts on one window by id — never on
"whatever is focused", which is the wrong-window failure this package exists
to rule out.

niri is a scrollable-tiling compositor, so some verbs mean what they mean
there and no more: "maximize" is the column's full width, "move" and "resize"
are a floating window's position and a window's size in the column, "tile left"
is half a column width with the column moved to the start. It has no minimized
state at all — that is said, not faked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import List, Tuple

from shani_chronoa.windows import Backend, Window, WindowError

_TIMEOUT_S = 5


def _niri(*args: str) -> str:
    try:
        out = subprocess.run(["niri", "msg", *args], capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowError(f"niri could not be asked ({exc})") from exc
    if out.returncode != 0:
        raise WindowError((out.stderr.strip().splitlines() or ["niri refused"])[-1])
    return out.stdout


def available() -> Tuple[object, str]:
    if not shutil.which("niri"):
        return None, "this looks like niri, but the niri command is not installed"
    if not os.environ.get("NIRI_SOCKET"):
        return None, "this looks like niri, but NIRI_SOCKET is not set (not a niri session)"
    try:
        _niri("-j", "version")
    except WindowError as exc:
        return None, f"niri did not answer ({exc})"
    return NiriBackend(), ""


class NiriBackend(Backend):
    name = "niri"

    def list(self) -> List[Window]:
        wins = json.loads(_niri("-j", "windows") or "[]")
        spaces = {w["id"]: w.get("idx", -1) for w in json.loads(_niri("-j", "workspaces") or "[]")}
        out = []
        for w in wins:
            size = ((w.get("layout") or {}).get("window_size") or [0, 0])
            out.append(Window(
                id=str(w["id"]),
                title=w.get("title") or "",
                app_id=w.get("app_id") or "",
                pid=w.get("pid") or 0,
                workspace=spaces.get(w.get("workspace_id"), -1),
                focused=bool(w.get("is_focused")),
                width=int(size[0]),
                height=int(size[1]),
            ))
        return out

    def _act(self, *args: str) -> None:
        _niri("action", *args)

    def focus(self, wid: str) -> None:
        self._act("focus-window", "--id", wid)

    def close(self, wid: str) -> None:
        self._act("close-window", "--id", wid)

    def minimize(self, wid: str) -> None:
        raise WindowError("niri has no minimized windows - a window can go to another workspace instead")

    def maximize(self, wid: str, on: bool = True) -> None:
        # the column's full width is niri's maximize; it toggles, so only when it would change
        self.focus(wid)
        self._act("maximize-column")

    def fullscreen(self, wid: str, on: bool = True) -> None:
        self._act("fullscreen-window", "--id", wid)

    def move(self, wid: str, x: int, y: int) -> None:
        # only a floating window has a position of its own
        self._act("move-window-to-floating", "--id", wid)
        self._act("move-floating-window", "--id", wid, "-x", str(int(x)), "-y", str(int(y)))

    def resize(self, wid: str, width: int, height: int) -> None:
        self._act("set-window-width", "--id", wid, str(int(width)))
        self._act("set-window-height", "--id", wid, str(int(height)))

    def set_workspace(self, wid: str, index: int) -> None:
        self._act("move-window-to-workspace", "--window-id", wid, "--focus", "false", str(int(index)))

    def tile(self, wid: str, side: str) -> None:
        if side not in ("left", "right"):
            raise WindowError("niri tiles windows side by side: left or right")
        self.focus(wid)
        self._act("set-column-width", "50%")
        self._act("move-column-to-first" if side == "left" else "move-column-to-last")
