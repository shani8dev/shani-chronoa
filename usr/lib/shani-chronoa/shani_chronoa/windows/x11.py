"""X11 window control, through the X server itself.

**`xdotool` was removed here on 2026-10-11, and not because it was flaky.** The
ShaniOS matrices (`mout/matrix-gnome.json`, `mout-plasma/shanios-matrix.json`,
both read out of pacman's file database on a booted image) say:

| | GNOME image | Plasma image |
|---|---|---|
| `xdotool` | **ABSENT** | **ABSENT** |
| `libx11` / `libxtst` | present | present |

So this backend could never have run where it ships. Every verb it offered was an
EWMH client message or a window-manager property read, all of which `x11.py` now
sends and reads directly - so this file is a thin adapter between the `Backend`
contract and the X protocol, with no external binary at all.

**What changed about the answers, not just where they come from:**

- **`app_id` was the answer to a question that had no right answer.**
  `xdotool getwindowclassname` does not exist in this build: it answers
  `Unknown command`, rc=1 - and returns **empty** rather than failing, so the old
  code read every window as its own client. `WM_CLASS` is read directly now.
- **`workspace` and `pid` are parsed from numbers, not from prose.**
  `_xprop` scraped `xprop`'s rendered text with a regex; a property read is a
  byte array, and a format mistake raises rather than quietly matching nothing.
- **`focused` is the WM's own answer.** `_NET_ACTIVE_WINDOW` rather than
  `getactivewindow`, which is the same question asked of the same server.

**When there is no X, this says so.** `xdotool` missing produced
"xdotool is not installed", which true or not is a fact about the machine; this
distinguishes "no X client library" from "no X server at :0", because those two
need opposite fixes.
"""

from __future__ import annotations

from typing import List

from shani_chronoa import x11
from shani_chronoa.windows import Backend, Window, WindowError


class X11Backend(Backend):
    name = "X11"

    def _open(self) -> x11.Display:
        try:
            return x11.Display()
        except x11.XError as exc:
            raise WindowError(
                "this X11 backend cannot run: %s. The X route needs both an X "
                "client library and a reachable display - on a Wayland session "
                "an X11 client only exists for Xwayland, which is why the "
                "RemoteDesktop portal is the route there." % exc) from None

    @staticmethod
    def _id(wid) -> int:
        """A window id in either spelling, as an int.

        **`xwininfo -tree` prints hexadecimal while every `xdotool` call speaks
        decimal**, so a caller holding `0x600004` from one and feeding it to the
        other got a lookup that reads as "the window has gone". Both spellings
        parse here and the display is asked in the one it understands.
        """
        import re

        text = str(wid).strip()
        if not re.fullmatch(r"(0x[0-9a-fA-F]+|\d+)", text):
            raise WindowError(
                "%r is not an X11 window id; list the windows to get one" % (wid,))
        return int(text, 16) if text.startswith("0x") else int(text)

    # -- reading ------------------------------------------------------------

    def _states(self, d: x11.Display, wid: int) -> "tuple[set, int, int]":
        """`(state atom names, desktop, pid)`, in one pass over the properties."""
        _kind, _fmt, raw = d._property(wid, "_NET_WM_STATE")
        atoms = set()
        if raw:
            import struct

            width = 4
            atoms = {struct.unpack_from("<L", raw, i)[0]
                     for i in range(0, len(raw) - width + 1, width)}
        names = set()
        for atom in atoms:
            # Read back by name rather than by number: a state this module has
            # not hard-coded is reported as its atom, so it is not silently
            # dropped into "none of the above".
            for known in ("_NET_WM_STATE_HIDDEN", "_NET_WM_STATE_MINIMIZED",
                          "_NET_WM_STATE_MAXIMIZED_VERT",
                          "_NET_WM_STATE_MAXIMIZED_HORZ",
                          "_NET_WM_STATE_FULLSCREEN", "_NET_WM_STATE_MODAL",
                          "_NET_WM_STATE_ABOVE", "_NET_WM_STATE_BELOW"):
                if atom == d.atom(known):
                    names.add(known)
        _k2, _f2, desk = d._property(wid, "_NET_WM_DESKTOP", "CARDINAL")
        _k3, _f3, pid = d._property(wid, "_NET_WM_PID", "CARDINAL")
        import struct as _s

        desktop = _s.unpack_from("<L", desk)[0] if len(desk) >= 4 else -1
        pidval = _s.unpack_from("<L", pid)[0] if len(pid) >= 4 else 0
        return names, int(desktop), int(pidval)

    def list(self) -> List[Window]:
        with self._open() as d:
            # `_NET_CLIENT_LIST` is the WM's own answer to "which windows are
            # there", so Mutter's guard window, its decoration frames, ibus's
            # helpers and Chromium's clipboard never appear - all of which
            # `xdotool search --name .` listed as if they were applications.
            ids = d.window_ids()
            active = d.active_window()
            out: List[Window] = []
            for wid in ids:
                title = d.name(wid)
                geo = d.geometry(wid)
                if geo is None:
                    # A window with no readable geometry cannot be placed, and
                    # reporting (0,0) would put every later click at the origin.
                    continue
                inst, klass = d.app_class(wid)
                names, desktop, pid = self._states(d, wid)
                out.append(Window(
                    id=str(wid), title=title,
                    app_id=inst or klass,
                    pid=pid,
                    workspace=-1 if desktop in (0xFFFFFFFF,) else desktop,
                    focused=wid == active,
                    minimized=any(n in names for n in
                                  ("_NET_WM_STATE_MINIMIZED",
                                   "_NET_WM_STATE_HIDDEN")),
                    maximized=("_NET_WM_STATE_MAXIMIZED_VERT" in names
                               and "_NET_WM_STATE_MAXIMIZED_HORZ" in names),
                    fullscreen="_NET_WM_STATE_FULLSCREEN" in names,
                    x=geo[0], y=geo[1], width=geo[2], height=geo[3]))
            return out

    # -- acting -------------------------------------------------------------

    def focus(self, wid):
        with self._open() as d:
            d.focus(d._id(wid))

    def close(self, wid):
        # Asking politely: `WM_DELETE_WINDOW` lets the application save, refuse
        # or ask, which a destroy would not.
        with self._open() as d:
            d.close_window(d._id(wid))

    def minimize(self, wid):
        with self._open() as d:
            d.iconify(d._id(wid))

    def maximize(self, wid, on=True):
        with self._open() as d:
            # **Two atoms.** Maximised is vertical *and* horizontal, and sending
            # one leaves a window maximised in one axis - a state that reads as
            # maximised to anyone who checks only one of them.
            d.set_state(d._id(wid),
                        ["_NET_WM_STATE_MAXIMIZED_VERT",
                         "_NET_WM_STATE_MAXIMIZED_HORZ"], on)

    def fullscreen(self, wid, on=True):
        with self._open() as d:
            d.set_state(d._id(wid), ["_NET_WM_STATE_FULLSCREEN"], on)

    def move(self, wid, x, y):
        with self._open() as d:
            d.move_window(d._id(wid), int(x), int(y))

    def resize(self, wid, width, height):
        if int(width) < 1 or int(height) < 1:
            raise WindowError("width and height must be positive")
        with self._open() as d:
            d.resize_window(d._id(wid), int(width), int(height))

    def set_workspace(self, wid, index):
        with self._open() as d:
            count = d.desktop_count()
            if count and not 0 <= int(index) < count:
                raise WindowError(
                    "workspace %s does not exist; there are %d (0 to %d)"
                    % (index, count, count - 1))
            d.set_desktop(d._id(wid), int(index))
