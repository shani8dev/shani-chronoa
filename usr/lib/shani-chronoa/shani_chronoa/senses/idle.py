"""How long since anyone touched this machine.

A voice assistant needs to know whether anyone is listening. Speaking a wake-up
confirmation to an empty room is worse than staying quiet, and "how long has
been nobody here" is the question that decides it.

There is no `xprintidle` on this machine and no Wayland equivalent to copy, but
the X Screen Saver extension is present and answers: it reports the time since
the last input event on the root window, and it tracks wall time exactly
(measured 3000 ms of idle for every 3000 ms slept). So the reading is real and
live rather than a constant this module made up.

## Unavailable is not zero, and this is the sharpest version of that

`idle == 0` means **someone is actively using the machine right now.** If the
extension cannot be read - no X display, no XScreenSaver, no library - and this
sense answered `0`, the assistant would conclude a person is present and
interrupt them. The failure lands on the user at the worst possible moment, and
it looks like a perfectly ordinary number rather than a fault.

So an unreadable clock returns nothing, names the reason, and says which
alternatives exist. There is no way to reach `0` by accident.

## X11 only, and said so

XScreenSaver is an X extension. There is no Wayland equivalent, and this
codebase has no verified portable interface for one. Under a Wayland session
this sense reports that it cannot read the clock rather than guessing, and the
`display` sense remains the one that works there.

## Presence is personal

Idle time reveals the shape of someone's day - when they arrive, when they
leave, when they stop for lunch. That is personal, so this is
`SENSITIVITY_PERSONAL` and the key defaults to off.

Polled, unlike `accessibility`: a presence flag is cheap, bounded, and is the
whole point of a background sense. It carries no content, only an elapsed time.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense

logger = logging.getLogger(__name__)

KIND = "machine-state"

#: Idle time describes when a person is present and when they are not, which
#: draws the shape of their day.
SENSITIVITY = SENSITIVITY_PERSONAL

_TTL_SECONDS = 30.0
_POLL_INTERVAL = 30.0


class _Unavailable(Exception):
    """The idle clock could not be read - unknown, never zero."""


class _ScreenSaverInfo(ctypes.Structure):
    """The XScreenSaverInfo layout from `scrnsaver.h`.

    Laid out with ctypes rather than pulled in through GDK because GDK 4 does
    not expose the Screen Saver extension at all, and this is the only way to
    reach it without binding a new library dependency.
    """

    _fields_ = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("til_or_since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("event_mask", ctypes.c_ulong),
    ]


def _libraries():
    """The X11 and XScreenSaver libraries, or raise explaining what is missing."""
    x11_path = ctypes.util.find_library("X11")
    if not x11_path:
        raise _Unavailable("libX11 is not present on this system")
    xss_path = ctypes.util.find_library("Xss")
    if not xss_path:
        raise _Unavailable(
            "the XScreenSaver extension library (libXss) is not installed, so "
            "the idle clock cannot be read"
        )
    x11 = ctypes.CDLL(x11_path)
    xss = ctypes.CDLL(xss_path)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_ScreenSaverInfo)
    xss.XScreenSaverAllocInfo.argtypes = []
    xss.XScreenSaverQueryInfo.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_ScreenSaverInfo),
    ]
    # These release the two resources `read_idle_seconds` acquires below, and
    # nothing else in the codebase calls either.
    x11.XCloseDisplay.restype = ctypes.c_int
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XFree.restype = ctypes.c_int
    x11.XFree.argtypes = [ctypes.c_void_p]
    return x11, xss


def read_idle_seconds() -> float:
    """Seconds since the last input event, or raise `_Unavailable`.

    This function has exactly two outcomes: a real number, or an exception.
    There is deliberately no path that returns 0 because the clock could not
    be read - see the module docstring for why that particular wrong answer is
    the dangerous one.
    """
    if not os.environ.get("DISPLAY"):
        raise _Unavailable(
            "no X display is available, so there is no idle clock to read"
        )
    x11 = xss = display = info = None
    try:
        x11, xss = _libraries()
        display = x11.XOpenDisplay(None)
        if not display:
            raise _Unavailable("the X display could not be opened")
        root = x11.XDefaultRootWindow(display)
        info = xss.XScreenSaverAllocInfo()
        if not xss.XScreenSaverQueryInfo(display, root, info):
            raise _Unavailable(
                "the X server does not offer the Screen Saver extension"
            )
        return info.contents.idle / 1000.0
    except _Unavailable:
        raise
    except OSError as exc:
        # A missing library surfaces here rather than from find_library on some
        # systems. Same meaning, so it is translated rather than allowed to
        # escape as something the caller has never heard of.
        raise _Unavailable(f"the idle clock could not be opened ({exc})") from exc
    finally:
        if xss is not None and info:
            x11.XFree(info)
        if x11 is not None and display:
            x11.XCloseDisplay(display)


def _describe(seconds: float) -> str:
    """A duration a person can act on, rather than a number to convert."""
    if seconds < 5:
        return "actively in use - input in the last few seconds"
    if seconds < 60:
        return f"idle for {int(seconds)} seconds - still at the machine"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"idle for {minutes} minute(s) - at the machine, but not working"
    hours = minutes // 60
    return f"idle for {hours} hour(s) and {minutes % 60} minute(s) - probably away"


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "idle",
        "description": (
            "Report how long since anyone last used this machine, read from the "
            "X Screen Saver extension. Useful for deciding whether to speak up: "
            "announcing something to an empty room is worse than staying quiet. "
            "X11 only - under Wayland it reports that it cannot read the clock "
            "rather than guessing. Requires the 'idle-sense-enabled' consent key."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed("idle"):
        return (
            f"Not reading the idle clock: {config.sense_allowed_reason('idle')}. "
            f"It reveals when you are at the machine and when you are not, which "
            f"is personal, so it is off until you turn it on."
        )

    try:
        seconds = read_idle_seconds()
    except _Unavailable as exc:
        return (
            f"Not reading the idle clock: {exc}. That means the idle time is "
            f"unknown - not that someone is at the machine, which is a very "
            f"different thing and the one worth not getting wrong."
        )

    return f"This machine is {_describe(seconds)} (idle {seconds:.0f}s)."


_SENSE = Sense(
    name="idle",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
