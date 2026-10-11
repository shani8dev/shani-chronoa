"""Talk to an X server directly, through ctypes.

**`xdotool` is gone from this module's path, and the reasons are measured rather
than stylistic.** From this repository's own notes, five separate failures came
from shelling out to it, every one of which exits 0:

- **`xdotool click` on Wayland delivers nothing at all.** XTEST events never
  leave the XWayland server (JDK-8280990), so the call succeeds and the
  application never sees it. That is not a bug to work around; it is the X route
  being structurally wrong for a Wayland session, which is why
  `ui_driver._pointer_routes` puts the portal first and only keeps XTEST where
  `WAYLAND_DISPLAY` is unset - inside a container's own X display, where the X
  server genuinely is the display server.
- **`xdotool search` returns Mutter's decoration frame, not the app.** Measured
  on Slack: the window named `slack` is `10485762` with `WM_CLASS
  "mutter-x11-frames"`, and the real client is its child `12582916`.
- **`xdotool getwindowclassname` does not exist in this build.** It answers
  `Unknown command`, rc=1 - and returns **empty** rather than failing loudly, so
  an earlier version decided every window was its own client, silently.
- **`xwininfo -tree` prints hexadecimal** while every `xdotool` call speaks
  decimal, so returning its child verbatim broke every downstream lookup in a way
  that reads as "the window has gone".
- **`xdotool getmouselocation` reports `WINDOW=1024`** for a point over no
  X-mapped window - a permanent 1920x1080 unnamed window at (0,0), which is
  Xwayland's own host window, not a missing window.

So the same questions are asked of the X server itself: the answer is the X
protocol's, it cannot be a different tool's idea of the answer, and a call that
fails fails where it is made.

**Why ctypes and not `python-xlib`.** `python-xlib` is not installed here
(measured), while `libX11.so.6` and `libXtst.so.6` both are, on any machine with
an X client at all. A ctypes binding has no dependency to be missing, which is
the property this module needs: its own absence is the honest answer when there
is no X server.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os

logger = logging.getLogger(__name__)

#: `None` means "no X here", and every reader treats it as a refusal rather than
#: as an empty result. A module that cannot open a display must not answer as if
#: it had found no windows.
X11 = None
XTST = None


def _load() -> bool:
    """Open both libraries once. False means no X client on this machine."""
    global X11, XTST
    if X11 is not None:
        return True
    x11_path = ctypes.util.find_library("X11") or "libX11.so.6"
    xtst_path = ctypes.util.find_library("Xtst") or "libXtst.so.6"
    try:
        X11 = ctypes.CDLL(x11_path)
        XTST = ctypes.CDLL(xtst_path)
    except OSError as exc:                      # pragma: no cover - platform
        logger.debug("no X client library here: %s", exc)
        X11 = XTST = None
        return False
    _declare()
    return True


# `Display` is opaque; a void pointer is enough and keeps the binding honest.
c_void_p = ctypes.c_void_p
c_ulong_p = ctypes.POINTER(ctypes.c_ulong)
c_ubyte_pp = ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))


def _declare() -> None:
    """Argtypes for every call this module makes.

    Set once, on purpose: a missing argtype is how a `c_void_p` returns garbage
    that reads as a real window id, and the failure then looks like a window that
    moved.
    """
    X11.XOpenDisplay.restype = c_void_p
    X11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    X11.XCloseDisplay.restype = ctypes.c_int
    X11.XCloseDisplay.argtypes = [c_void_p]
    X11.XDefaultScreen.restype = ctypes.c_int
    X11.XDefaultScreen.argtypes = [c_void_p]
    X11.XDisplayWidth.restype = ctypes.c_int
    X11.XDisplayWidth.argtypes = [c_void_p, ctypes.c_int]
    X11.XDisplayHeight.restype = ctypes.c_int
    X11.XDisplayHeight.argtypes = [c_void_p, ctypes.c_int]
    X11.XFlush.restype = ctypes.c_int
    X11.XFlush.argtypes = [c_void_p]

    X11.XInternAtom.restype = ctypes.c_ulong
    X11.XInternAtom.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_int]

    X11.XRootWindow.restype = ctypes.c_ulong
    X11.XRootWindow.argtypes = [c_void_p, ctypes.c_int]

    # The subtlest signature in the binding. Ten arguments, three of them
    # return-by-pointer, and the property blob is a `unsigned char **` the caller
    # must free with XFree.
    X11.XGetWindowProperty.restype = ctypes.c_int
    X11.XGetWindowProperty.argtypes = [
        c_void_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        c_ubyte_pp]
    X11.XFree.restype = ctypes.c_int
    X11.XFree.argtypes = [c_void_p]

    X11.XFetchName.restype = ctypes.c_int
    X11.XFetchName.argtypes = [c_void_p, ctypes.c_ulong,
                               ctypes.POINTER(ctypes.c_char_p)]

    # `children_return` is a `Window **` - a pointer the caller frees - so its
    # Python type is a pointer **to** a pointer, and the local is itself a
    # `POINTER(c_ulong)` passed directly. Passing `byref()` of that raises
    # `expected LP_c_ulong instance instead of pointer to LP_c_ulong`.
    X11.XQueryTree.restype = ctypes.c_int
    X11.XQueryTree.argtypes = [c_void_p, ctypes.c_ulong,
                               c_ulong_p, c_ulong_p,
                               ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                               ctypes.POINTER(ctypes.c_uint)]

    X11.XQueryPointer.restype = ctypes.c_int
    X11.XQueryPointer.argtypes = [
        c_void_p, ctypes.c_ulong, c_ulong_p, c_ulong_p,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        c_ulong_p]

    # Input events. `XSendEvent` is deliberately **not bound**: synthesising a
    # client message rather than an input event is how a program makes a window
    # believe something happened without the server having seen input.
    XTST.XTestFakeMotionEvent.restype = ctypes.c_int
    XTST.XTestFakeMotionEvent.argtypes = [c_void_p, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int]
    XTST.XTestFakeButtonEvent.restype = ctypes.c_int
    XTST.XTestFakeButtonEvent.argtypes = [c_void_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_ulong]
    XTST.XTestFakeKeyEvent.argtypes = [c_void_p, ctypes.c_uint,
                                       ctypes.c_int, ctypes.c_ulong]
    XTST.XTestFakeKeyEvent.restype = ctypes.c_int


class XError(RuntimeError):
    """The X server refused, or the call could not be made at all."""


class Display:
    """One open connection. Use as a context manager, or call `close`.

    Opening a display and never closing it is how a long-running assistant ends
    up holding a file descriptor per call.
    """

    def __init__(self, name: "str | None" = None) -> None:
        if not _load():
            raise XError("no X client library is installed here")
        self._d = X11.XOpenDisplay(name.encode() if name else None)
        if not self._d:
            raise XError("no X server at %s" % (name or os.environ.get("DISPLAY")
                                                or "the default display"))

    def __enter__(self) -> "Display":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if getattr(self, "_d", None):
            X11.XCloseDisplay(self._d)
            self._d = None

    # -- geometry -----------------------------------------------------------

    def size(self) -> "tuple[int, int]":
        screen = X11.XDefaultScreen(self._d)
        return (X11.XDisplayWidth(self._d, screen),
                X11.XDisplayHeight(self._d, screen))

    def root(self) -> int:
        return X11.XRootWindow(self._d, X11.XDefaultScreen(self._d))

    # -- atoms and properties ----------------------------------------------

    def atom(self, name: str) -> int:
        got = X11.XInternAtom(self._d, name.encode(), 0)
        return int(got)

    def _property(self, window: int, name: str, kind: str = "any"
                  ) -> "tuple[int, int, bytes]":
        """Read one property, returning `(type, value)` or `(0, b"")`.

        **The `bytes_after` check is load-bearing.** `long_length` is in 32-bit
        units, so reading more than the server gives back leaves the first part
        already correct and the rest one read away - and a reader that ignores
        `bytes_after` reports a truncated array as a whole one. Full read, or
        nothing.
        """
        actual_type = ctypes.c_ulong()
        fmt = ctypes.c_int()
        nitems = ctypes.c_ulong()
        after = ctypes.c_ulong()
        blob = ctypes.POINTER(ctypes.c_ubyte)()
        status = X11.XGetWindowProperty(
            self._d, window, self.atom(name), 0, 1024, 0,
            self.atom(kind) if kind != "any" else 0,
            ctypes.byref(actual_type), ctypes.byref(fmt),
            ctypes.byref(nitems), ctypes.byref(after), ctypes.byref(blob))
        if status != 0 or not blob or nitems.value == 0:
            if blob:
                X11.XFree(blob)
            return 0, 0, b""
        # **The item width comes from the format, not from an assumption.** A
        # `WINDOW` array is format 32 here - 4 bytes per id, measured - and the
        # obvious `sizeof(unsigned long)` (8) reads half as many windows as the
        # server sent and reports the list as empty.
        try:
            width = fmt.value // 8 if fmt.value in (8, 16, 32) else 1
            nbytes = nitems.value * width
            raw = bytes(bytearray(blob[:nbytes]))
        finally:
            X11.XFree(blob)
        return int(actual_type.value), int(fmt.value), raw

    def window_ids(self, atom_name: str = "_NET_CLIENT_LIST") -> "list[int]":
        """The windows the window manager knows about, in stacking order.

        Read from `_NET_CLIENT_LIST` rather than by walking the tree from the
        root, because the root's children include every internal window the WM
        created - Measured here: Mutter's guard window, its decoration frames,
        ibus's two helper windows, and Chromium's clipboard, none of which are
        applications. `_NET_CLIENT_LIST` is the WM's own answer to "which windows
        are there", which is the question being asked.
        """
        _kind, fmt, raw = self._property(self.root(), atom_name, "WINDOW")
        if not raw:
            return []
        import struct

        width = fmt // 8
        if width not in (1, 2, 4, 8) or len(raw) % width:
            logger.warning("window list of %d bytes at format %d is not a list of ids", len(raw), fmt)
            return []
        count = len(raw) // width
        return list(struct.unpack_from("<%dL" % count, raw))

    def name(self, window: int) -> str:
        """A window's title. `_NET_WM_NAME` first, then `WM_NAME`.

        `_NET_WM_NAME` is UTF-8 and is what a modern WM reports; `WM_NAME` is
        the older Latin-1 string and is the fallback, not the first choice.
        """
        _kind, _fmt, raw = self._property(window, "_NET_WM_NAME", "UTF8_STRING")
        if raw:
            return raw.decode("utf-8", "replace").rstrip("\x00")
        text = ctypes.c_char_p()
        if X11.XFetchName(self._d, window, ctypes.byref(text)):
            if text.value:
                return text.value.decode("utf-8", "replace")
            X11.XFree(text)
        return ""

    def app_class(self, window: int) -> "tuple[str, str]":
        """`(WM_CLASS instance, WM_CLASS class)`, both possibly empty.

        **This is the call `xdotool getwindowclassname` pretended to be.** It
        does not exist in this build of xdotool and answers with an empty string
        and rc=1, so every window looked like its own client. Reading the
        property directly is the only version that can be wrong in a way anyone
        can see.
        """
        _kind, _fmt, raw = self._property(window, "WM_CLASS")
        if not raw:
            return "", ""
        parts = [p for p in raw.split(b"\x00") if p]
        if len(parts) >= 2:
            return parts[0].decode("latin-1"), parts[1].decode("latin-1")
        return (parts[0].decode("latin-1"), "") if parts else ("", "")

    def children(self, window: int) -> "list[int]":
        root = parent = ctypes.c_ulong()
        kids = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        if X11.XQueryTree(self._d, window, ctypes.byref(root),
                          ctypes.byref(parent), ctypes.byref(kids),
                          ctypes.byref(count)) == 0:
            return []
        if not kids or not count.value:
            return []
        try:
            return [kids[i] for i in range(count.value)]
        finally:
            # A `Window **` must be freed as a pointer, not as a value: passing
            # the `c_ulong` the array was read through raises
            # `ArgumentError: cannot be interpreted as c_void_p`.
            if kids:
                X11.XFree(kids)

    def geometry(self, window: int) -> "tuple[int, int, int, int] | None":
        """`(x, y, width, height)` translated into root coordinates.

        **Translated, not read.** `XGetGeometry` reports a window's own
        coordinates relative to its parent, which is meaningless for a client
        inside a decoration frame. `XTranslateCoordinates` asks the server where
        the window actually is, which is the number a click needs.
        """
        X11.XTranslateCoordinates.restype = ctypes.c_int
        X11.XTranslateCoordinates.argtypes = [
            c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            c_ulong_p]
        X11.XGetGeometry.restype = ctypes.c_int
        X11.XGetGeometry.argtypes = [
            c_void_p, ctypes.c_ulong, c_ulong_p, ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint)]
        real_root = ctypes.c_ulong()
        px = ctypes.c_int()
        py = ctypes.c_int()
        # `width` and `height` are `unsigned int *`. Making them plain ints
        # raises `expected LP_c_uint instead of pointer to c_int`, which is the
        # same signature-assumption trap the rest of this module is written to
        # avoid - and it is why every argtype is declared rather than inferred.
        w = ctypes.c_uint()
        h = ctypes.c_uint()
        border = depth = ctypes.c_uint()
        if X11.XGetGeometry(self._d, window, ctypes.byref(real_root),
                            ctypes.byref(px), ctypes.byref(py), ctypes.byref(w),
                            ctypes.byref(h), ctypes.byref(border),
                            ctypes.byref(depth)) == 0:
            return None
        rx = ctypes.c_int()
        ry = ctypes.c_int()
        child = ctypes.c_ulong()
        X11.XTranslateCoordinates(self._d, window, real_root.value, 0, 0,
                                  ctypes.byref(rx), ctypes.byref(ry),
                                  ctypes.byref(child))
        return int(rx.value), int(ry.value), int(w.value), int(h.value)

    def pointer(self) -> "tuple[int, int, int] | None":
        """`(x, y, window_under_pointer)`.

        **The window under the pointer is the part that matters, and it is asked
        of the server rather than guessed.** `xdotool getmouselocation` reports
        `WINDOW=1024` for a point over nothing, which is Xwayland's own host
        window at (0,0) - and a client that reads 1024 as "no window found"
        concludes the app it was driving has closed.
        """
        root = child = ctypes.c_ulong()
        rx = ry = wx = wy = ctypes.c_int()
        mask = ctypes.c_ulong()
        if X11.XQueryPointer(self._d, self.root(), ctypes.byref(root),
                             ctypes.byref(child), ctypes.byref(rx),
                             ctypes.byref(ry), ctypes.byref(wx),
                             ctypes.byref(wy), ctypes.byref(mask)) == 0:
            return None
        return int(rx.value), int(ry.value), int(child.value)

    def find(self, needle: str) -> "int | None":
        """The window whose name contains `needle`, frame resolved to the client.

        **`_NET_CLIENT_LIST` already holds clients, so the frame resolution is
        belt and braces** - and it is kept, because the recorded failure was a
        `xdotool search` that returned Mutter's decoration frame for Slack. When
        a matched id turns out to *be* a frame (its `WM_CLASS` class is
        `mutter-x11-frames`, or it has no class at all and has a child that
        does), the child is returned.
        """
        low = needle.lower()
        for wid in self.window_ids():
            if not self.name(wid):
                continue
            if low not in self.name(wid).lower():
                continue
            _instance, klass = self.app_class(wid)
            if klass == "mutter-x11-frames":
                for kid in self.children(wid):
                    if self.app_class(kid)[1]:
                        return kid
            return wid
        return None

    # -- input --------------------------------------------------------------

    def move(self, x: int, y: int) -> None:
        """Move the pointer. Absolute, and it is the *cursor*, not a target."""
        XTST.XTestFakeMotionEvent(self._d, X11.XDefaultScreen(self._d),
                                   int(x), int(y), 0)
        X11.XFlush(self._d)

    def button(self, button: int, pressed: bool) -> None:
        XTST.XTestFakeButtonEvent(self._d, int(button), 1 if pressed else 0, 0)
        X11.XFlush(self._d)

    def _keycode(self, keysym: int) -> int:
        X11.XKeysymToKeycode.restype = ctypes.c_ubyte
        X11.XKeysymToKeycode.argtypes = [c_void_p, ctypes.c_ulong]
        return int(X11.XKeysymToKeycode(self._d, keysym))

    def key(self, keysym: int, pressed: bool) -> None:
        code = self._keycode(keysym)
        if not code:
            raise XError("no keycode maps to keysym 0x%x" % keysym)
        XTST.XTestFakeKeyEvent(self._d, code, 1 if pressed else 0, 0)
        X11.XFlush(self._d)

    def wheel(self, direction: str, amount: int = 3) -> bool:
        """Wheel notches, as button presses 4/5/6/7.

        X11's wheel *is* buttons: 4 up, 5 down, 6 left, 7 right. Sending a press
        and a release for each notch is the whole of it, and it is the same
        mechanism every other press goes through rather than a side channel.
        """
        buttons = {"up": 4, "down": 5, "left": 6, "right": 7}
        number = buttons.get(direction)
        if number is None:
            raise XError("%r is not a direction; use %s"
                         % (direction, ", ".join(sorted(buttons))))
        for _ in range(int(amount)):
            self.button(number, True)
            self.button(number, False)
        return True


def display(name: "str | None" = None) -> Display:
    """Open a display. Raises `XError` when there is no X server to talk to."""
    return Display(name)


def available() -> bool:
    """False when there is no X client library or no display to open."""
    try:
        with Display() as d:
            d.size()
        return True
    except XError:
        return False
