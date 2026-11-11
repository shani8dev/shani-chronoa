"""The X binding is tested against a real X server, or not at all.

A binding that is only exercised through a fake can pass with every signature
wrong, which is exactly how this module got written: three of its signatures were
wrong, and each one found by running rather than by reading.

Those three, so nobody re-derives them:

- **`_NET_CLIENT_LIST` is format 32, and Xlib hands format-32 data back as an
  array of C `long`s - 8 bytes an entry here, not 4.** `nitems` counts
  elements, so reading `nitems * 4` reads **half the buffer**: measured
  2026-11-07, `xprop` listed 25 windows and `window_ids()` returned 25 values
  alternating real ids and zeros (this file failed on the first zero).
- **`XQueryTree`'s children return is a `Window **`**, so the local is a
  `POINTER(c_ulong)` passed *directly*. Passing `byref()` of it raises
  `expected LP_c_ulong instance instead of pointer to LP_c_ulong`, and iterating
  a plain `c_ulong` raises `not subscriptable`.
- **`XGetGeometry`'s width and height are `unsigned int *`**, so they raise
  `expected LP_c_uint instead of pointer to c_int` if declared as ints.
"""

import os
import struct

import pytest

from shani_chronoa import x11


def _display():
    """A real display, or skip the test. Never a stub."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("no display to talk to")
    try:
        return x11.Display()
    except x11.XError as exc:
        pytest.skip("no X server here: %s" % exc)


def test_the_binding_opens_a_real_display_and_reads_its_size():
    """The module cannot be exercised at all without this, and every other test
    in the file depends on it."""
    with _display() as d:
        width, height = d.size()
        assert width > 0 and height > 0, (width, height)


def test_the_window_list_is_read_at_the_servers_own_format():
    """**The empty-list bug.** A format-32 `WINDOW` array unpacked as 8-byte
    entries yields zero windows, which is indistinguishable from a desktop with
    no applications on it - the exact silent-wrong-answer shape this repo keeps
    recording."""
    with _display() as d:
        for wid in d.window_ids():
            assert isinstance(wid, int) and wid > 0, wid
        # and the ids really are what the raw property holds
        _kind, fmt, raw = d._property(d.root(), "_NET_CLIENT_LIST", "WINDOW")
        if raw:
            width = x11._item_width(fmt)
            code = {1: "B", 2: "H", 4: "L", 8: "Q"}[width]
            expected = list(struct.unpack_from(
                "<%d%s" % (len(raw) // width, code), raw))
            assert d.window_ids() == expected


def test_a_window_carries_a_real_class_not_an_empty_string():
    """`xdotool getwindowclassname` **does not exist** in this build and answers
    empty with rc=1, so an earlier version decided every window was its own
    client. Reading `WM_CLASS` is the only version whose failure is visible."""
    with _display() as d:
        wid = d.find("slack") or (d.window_ids() or [None])[0]
        if wid is None:
            pytest.skip("no window on the screen to read")
        inst, klass = d.app_class(wid)
        assert inst or klass, (
            "no WM_CLASS on window %d; a binding that always answers '' is the "
            "same defect as xdotool's missing verb" % wid)


def _make_window(d, x=137, y=241, width=320, height=240):
    """A real X window at a known position, and the function to destroy it.

    **A window is arranged, not found.** `window_ids()` reads
    `_NET_CLIENT_LIST`, which a **window manager** publishes - and a bare
    `Xvfb` has no window manager and no applications, so the list is empty
    and every test that wanted a window had nothing to measure. Measured on
    a CI runner (2026-11-11): the geometry test reported *"no window was
    on the 1280x800 screen, so this checked nothing: []"*, which is the
    honest wording of a check that could not run.

    So the window is made here, through the same `libX11` the module
    already loads: `XCreateSimpleWindow` + `XMapWindow`, then
    `XSync` so the server has actually mapped it before anything reads
    its geometry.

    **It is a child of another window, and that is the whole point.**
    `XGetGeometry` reports a window's coordinates relative to its
    **parent**, and a client window's parent is the decoration frame - so
    the raw numbers and the root-relative numbers differ by the frame's
    offset. A window parented straight to the root has both numbers equal,
    and a translation that did nothing would look right. So the window is
    created inside a parent placed at an offset, and the position checked
    is the one in **root** coordinates.
    """
    import ctypes

    X11 = x11.X11
    c_void_p = ctypes.c_void_p
    X11.XCreateSimpleWindow.restype = ctypes.c_ulong
    X11.XCreateSimpleWindow.argtypes = [
        c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong,
        ctypes.c_ulong]
    X11.XMapWindow.restype = ctypes.c_int
    X11.XMapWindow.argtypes = [c_void_p, ctypes.c_ulong]
    X11.XSync.restype = ctypes.c_int
    X11.XSync.argtypes = [c_void_p, ctypes.c_int]
    X11.XDestroyWindow.restype = ctypes.c_int
    X11.XDestroyWindow.argtypes = [c_void_p, ctypes.c_ulong]

    root = d.root()
    # The parent stands in for a decoration frame: it is placed at an
    # offset, so the child's own coordinates are relative to *it*.
    parent = X11.XCreateSimpleWindow(
        d._d, root, 40, 60, width + 80, height + 60, 0, 0, 0)
    X11.XMapWindow(d._d, parent)
    wid = X11.XCreateSimpleWindow(
        d._d, parent, x, y, width, height, 0, 0, 0)
    X11.XMapWindow(d._d, wid)
    X11.XSync(d._d, 0)
    return parent, wid


def test_geometry_is_translated_into_root_coordinates():
    """A client window's own coordinates are relative to its **parent**, which
    is the decoration frame. Using them as screen coordinates puts every click
    in the wrong place - which is the 37px/74px failure this repo measured.

    **A window is made, not found** - see `_make_window`. The point of the
    translation is that the number comes back in **root** coordinates, so
    the check is that the position asked for is the position read back.
    """
    with _display() as d:
        width, height = d.size()
        x, y, w, h = 137, 241, 320, 240
        parent_x, parent_y = 40, 60
        if x + parent_x >= width or y + parent_y >= height:
            pytest.skip(f"the screen is {width}x{height}, too small for the "
                        f"window this test places")
        parent, wid = _make_window(d, x, y, w, h)
        try:
            geo = d.geometry(wid)
            assert geo is not None, "the window has no geometry at all"
            # **The root position is the child's own position plus its
            # parent's.** The child's own coordinates are relative to the
            # parent, so a reader that did not translate would answer
            # `(x, y)` - and that is exactly the number a click must not
            # be sent to.
            assert geo == (x + parent_x, y + parent_y, w, h), (
                f"the window sits at ({x}, {y}) inside a parent at "
                f"({parent_x}, {parent_y}), so it is at "
                f"({x + parent_x}, {y + parent_y}) on the screen - but it "
                f"read back at {geo[:2]}. The numbers are not in root "
                f"coordinates, so every click would land in the wrong place")
        finally:
            import ctypes
            x11.X11.XDestroyWindow(d._d, wid)
            x11.X11.XDestroyWindow(d._d, parent)
            x11.X11.XSync(d._d, 0)


def test_the_pointer_is_asked_of_the_server():
    """`xdotool getmouselocation` answers `WINDOW=1024` for a point over nothing,
    which is Xwayland's own host window - not a missing window. This asks the
    server directly, and `0` means the root, which is what it means."""
    with _display() as d:
        where = d.pointer()
        assert where is not None, "XQueryPointer answered nothing"
        x, y, window = where
        width, height = d.size()
        assert 0 <= x <= width and 0 <= y <= height, (x, y)


def test_children_hands_the_server_a_pointer_not_a_value():
    """The `Window **` bug: iterating a `c_ulong` raises `not subscriptable` and
    freeing it raises `cannot be interpreted as c_void_p`."""
    with _display() as d:
        ids = d.window_ids()
        if not ids:
            pytest.skip("no window to walk")
        for kid in d.children(ids[0]):
            assert isinstance(kid, int) and kid > 0, kid


def test_find_returns_none_for_a_window_that_is_not_there():
    """A finder that always returns something is worse than one that finds
    nothing - and `find` is what every caller will trust."""
    with _display() as d:
        assert d.find("a window name nobody would choose 9182736") is None


def test_no_display_is_a_refusal_not_an_empty_result():
    """The module's own rule: cannot open a display means refuse, never answer as
    though nothing was found."""
    with pytest.raises(x11.XError):
        x11.Display(":nonexistent-display-42")
    assert x11.Display.__module__ == "shani_chronoa.x11"


def test_xtest_delivery_is_asked_of_the_server_not_assumed():
    """**A route that uses XTEST must check before it claims a motion.**

    Measured here: `move()` returns without error, `XFlush` runs, the XTEST
    extension is advertised by `XQueryExtension` - and the pointer does not move.
    That is the same fact AGENTS.md records from the other side as JDK-8280990,
    "XTEST events never leave the X server", seen here as they not reaching even
    the server's own recorded pointer.

    This test does not assert which way it answers - that is a fact about the
    machine, and a test pinned to one answer fails on the other. It asserts the
    question is *answerable* and answered the same way twice.
    """
    from shani_chronoa import x11

    first = x11.xtest_delivers()
    second = x11.xtest_delivers()
    assert first == second, (
        "the XTEST self-test gave two answers on one machine (%r then %r), so it "
        "is not measuring anything" % (first, second))


def test_the_xtest_self_test_restores_where_the_pointer_was():
    """**The cursor belongs to whoever is sitting at the machine.** A self-test
    that leaves the pointer at the origin has just moved their mouse, which is
    not what a probe does."""
    from shani_chronoa import x11

    try:
        display = x11.Display()
    except x11.XError:
        pytest.skip("no X server here")
    with display:
        before = display.pointer()
        if before is None:
            pytest.skip("the pointer could not be read")
        x11.xtest_delivers(display)
        after = display.pointer()
    assert after[:2] == before[:2], (
        "the self-test moved the pointer from %r to %r and left it there"
        % (before[:2], after[:2]))
