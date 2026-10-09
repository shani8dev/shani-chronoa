"""GNOME: window control through the Chronoa Shell extension.

GNOME has no Wayland protocol through which another program may focus, close or
move a window, and `org.gnome.Shell.Eval` is locked outside unsafe mode, so the
honest route is a Shell extension. Ours (`chronoa-windows@shani.dev`, shipped
under `usr/share/gnome-shell/extensions/`) exports a fixed D-Bus interface - the
window verbs below and nothing else, never a way to run code in the shell.

Whether it is enabled is the person's choice; this module only reports that it
is not, with the command that turns it on.
"""

from __future__ import annotations

import json
from typing import List, Tuple

from shani_chronoa.windows import Backend, Window, WindowError

BUS_NAME = "org.shani.Chronoa.Windows"
OBJECT_PATH = "/org/shani/Chronoa/Windows"
INTERFACE = "org.shani.Chronoa.Windows"
EXTENSION_UUID = "chronoa-windows@shani.dev"
_TIMEOUT_MS = 5000


def _bus():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    return Gio, Gio.bus_get_sync(Gio.BusType.SESSION, None)


def available() -> Tuple[object, str]:
    try:
        Gio, bus = _bus()
        from gi.repository import GLib
        owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                              "org.freedesktop.DBus", "NameHasOwner",
                              GLib.Variant("(s)", (BUS_NAME,)), None,
                              Gio.DBusCallFlags.NONE, _TIMEOUT_MS, None).unpack()[0]
    except Exception as exc:  # noqa: BLE001 - no session bus is an answer, not a crash
        return None, f"the session bus could not be reached ({exc})"
    if not owner:
        # Without the extension, the accessibility bus still closes, minimizes
        # and maximizes GTK4 windows (measured) - so use it, and let each
        # refusal carry how to get the rest.
        from shani_chronoa.windows import atspi
        return atspi.available(
            "Focusing, moving and the rest need Chronoa's GNOME Shell extension, "
            "which is not enabled - turning it on is the permission: "
            f"`gnome-extensions enable {EXTENSION_UUID}` (or the Extensions app). "
            "A newly installed extension needs a fresh login before GNOME sees it")
    return GnomeBackend(), ""


class GnomeBackend(Backend):
    name = "GNOME"

    def _call(self, method: str, signature: str = "", args: tuple = ()):
        Gio, bus = _bus()
        from gi.repository import GLib
        try:
            reply = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method,
                                  GLib.Variant(f"({signature})", args) if signature else None,
                                  None, Gio.DBusCallFlags.NONE, _TIMEOUT_MS, None)
        except GLib.Error as exc:
            raise WindowError(_plain(exc.message)) from None
        return reply.unpack() if reply is not None else ()

    def list(self) -> List[Window]:
        raw = self._call("List")[0]
        return [Window(id=str(d["id"]), title=d.get("title", ""), app_id=d.get("app_id") or d.get("wm_class", ""),
                       pid=int(d.get("pid") or 0), workspace=int(d.get("workspace", -1)),
                       focused=bool(d.get("focused")), minimized=bool(d.get("minimized")),
                       maximized=bool(d.get("maximized")), fullscreen=bool(d.get("fullscreen")),
                       x=int(d.get("x", 0)), y=int(d.get("y", 0)),
                       width=int(d.get("width", 0)), height=int(d.get("height", 0)))
                for d in json.loads(raw)]

    def focus(self, wid):
        self._call("Focus", "t", (_id(wid),))

    def close(self, wid):
        self._call("Close", "t", (_id(wid),))

    def minimize(self, wid):
        self._call("Minimize", "t", (_id(wid),))

    def maximize(self, wid, on=True):
        self._call("Maximize", "tb", (_id(wid), bool(on)))

    def fullscreen(self, wid, on=True):
        self._call("Fullscreen", "tb", (_id(wid), bool(on)))

    def move(self, wid, x, y):
        self._call("Move", "tii", (_id(wid), int(x), int(y)))

    def resize(self, wid, width, height):
        self._call("Resize", "tii", (_id(wid), int(width), int(height)))

    def set_workspace(self, wid, index):
        self._call("SetWorkspace", "ti", (_id(wid), int(index)))


def _id(wid) -> int:
    try:
        return int(str(wid))
    except ValueError:
        raise WindowError(f"{wid!r} is not a GNOME window id; list the windows to get one") from None


def _plain(message: str) -> str:
    """The extension's own sentence, without the GDBus/JS error prefix."""
    for marker in ("JSError.Error: ", "JSError: ", "GDBus.Error:"):
        if marker in message:
            message = message.split(marker, 1)[1]
    return message.strip()
