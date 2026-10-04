"""A stand-in xdg-desktop-portal for tests: RemoteDesktop and GlobalShortcuts on a private bus.

Implements the Request pattern the real one uses (a method returns a request
path; the answer arrives as that path's Response signal), records every call
to $FAKE_PORTAL_LOG as JSON lines, and answers with code 1 ("declined in the
dialog") when $FAKE_PORTAL_DECLINE names the method. `TriggerShortcut(s)` on
the test interface emits GlobalShortcuts.Activated, as a key press would.
"""

import json
import os
import sys

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

XML = """
<node>
  <interface name="org.freedesktop.portal.RemoteDesktop">
    <method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <method name="SelectDevices"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <method name="Start"><arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <method name="NotifyKeyboardKeysym"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="in"/></method>
    <method name="NotifyPointerButton"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="in"/></method>
    <property name="version" type="u" access="read"/>
  </interface>
  <interface name="org.freedesktop.portal.GlobalShortcuts">
    <method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <method name="BindShortcuts"><arg type="o" direction="in"/><arg type="a(sa{sv})" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <signal name="Activated"><arg type="o"/><arg type="s"/><arg type="t"/><arg type="a{sv}"/></signal>
    <signal name="Deactivated"><arg type="o"/><arg type="s"/><arg type="t"/><arg type="a{sv}"/></signal>
    <property name="version" type="u" access="read"/>
  </interface>
  <interface name="dev.shani.test.FakePortal">
    <method name="TriggerShortcut"><arg type="s" direction="in"/></method>
    <method name="ReleaseShortcut"><arg type="s" direction="in"/></method>
  </interface>
</node>
"""
LOG = os.environ.get("FAKE_PORTAL_LOG", "/dev/null")
DECLINE = set(filter(None, os.environ.get("FAKE_PORTAL_DECLINE", "").split(",")))
state = {"session": None}


def log(entry):
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    loop = GLib.MainLoop()

    def respond(conn, sender, token, code, results):
        path = f"/org/freedesktop/portal/desktop/request/{sender[1:].replace('.', '_')}/{token}"

        def emit():
            conn.emit_signal(sender, path, "org.freedesktop.portal.Request", "Response",
                             GLib.Variant("(ua{sv})", (code, results)))
            return False
        GLib.timeout_add(20, emit)
        return path

    def on_call(conn, sender, path, iface, method, params, inv):
        args = params.unpack()
        log({"iface": iface.rsplit(".", 1)[-1], "method": method,
             "args": [a if isinstance(a, (str, int)) else (sorted(a) if isinstance(a, dict) else str(a)) for a in args]})
        if method == "ReleaseShortcut":
            conn.emit_signal(None, "/org/freedesktop/portal/desktop", "org.freedesktop.portal.GlobalShortcuts",
                             "Deactivated", GLib.Variant("(osta{sv})", (state["session"] or "/", args[0], 0, {})))
            inv.return_value(None)
            return
        if method == "TriggerShortcut":
            conn.emit_signal(None, "/org/freedesktop/portal/desktop", "org.freedesktop.portal.GlobalShortcuts",
                             "Activated", GLib.Variant("(osta{sv})", (state["session"] or "/", args[0], 0, {})))
            inv.return_value(None)
            return
        if method.startswith("Notify"):
            inv.return_value(None)
            return
        opts = next(a for a in args if isinstance(a, dict))
        token = opts.get("handle_token", "t")
        code = 1 if method in DECLINE else 0
        results = {}
        if method == "CreateSession":
            state["session"] = f"/org/freedesktop/portal/desktop/session/x/{opts.get('session_handle_token', 's')}"
            results = {"session_handle": GLib.Variant("s", state["session"])}
        if method == "Start":
            results = {"devices": GLib.Variant("u", 3), "restore_token": GLib.Variant("s", "tok-1")}
        inv.return_value(GLib.Variant("(o)", (respond(conn, sender, token, code, results),)))

    def on_get(conn, sender, path, iface, prop):
        return GLib.Variant("u", 2 if "RemoteDesktop" in iface else 1)

    def on_bus(conn, name):
        for iface in node.interfaces:
            conn.register_object("/org/freedesktop/portal/desktop", iface, on_call, on_get, None)

    Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.portal.Desktop", Gio.BusNameOwnerFlags.NONE,
                     on_bus, lambda *a: print("READY", flush=True), lambda *a: loop.quit())
    loop.run()


if __name__ == "__main__":
    sys.exit(main())
