"""xdg-desktop-portal clients: RemoteDesktop (keyboard/pointer input) and GlobalShortcuts.

Why portals: on Wayland an application may not inject input or grab a key
system-wide by itself - that is the point of Wayland - so GNOME (mutter) and
Plasma (KWin) both expose these through xdg-desktop-portal, which asks the
person once in a system dialog. Both images ship a portal backend (gnome /
kde) with RemoteDesktop and GlobalShortcuts; neither ships xdotool, and
wtype/dotool need things the images do not give a user (see input_control).

Every portal call follows the Request pattern: the method returns a request
object path at once, and the real answer arrives later as that object's
`Response` signal. `PortalClient.request` subscribes *before* calling (the
documented way to avoid missing a fast reply), then iterates its own
MainContext until the answer or a timeout - so it works from a skill
subprocess and from a background thread alike, never on the GTK main loop.

RemoteDesktop permission is kept with `persist_mode=2` and the returned
`restore_token` (stored under $XDG_STATE_HOME), so only the first use shows
the dialog; a token the portal no longer honours just shows it again.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Callable, Optional

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
RD_IFACE = "org.freedesktop.portal.RemoteDesktop"
GS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
KEYBOARD, POINTER = 1, 2
BTN = {"left": 0x110, "right": 0x111, "middle": 0x112}  # evdev codes

#: Named keys -> X keysyms (the portal takes keysyms). Printable characters use
#: their Latin-1 value or the Unicode keysym range 0x01000000 + codepoint.
KEYSYMS = {
    "return": 0xFF0D, "enter": 0xFF0D, "tab": 0xFF09, "escape": 0xFF1B, "esc": 0xFF1B, "backspace": 0xFF08,
    "delete": 0xFFFF, "space": 0x20, "home": 0xFF50, "end": 0xFF57, "left": 0xFF51, "up": 0xFF52,
    "right": 0xFF53, "down": 0xFF54, "pageup": 0xFF55, "page_up": 0xFF55, "pagedown": 0xFF56,
    "page_down": 0xFF56, "insert": 0xFF63, "ctrl": 0xFFE3, "control": 0xFFE3, "shift": 0xFFE1,
    "alt": 0xFFE9, "super": 0xFFEB, "meta": 0xFFEB, "win": 0xFFEB, "menu": 0xFF67, "print": 0xFF61,
    **{f"f{i}": 0xFFBD + i for i in range(1, 13)},
}


class PortalError(Exception):
    """The portal is missing, refused, or the person said no - with which, in the message."""


def keysym_for(token: str) -> int:
    t = token.strip()
    if t.lower() in KEYSYMS:
        return KEYSYMS[t.lower()]
    if len(t) == 1:
        cp = ord(t)
        return cp if (0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF) else 0x01000000 + cp
    raise ValueError(f"{token!r} is not a key name")


def _state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "shani-chronoa"


class PortalClient:
    """One connection and one MainContext of its own; blocking requests that never touch the GTK loop."""

    def __init__(self, connection=None, timeout: float = 60.0) -> None:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.context = GLib.MainContext.new()
        self.context.push_thread_default()
        try:
            self.bus = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:  # noqa: BLE001
            self.context.pop_thread_default()
            raise PortalError(f"no session bus to reach the desktop portal ({exc})") from exc
        self.timeout = timeout
        self._sender = self.bus.get_unique_name()[1:].replace(".", "_")

    def close(self) -> None:
        try:
            self.context.pop_thread_default()
        except Exception:  # noqa: BLE001
            pass

    def version(self, iface: str) -> int:
        try:
            reply = self.bus.call_sync(PORTAL_BUS, PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
                                       self.GLib.Variant("(ss)", (iface, "version")), None,
                                       self.Gio.DBusCallFlags.NONE, 5000, None)
            return int(reply.unpack()[0])
        except Exception:  # noqa: BLE001 - no portal, or no such interface
            return 0

    def request(self, iface: str, method: str, params, options_index: int) -> dict:
        """Call a Request-pattern method and wait for its Response; returns the results dict."""
        GLib = self.GLib
        token = "chronoa" + secrets.token_hex(6)
        path = f"{PORTAL_PATH}/request/{self._sender}/{token}"
        answer = {}
        loop = GLib.MainLoop.new(self.context, False)

        def on_response(_conn, _sender, _path, _iface, _signal, args):
            answer["code"], answer["results"] = args.unpack()
            loop.quit()

        sub = self.bus.signal_subscribe(PORTAL_BUS, "org.freedesktop.portal.Request", "Response", path, None,
                                        self.Gio.DBusSignalFlags.NONE, on_response)
        try:
            values = list(params)
            opts = dict(values[options_index])
            opts["handle_token"] = GLib.Variant("s", token)
            values[options_index] = opts
            sig = method_signature(iface, method)
            try:
                self.bus.call_sync(PORTAL_BUS, PORTAL_PATH, iface, method, GLib.Variant(sig, tuple(values)), None,
                                   self.Gio.DBusCallFlags.NONE, int(self.timeout * 1000), None)
            except Exception as exc:  # noqa: BLE001
                raise PortalError(f"the desktop portal has no {iface.rsplit('.', 1)[-1]} ({str(exc)[:120]})") from exc
            timer = GLib.timeout_source_new(int(self.timeout * 1000))
            timer.set_callback(lambda *_: (loop.quit(), False)[1])
            timer.attach(self.context)
            loop.run()
            timer.destroy()
        finally:
            self.bus.signal_unsubscribe(sub)
        if "code" not in answer:
            raise PortalError(f"the portal did not answer {method} within {self.timeout:g}s")
        if answer["code"] == 1:
            raise PortalError("the request was declined in the desktop's permission dialog")
        if answer["code"] != 0:
            raise PortalError(f"the portal ended {method} without an answer (code {answer['code']})")
        return answer["results"]

    def call(self, iface: str, method: str, sig: str, values: tuple) -> None:
        self.bus.call_sync(PORTAL_BUS, PORTAL_PATH, iface, method, self.GLib.Variant(sig, values), None,
                           self.Gio.DBusCallFlags.NONE, 5000, None)


_SIGNATURES = {
    (RD_IFACE, "CreateSession"): "(a{sv})",
    (RD_IFACE, "SelectDevices"): "(oa{sv})",
    (RD_IFACE, "Start"): "(osa{sv})",
    (GS_IFACE, "CreateSession"): "(a{sv})",
    (GS_IFACE, "BindShortcuts"): "(oa(sa{sv})sa{sv})",
}


def method_signature(iface: str, method: str) -> str:
    return _SIGNATURES[(iface, method)]


class RemoteInput:
    """A RemoteDesktop session for one burst of input; `with RemoteInput() as ri: ri.keysym(...)`."""

    TOKEN_FILE = "remote-desktop.token"

    def __init__(self, devices: int = KEYBOARD | POINTER, client: Optional[PortalClient] = None) -> None:
        self.client = client or PortalClient()
        self.devices = devices
        self.session = None

    def __enter__(self) -> "RemoteInput":
        c, GLib = self.client, self.client.GLib
        if c.version(RD_IFACE) == 0:
            raise PortalError("this desktop's portal does not offer RemoteDesktop")
        sess_token = "chronoa" + secrets.token_hex(6)
        res = c.request(RD_IFACE, "CreateSession", ({"session_handle_token": GLib.Variant("s", sess_token)},), 0)
        self.session = res.get("session_handle")
        if not self.session:
            raise PortalError("the portal created no session")
        opts = {"types": GLib.Variant("u", self.devices), "persist_mode": GLib.Variant("u", 2)}
        saved = _state_dir() / self.TOKEN_FILE
        if saved.is_file() and saved.read_text().strip():
            opts["restore_token"] = GLib.Variant("s", saved.read_text().strip())
        c.request(RD_IFACE, "SelectDevices", (self.session, opts), 1)
        started = c.request(RD_IFACE, "Start", (self.session, "", {}), 2)
        token = started.get("restore_token")
        if token:
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text(token)
            os.chmod(saved, 0o600)
        granted = int(started.get("devices", 0))
        if not granted & self.devices:
            raise PortalError("the portal started the session without the input devices that were asked for")
        return self

    def __exit__(self, *exc) -> None:
        if self.session:
            try:
                self.client.bus.call_sync(PORTAL_BUS, self.session, "org.freedesktop.portal.Session", "Close",
                                          None, None, self.client.Gio.DBusCallFlags.NONE, 2000, None)
            except Exception:  # noqa: BLE001
                pass
        self.client.close()

    def keysym(self, keysym: int, pressed: bool) -> None:
        self.client.call(RD_IFACE, "NotifyKeyboardKeysym", "(oa{sv}iu)", (self.session, {}, keysym, 1 if pressed else 0))

    def tap(self, keysym: int) -> None:
        self.keysym(keysym, True)
        self.keysym(keysym, False)

    def chord(self, keys: "list[int]") -> None:
        for k in keys:
            self.keysym(k, True)
        for k in reversed(keys):
            self.keysym(k, False)

    def type_text(self, text: str) -> None:
        for ch in text:
            self.tap(KEYSYMS["return"] if ch == "\n" else KEYSYMS["tab"] if ch == "\t" else keysym_for(ch))

    def click(self, button: str = "left", count: int = 1) -> None:
        code = BTN[button]
        for _ in range(count):
            self.client.call(RD_IFACE, "NotifyPointerButton", "(oa{sv}iu)", (self.session, {}, code, 1))
            self.client.call(RD_IFACE, "NotifyPointerButton", "(oa{sv}iu)", (self.session, {}, code, 0))


def bind_global_shortcut(shortcut_id: str, description: str, preferred: str,
                         on_activated: Callable[[], None], client: Optional[PortalClient] = None,
                         on_deactivated: Optional[Callable[[], None]] = None):
    """Bind one system-wide shortcut through GlobalShortcuts; returns the client holding the session open.

    The caller keeps the returned client alive (and its context iterating) for
    as long as the shortcut should work: the session ends with the connection.
    """
    c = client or PortalClient(timeout=300)  # the first bind shows a dialog a person must answer
    GLib = c.GLib
    if c.version(GS_IFACE) == 0:
        raise PortalError("this desktop's portal does not offer GlobalShortcuts")
    res = c.request(GS_IFACE, "CreateSession",
                    ({"session_handle_token": GLib.Variant("s", "chronoa" + secrets.token_hex(6))},), 0)
    session = res.get("session_handle")
    if not session:
        raise PortalError("the portal created no shortcut session")
    shortcuts = [(shortcut_id, {"description": GLib.Variant("s", description),
                                "preferred_trigger": GLib.Variant("s", preferred)})]
    c.request(GS_IFACE, "BindShortcuts", (session, shortcuts, "", {}), 3)

    def on_signal(_conn, _sender, _path, _iface, _signal, args):
        if args.unpack()[1] == shortcut_id:
            on_activated()

    c.bus.signal_subscribe(PORTAL_BUS, GS_IFACE, "Activated", PORTAL_PATH, None, 0, on_signal)
    if on_deactivated is not None:
        # the key's release (GlobalShortcuts.Deactivated): what makes hold-to-talk possible
        def on_release(_conn, _sender, _path, _iface, _signal, args):
            if args.unpack()[1] == shortcut_id:
                on_deactivated()
        c.bus.signal_subscribe(PORTAL_BUS, GS_IFACE, "Deactivated", PORTAL_PATH, None, 0, on_release)
    c.session = session
    return c
