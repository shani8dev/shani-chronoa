"""The X binding is tested against a real X server, or not at all.

A binding that is only exercised through a fake can pass with every signature
wrong, which is exactly how this module got written: three of its signatures were
wrong, and each one found by running rather than by reading.

Those three, so nobody re-derives them:

- **`_NET_CLIENT_LIST` is format 32 here**, so a window id is **4 bytes**, not
  the 8 that `sizeof(unsigned long)` suggests. Reading it as 8 reads half as many
  windows and reports an **empty list** - measured, on a server that had one
  window in it.
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
            expected = list(struct.unpack_from("<%dL" % (len(raw) // (fmt // 8)), raw))
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


def test_geometry_is_translated_into_root_coordinates():
    """A client window's own coordinates are relative to its **parent**, which
    is the decoration frame. Using them as screen coordinates puts every click
    in the wrong place - which is the 37px/74px failure this repo measured."""
    with _display() as d:
        for wid in d.window_ids():
            geo = d.geometry(wid)
            if geo is None:
                continue
            x, y, w, h = geo
            assert w > 0 and h > 0, (x, y, w, h)
            width, height = d.size()
            assert 0 <= x <= width and 0 <= y <= height, (
                "window %d at (%d,%d) is outside a %dx%d screen" % (wid, x, y, width, height))


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
