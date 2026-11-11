"""The JioFiber landline, as Chronoa reaches it: a client of shani-link.

The line itself - provisioning, SIP, the call's audio and video - is shani-link's
(dev.shani.Link on the session bus, /dev/shani/Link/Landline): device plumbing
that rings with every window closed, on any desktop, with one owner. This module
is the thin client the assistant's own surfaces use:

    is_set_up(), ready(), status(), dial_user(number), dial(number),
    answer(), hangup(), resolve_name(name), listen(cb),
    start(), stop()

**Only what Chronoa reaches is here.** The Phone page - the dialer, the keypad,
the mute button, the contact cache, the video picture - moved to shani-link's app
on 2026-11-04, and that app has its own complete client
(`shani_link/app/line.py`) with every one of those calls on it. A second copy of
them here would be an unreferenced surface: seventeen wrappers nothing in this
tree called, which the unwired-helper audit reported by name.

So the split is **by consumer**, not by capability: the same D-Bus service, two
clients, each holding what its own tree uses. A call the assistant places is
answered from here; a call the person places from the app's dialer is answered
from there.

A failure the service reports comes back as the RuntimeError the facade used to
raise, with its sentence. `landline-enabled` is still Chronoa's own consent:
whether the *assistant* may use the line (capabilities.py); the line runs
because its owner set it up.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)

CONSENT_KEY = "landline-enabled"
BUS_NAME = "dev.shani.Link"
PATH = "/dev/shani/Link/Landline"
IFACE = "dev.shani.Link.Landline"


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


def resolve_name(name: str) -> str:
    return _quiet("ResolveName", "", [name], "s")


# --- what happens on the line --------------------------------------------------

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
