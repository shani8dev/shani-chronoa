"""KWin (Plasma): window control through KWin's own scripting API.

KWin runs JavaScript it is handed over D-Bus (`org.kde.kwin.Scripting`), with
the full window model - list, activate, close, minimize, maximize, fullscreen,
geometry, virtual desktops. That is Plasma's supported route, on Wayland and
X11 alike, and needs no extension.

A loaded script cannot return a value, so each one calls back to a one-shot
object this process exports on its own unique bus name, then is unloaded. The
script text is fixed per verb; the only data in it is a JSON-encoded argument
object, so a window title or id can never become code.

Written for Plasma 6 (`workspace.windowList`, `activeWindow`, `desktops`) with
the Plasma 5 names as fallbacks.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from typing import List, Tuple

from shani_chronoa.windows import Backend, Window, WindowError

_TIMEOUT_S = 5.0
_REPLY_IFACE = "org.shani.Chronoa.KWinReply"
_REPLY_XML = (f'<node><interface name="{_REPLY_IFACE}"><method name="Reply">'
              '<arg type="s" direction="in" name="json"/></method></interface></node>')

# Shared prelude: Plasma 6 names first, Plasma 5 second.
_PRELUDE = r"""
const all = () => (workspace.windowList ? workspace.windowList() : workspace.clientList())
    .filter(w => (w.normalWindow || w.dialog) && !w.skipTaskbar);
const idOf = w => String(w.internalId);
const find = id => {
    const w = all().find(x => idOf(x) === id);
    if (!w) throw new Error("no window has id " + id + "; it may have closed - list the windows again");
    return w;
};
const active = () => (workspace.activeWindow !== undefined ? workspace.activeWindow : workspace.activeClient);
const deskIndex = w => {
    if (w.onAllDesktops) return -1;
    if (w.desktops !== undefined) return w.desktops.length ? workspace.desktops.indexOf(w.desktops[0]) : -1;
    return w.desktop - 1;
};
const maximized = w => {
    const area = workspace.clientArea(KWin.MaximizeArea, w);
    const g = w.frameGeometry;
    return g.width >= area.width && g.height >= area.height && !w.fullScreen;
};
const rect = (x, y, wd, ht) => (typeof Qt !== "undefined" && Qt.rect) ? Qt.rect(x, y, wd, ht) : {x: x, y: y, width: wd, height: ht};
"""

_VERBS = {
    "list": r"""
return all().map(w => ({id: idOf(w), title: w.caption || "", app_id: w.desktopFileName || w.resourceClass || "",
    pid: w.pid || 0, workspace: deskIndex(w), focused: w === active(), minimized: !!w.minimized,
    maximized: maximized(w), fullscreen: !!w.fullScreen,
    x: w.frameGeometry.x, y: w.frameGeometry.y, width: w.frameGeometry.width, height: w.frameGeometry.height}));
""",
    "focus": r"""
const w = find(A.id);
if (w.minimized) w.minimized = false;
if (workspace.activeWindow !== undefined) workspace.activeWindow = w; else workspace.activeClient = w;
""",
    "close": "find(A.id).closeWindow();",
    "minimize": r"""
const w = find(A.id);
if (!w.minimizable) throw new Error("this window cannot be minimized");
w.minimized = true;
""",
    "maximize": r"""
const w = find(A.id);
if (A.on && !w.maximizable) throw new Error("this window cannot be maximized");
w.setMaximize(A.on, A.on);
""",
    "fullscreen": r"""
const w = find(A.id);
if (A.on && !w.fullScreenable) throw new Error("this window cannot be made fullscreen");
w.fullScreen = A.on;
""",
    "move": r"""
const w = find(A.id);
if (!w.moveable) throw new Error("this window does not allow moving");
if (maximized(w)) w.setMaximize(false, false);
const g = w.frameGeometry;
w.frameGeometry = rect(A.x, A.y, g.width, g.height);
""",
    "resize": r"""
const w = find(A.id);
if (!w.resizeable) throw new Error("this window does not allow resizing");
if (maximized(w)) w.setMaximize(false, false);
const g = w.frameGeometry;
w.frameGeometry = rect(g.x, g.y, A.width, A.height);
""",
    "set_workspace": r"""
const w = find(A.id);
const n = workspace.desktops !== undefined ? workspace.desktops.length : workspace.desktops_count || workspace.desktops;
if (A.index < 0 || A.index >= n) throw new Error("workspace " + A.index + " does not exist; there are " + n + " (0 to " + (n - 1) + ")");
if (workspace.desktops !== undefined && Array.isArray(workspace.desktops)) w.desktops = [workspace.desktops[A.index]];
else w.desktop = A.index + 1;
""",
}


def _gio():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    return Gio, GLib


def available() -> Tuple[object, str]:
    try:
        Gio, GLib = _gio()
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", ("org.kde.KWin",)), None,
                              Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    except Exception as exc:  # noqa: BLE001
        return None, f"the session bus could not be reached ({exc})"
    if not owner:
        return None, "this looks like Plasma, but KWin is not on the session bus"
    return KWinBackend(), ""


def run_script(verb: str, args: dict, timeout: float = _TIMEOUT_S):
    """Run one fixed verb in KWin and return what it answered."""
    Gio, GLib = _gio()
    context = GLib.MainContext.new()
    context.push_thread_default()
    path = f"/org/shani/Chronoa/KWinReply/r{uuid.uuid4().hex}"
    name = f"chronoa-{uuid.uuid4().hex}"
    reply: dict = {}
    script_file = None
    bus = None
    reg = 0
    try:
        # A private connection, so its callbacks are dispatched on `context`.
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        bus = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

        def on_call(_conn, _sender, _path, _iface, _method, params, invocation):
            reply["raw"] = params.unpack()[0]
            invocation.return_value(None)

        node = Gio.DBusNodeInfo.new_for_xml(_REPLY_XML)
        reg = bus.register_object(path, node.interfaces[0], on_call, None, None)
        js = (_PRELUDE + f"const A = {json.dumps(args)};\n"
              "let R;\ntry { R = JSON.stringify({ok: true, value: (function () {\n"
              + _VERBS[verb] +
              "\n})()}); } catch (e) { R = JSON.stringify({ok: false, error: String(e && e.message || e)}); }\n"
              f"callDBus({json.dumps(bus.get_unique_name())}, {json.dumps(path)}, "
              f"{json.dumps(_REPLY_IFACE)}, 'Reply', R);\n")
        fd, script_file = tempfile.mkstemp(prefix="chronoa-kwin-", suffix=".js",
                                           dir=os.environ.get("XDG_RUNTIME_DIR") or None)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(js)

        def call(obj, iface, method, params, sig):
            return bus.call_sync("org.kde.KWin", obj, iface, method, params, GLib.VariantType(sig) if sig else None,
                                 Gio.DBusCallFlags.NONE, int(timeout * 1000), None)

        sid = call("/Scripting", "org.kde.kwin.Scripting", "loadScript",
                   GLib.Variant("(ss)", (script_file, name)), "(i)").unpack()[0]
        if sid < 0:
            raise WindowError("KWin refused to load the window script")
        for obj in (f"/Scripting/Script{sid}", f"/{sid}"):  # Plasma 6, then 5
            try:
                call(obj, "org.kde.kwin.Script", "run", None, None)
                break
            except GLib.Error:
                continue
        else:
            raise WindowError("KWin loaded the window script but would not run it")
        deadline = time.monotonic() + timeout
        while "raw" not in reply and time.monotonic() < deadline:
            context.iteration(False) or time.sleep(0.01)
        try:
            call("/Scripting", "org.kde.kwin.Scripting", "unloadScript", GLib.Variant("(s)", (name,)), None)
        except GLib.Error:
            pass
    except GLib.Error as exc:
        raise WindowError(f"KWin did not answer: {exc.message}") from None
    finally:
        if bus is not None and reg:
            bus.unregister_object(reg)
        if bus is not None:
            bus.close_sync(None)
        context.pop_thread_default()
        if script_file:
            try:
                os.unlink(script_file)
            except OSError:
                pass
    if "raw" not in reply:
        raise WindowError(f"KWin ran the window script but did not answer within {timeout:g}s")
    result = json.loads(reply["raw"])
    if not result.get("ok"):
        raise WindowError(result.get("error") or "KWin reported an error")
    return result.get("value")


class KWinBackend(Backend):
    name = "KWin"

    def list(self) -> List[Window]:
        return [Window(**{k: d[k] for k in Window.__dataclass_fields__ if k in d})
                for d in run_script("list", {}) or []]

    def focus(self, wid):
        run_script("focus", {"id": str(wid)})

    def close(self, wid):
        run_script("close", {"id": str(wid)})

    def minimize(self, wid):
        run_script("minimize", {"id": str(wid)})

    def maximize(self, wid, on=True):
        run_script("maximize", {"id": str(wid), "on": bool(on)})

    def fullscreen(self, wid, on=True):
        run_script("fullscreen", {"id": str(wid), "on": bool(on)})

    def move(self, wid, x, y):
        run_script("move", {"id": str(wid), "x": int(x), "y": int(y)})

    def resize(self, wid, width, height):
        if int(width) < 1 or int(height) < 1:
            raise WindowError("width and height must be positive")
        run_script("resize", {"id": str(wid), "width": int(width), "height": int(height)})

    def set_workspace(self, wid, index):
        run_script("set_workspace", {"id": str(wid), "index": int(index)})
