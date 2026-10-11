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
    """The X keysym for a character or a named key.

    **The single-character test comes first and uses the *unstripped* token,
    and that ordering is the fix for a bug this box found by running it.**
    Stripping first turned a space into `""`, which is not length 1 and is not
    in `KEYSYMS` either - so `type_text("test message")` raised
    `ValueError: ' ' is not a key name` **before a single keystroke was sent**.
    `KEYSYMS` has always contained `"space": 0x20`; the strip meant it could
    never be reached. Every single-word probe typed fine, which is why this
    survived: `ZZPORTALMARK` and `zzdirect` both went through, and the failure
    looked like "the portal does not work on this desktop" rather than "the
    portal cannot type a space".
    """
    if len(token) == 1:
        cp = ord(token)
        return cp if (0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF) else 0x01000000 + cp
    t = token.strip().lower()
    if t in KEYSYMS:
        return KEYSYMS[t]
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


def pointer_position():
    """`(x, y)` of the pointer right now, or `None` when it cannot be read.

    **The portal has no "where is the pointer" method**, and `move()` needs
    one: the only drivable motion method is the *relative* one, so reaching an
    absolute point means sending the difference from here.

    Reading it through X is not a shortcut around the compositor - it is the
    compositor's own pointer, mirrored. Measured on this desktop: every motion
    the portal was asked to perform showed up in this reading, to the pixel,
    including the ones that proved the method was relative.
    """
    # **Asked of the X server, not of `xdotool`.** This read is what
    # `RemoteInput.move()` depends on - the portal moves the pointer
    # *relatively*, so an absolute request has to be turned into a difference,
    # and the difference needs the present position. Measured: with `xdotool`
    # absent from the machine, this returned `None`, `move()` refused with "the
    # pointer's present position cannot be read", and **every pointer route in the
    # driver - click, glide, and the wheel - stopped dead at one line.** The
    # server was reachable the whole time.
    #
    # `XQueryPointer` is the compositor's own pointer mirrored, exactly as the
    # `xdotool` reading was; it just does not need a binary that is not
    # installed. `x11.Dialog` is imported lazily because importing it here would
    # make the portal module depend on libX11, which is not present everywhere an
    # X server is.
    try:
        from shani_chronoa import x11

        with x11.Display() as display:
            where = display.pointer()
    except Exception:  # noqa: BLE001 - unreadable is not "at the origin"
        return None
    if where is None:
        return None
    return (where[0], where[1])


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

    def move(self, x: float, y: float) -> None:
        """Put the pointer at an **absolute** position on the screen.

        Without this, `click()` is a click wherever the pointer already happens
        to be, which is the same "success" a click 900px away would report.

        **`NotifyPointerMotion` is the RELATIVE method, and calling it with a
        pair of absolute coordinates moves the pointer by that much.** This is
        not a rounding bug - it is a different method, and it is why a whole
        session's worth of correctly-placed clicks did nothing:

            pointer at      asked for      pointer ended at
            (688, 1079)     (214, 874)     (902, 1079)     688 + 214
            (902, 1079)     (600, 400)     (1502, 1079)    902 + 600

        y never moved because it was already clamped at the bottom edge of a
        1080-tall screen, so every "click on a sidebar row" was really a click
        **at the bottom of the screen**, and it reported itself delivered.

        The portal's own introspection is the authority, and it carries both:

            NotifyPointerMotion(in o, in a{sv}, in d dx, in d dy)
            NotifyPointerMotionAbsolute(in o, in a{sv}, in u stream,
                                        in d x, in d y)

        **The absolute method exists and cannot be used to drive.** It takes a
        **`stream`** - an InputCapture session id - and the portal answers
        `Failed: Invalid position (0)` for a literal 0. It is for a client
        that has opened an input capture, not for one that wants a click.

        So the fix is to send the **difference** from where the pointer
        already is, which is the whole of what the relative method is.
        Measured, four targets, every one exact: (214,874) (600,400)
        (1200,300) (214,874).
        """
        here = pointer_position()
        if here is None:
            # PortalError, not ui_driver's GuiUnavailable: ui_driver imports
            # this module, so importing back would be circular - and the
            # driver wraps this anyway.
            raise PortalError(
                "the portal can only move the pointer relatively, and the "
                "pointer's present position cannot be read, so there is no "
                "way to say where it should go. Refusing rather than nudging "
                "it by the coordinates as if they were a delta.")
        self.client.call(RD_IFACE, "NotifyPointerMotion", "(oa{sv}dd)",
                        (self.session, {}, float(x - here[0]),
                         float(y - here[1])))

    def scroll(self, dx: float = 0.0, dy: float = 0.0) -> None:
        """Wheel. `dy` is clicks, positive **down**, matching `NotchClass`.

        **`NotifyPointerScroll` is not a method this portal has.** Introspecting
        `org.freedesktop.portal.RemoteDesktop` lists `NotifyPointerAxis` and
        `NotifyPointerAxisDiscrete`, and a grep for the name the code was using
        returns **0** - so every scroll through this route was a D-Bus call to a
        method that does not exist, on a path whose whole job is to make the
        failure visible.

        `NotifyPointerAxis` takes the same `(oa{sv}dd)` and its `dx`/`dy` are
        relative scroll amounts, which is what a wheel notch is.
        """
        self.client.call(RD_IFACE, "NotifyPointerAxis", "(oa{sv}dd)",
                         (self.session, {}, float(dx), float(dy)))


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
