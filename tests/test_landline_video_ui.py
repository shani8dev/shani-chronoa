"""The Phone page's landline picture: that a decoded frame reaches the screen.

**A widget built is not a widget that draws.** The panel's video area is a
`Gtk.Picture` fed a `Gdk.MemoryTexture` off the decoder's own worker thread, and
every link in that chain can be wired and still show nothing: a sink nobody
subscribed, a frame marshalled onto the wrong loop, bytes wrapped in a format
that does not match them, a box left hidden. Building the panel proves none of
it, so each row below drives a real frame through the real path and reads back
what the picture actually holds.

What is deliberately **not** here: any pixel. No frame is rendered to a PNG and
read, because the frames are generated rather than captured - the decoder's own
pixels are measured by `tests/test_landline_video.py`'s loopback. This file is
about the hand-off.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import landline  # noqa: E402
from shani_chronoa.gui.surfaces import phone  # noqa: E402


class _Config:
    def get(self, key, default=""):
        return str(default)

    def get_bool(self, key, default=False):
        return False

    def get_double(self, key, default=0.0):
        return float(default)

    def set(self, key, value):
        pass


class _App:
    def __init__(self):
        self.config = _Config()
        self.window = None
        self.tts = None
        self.stt = None

    def activate_action(self, name, arg):
        pass


WIDTH, HEIGHT = 4, 2


def _frame(colour: int) -> bytes:
    """A `WIDTH`x`HEIGHT` rgb24 frame, flat, of one colour."""
    return bytes([colour, (colour * 7) % 256, (colour * 13) % 256]) * (WIDTH * HEIGHT)


def _panel():
    panel = phone.PhonePanel(_App())
    panel._video_surface()          # what `calls_tab` would put on screen
    return panel


def _drain(rounds: int = 50):
    ctx = GLib.MainContext.default()
    for _ in range(rounds):
        if not ctx.pending():
            break
        ctx.iteration(False)


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


# --- the hand-off ---------------------------------------------------------------

def test_a_decoded_frame_reaches_the_picture():
    """The whole chain, driven: `_on_frame` (the decoder's thread) marshals onto
    the GTK loop, `_paint_frame` wraps the bytes, and the picture holds them."""
    panel = _panel()
    first = panel.video_picture.get_paintable()
    panel._on_frame(_frame(3), WIDTH, HEIGHT)
    _drain()
    paintable = panel.video_picture.get_paintable()
    assert paintable is not None, "the frame never became a texture"
    assert paintable is not first, "the frame never reached the picture"
    assert panel.video_box.get_visible(), "the area was left hidden with a frame up"


def test_a_frame_that_is_not_a_whole_picture_is_dropped():
    """A short read is not a frame, and wrapping it would hand GTK a texture that
    reads past the end of its own bytes.

    **The over-long case is the one that bites.** GLib refuses a buffer shorter
    than the stride it is handed (measured: the constructor asserts and returns
    NULL), so a *short* frame is dropped by GTK whether this guard exists or
    not. It says nothing about a longer one - and an over-long buffer drawn as a
    whole frame is a stretched frame of somebody else's pixels.
    """
    panel = _panel()
    panel._on_frame(_frame(3), WIDTH, HEIGHT)
    _drain()
    good = panel.video_picture.get_paintable()
    panel._on_frame(b"\x01\x02\x03", WIDTH, HEIGHT)      # 3 bytes for a 24-byte frame
    panel._on_frame(_frame(3) + b"\xff" * 6, WIDTH, HEIGHT)   # 30 for the same 24
    panel._on_frame(b"", WIDTH, HEIGHT)                   # a frame with nothing in it
    _drain()
    assert panel.video_picture.get_paintable() is good, (
        "something that is not exactly one frame replaced a good one")
    assert panel.video_box.get_visible(), "a dropped frame hid the picture"


def test_a_second_frame_replaces_the_first():
    """Identity, not equality: two flat textures of different colours are both
    textures, and a count cannot tell a replaced one from a stuck one."""
    panel = _panel()
    panel._on_frame(_frame(3), WIDTH, HEIGHT)
    _drain()
    first = panel.video_picture.get_paintable()
    panel._on_frame(_frame(9), WIDTH, HEIGHT)
    _drain()
    assert panel.video_picture.get_paintable() is not first, (
        "the second frame did not replace the first")


def test_the_box_starts_hidden_and_the_wide_one_holds_the_ratio():
    """A grey rectangle that is always on screen reads as a broken camera rather
    than as a call with no video in it, so the area starts out of sight."""
    panel = _panel()
    assert not panel.video_box.get_visible(), "the picture is on screen before any call"
    held = [w for w in _walk(panel.video_box)
            if isinstance(w, Gtk.AspectFrame)]
    assert held, "the picture has no aspect frame, so another shape would stretch"
    assert held[0].get_ratio() == pytest.approx(16.0 / 9.0)


def test_wiring_the_panel_subscribes_its_screen_to_the_line(monkeypatch):
    """Driven through the facade's own subscription, not the panel's handler.

    **A handler nobody subscribed is a handler nothing calls**, and every row
    above would pass with the wire unplugged if it drove `_on_frame` directly -
    which is the "a test that reaches past the call site to the function behind
    it" shape, three files over from where it was last fixed.
    """
    taken = {}
    monkeypatch.setattr(landline, "listen", lambda cb: (lambda: None))
    monkeypatch.setattr(landline, "video_sink",
                        lambda sink: taken.setdefault("sink", sink) or (lambda: None))
    panel = _panel()
    panel._wire_landline_events()
    assert "sink" in taken, "the panel wired its events and never its screen"
    taken["sink"](_frame(3), WIDTH, HEIGHT)
    _drain()
    assert panel.video_picture.get_paintable() is not None, (
        "the sink the panel handed over does not paint")


# --- why there is no picture ----------------------------------------------------

@pytest.mark.parametrize("camera,expected", [
    (True, "The other side answered this call without video."),
    (False, "this computer has no camera"),
])
def test_a_call_with_no_picture_says_which_reason_it_is(monkeypatch, camera, expected):
    """A hidden box and an empty one say the same thing to somebody who does not
    know which to expect, and the two reasons have different remedies."""
    monkeypatch.setattr(landline, "video_available", lambda: camera)
    panel = _panel()
    panel._show_video_state()
    assert panel.video_box.get_visible(), "the empty area stayed hidden"
    assert expected in panel.video_placeholder.get_text(), (
        f"the row said {panel.video_placeholder.get_text()!r}, not the reason")


def test_a_picture_that_arrived_says_nothing_more():
    """Once the frames are coming, the explanation is noise on top of them."""
    panel = _panel()
    panel._on_frame(_frame(3), WIDTH, HEIGHT)
    _drain()
    panel._show_video_state()
    assert panel.video_placeholder.get_text() == "", (
        "an explanation was written over a picture that was already arriving")


def test_the_picture_hides_when_the_call_ends():
    panel = _panel()
    panel._on_frame(_frame(3), WIDTH, HEIGHT)
    _drain()
    assert panel.video_box.get_visible()
    panel._hide_video()
    assert not panel.video_box.get_visible(), "the last outlived the call"
    assert panel.video_placeholder.get_visible(), "the explanation did not come back"
