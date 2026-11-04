"""The JioFiber landline, as Chronoa reaches it: a client of shani-link.

The line itself - provisioning, SIP, the call's audio and video - is shani-link's
(dev.shani.Link on the session bus, /dev/shani/Link/Landline): device plumbing
that rings with every window closed, on any desktop, with one owner. This module
keeps the names Chronoa's Phone page, the `landline_call` skill and the mascot
always used, so none of them changed when the engine moved:

    dial(number), answer(), hangup(), dtmf(digit), status(), listen(cb), ...

A failure the service reports comes back as the RuntimeError (or
ProvisionError for setting up) the facade used to raise, with its sentence.
`landline-enabled` is still Chronoa's own consent: whether the *assistant* may
use the line (capabilities.py); the line runs because its owner set it up.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
from typing import Callable

logger = logging.getLogger(__name__)

CONSENT_KEY = "landline-enabled"
BUS_NAME = "dev.shani.Link"
PATH = "/dev/shani/Link/Landline"
IFACE = "dev.shani.Link.Landline"


class ProvisionError(RuntimeError):
    """Setting the line up failed (the router or the code said no)."""


def _bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _call(method: str, args=None, sig: str = "", timeout_ms: int = 5000, error=RuntimeError):
    """One call to the line; the service's own sentence on failure."""
    from gi.repository import Gio, GLib
    params = GLib.Variant(f"({sig})", tuple(args)) if sig else None
    try:
        reply = _bus().call_sync(BUS_NAME, PATH, IFACE, method, params, None,
                                 Gio.DBusCallFlags.NONE, timeout_ms, None)
    except GLib.Error as exc:
        msg = exc.message.split(": ", 1)[-1] if "dev.shani.Link.Error" in exc.message else \
            "The phone line service (shani-link) is not running."
        raise error(msg) from None
    out = reply.unpack() if reply is not None else ()
    return out[0] if out else None


def _quiet(method: str, default, *a, **kw):
    try:
        return _call(method, *a, **kw)
    except RuntimeError:
        return default


# --- setting up, once ----------------------------------------------------------

def is_set_up() -> bool:
    return bool(_quiet("IsSetUp", False))


def setup_start(host: str = "") -> str:
    return _call("SetupStart", [host], "s", timeout_ms=60000, error=ProvisionError)


def setup_finish(code: str) -> str:
    return _call("SetupFinish", [code], "s", timeout_ms=60000, error=ProvisionError)


def forget() -> None:
    _call("Forget")


def enabled() -> bool:
    return bool(_quiet("Enabled", False))


def set_enabled(on: bool) -> None:
    _call("SetEnabled", [bool(on)], "b")


# --- the line's life: shani-link's, not Chronoa's ------------------------------

def start(app=None, line_factory=None) -> bool:
    """The service keeps the line registered; asking it anything starts it (D-Bus
    activation). Kept so callers from before the move need no change."""
    return ready() or is_set_up()


def stop() -> None:
    """Chronoa closing no longer hangs the line up: it belongs to shani-link."""


def ready() -> bool:
    return bool(_quiet("Ready", False))


def status() -> str:
    return _quiet("Status", "The phone line service (shani-link) is not running.")


# --- calls ---------------------------------------------------------------------

def dial_user(number: str) -> str:
    """The user part to dial: Indian numbers in +91 form, as the line expects."""
    digits = "".join(ch for ch in number if ch.isdigit() or ch in "+*#")
    if len(digits) == 11 and digits.startswith("0"):
        return "+91" + digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return digits


def dial(number: str) -> str:
    return _call("Dial", [number], "s")


def answer() -> None:
    _call("Answer")


def hangup() -> None:
    _call("Hangup")


def dtmf(digit: str) -> bool:
    return bool(_call("Dtmf", [digit], "s"))


def set_muted(on: bool) -> bool:
    return bool(_quiet("SetMuted", False, [bool(on)], "b"))


def muted() -> bool:
    return bool(_quiet("Muted", False))


def in_call() -> bool:
    return bool(_quiet("InCall", False))


def hold() -> bool:
    return bool(_call("Hold"))


def swap() -> bool:
    return bool(_call("Swap"))


def merge() -> bool:
    return bool(_call("Merge"))


def calls() -> list:
    return json.loads(_quiet("Calls", "[]") or "[]")


def history() -> "list[dict]":
    return json.loads(_quiet("History", "[]") or "[]")


def resolve_name(name: str) -> str:
    return _quiet("ResolveName", "", [name], "s")


def cache_contacts(book: list) -> None:
    _quiet("CacheContacts", None, [json.dumps(book, default=str)], "s")


def video_available() -> bool:
    return bool(_quiet("VideoAvailable", False))


# --- what happens on the line, and its picture ---------------------------------

def listen(callback: Callable[..., None]) -> Callable[[], None]:
    """`callback(kind, **info)` for every event on the line (the service's
    Event signal). Returns the unsubscribe."""
    from gi.repository import Gio
    bus = _bus()

    def on_signal(_conn, _sender, _path, _iface, _signal, params):
        kind, info = params.unpack()
        try:
            callback(kind, **json.loads(info or "{}"))
        except Exception:  # noqa: BLE001
            logger.debug("landline listener failed", exc_info=True)
    sid = bus.signal_subscribe(BUS_NAME, IFACE, "Event", PATH, None, Gio.DBusSignalFlags.NONE, on_signal)
    return lambda: bus.signal_unsubscribe(sid)


def video_sink(sink: Callable[[bytes, int, int], None]) -> Callable[[], None]:
    """`sink(rgb24, width, height)` for every picture the line decodes, read
    from shani-link's video socket on a thread of its own (the same rule as
    before: anything GTK-shaped marshals onto the main loop itself)."""
    stop = threading.Event()

    def read_all(conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n and not stop.is_set():
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def run():
        try:
            path = _call("VideoSocket")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.connect(path)
                while not stop.is_set():
                    w, h, n = struct.unpack(">III", read_all(conn, 12))
                    sink(read_all(conn, n), w, h)
        except (OSError, ConnectionError, RuntimeError, struct.error):
            logger.debug("landline: no picture from shani-link", exc_info=True)
    threading.Thread(target=run, daemon=True, name="landline-video").start()
    return stop.set
