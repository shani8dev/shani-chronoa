"""The cue layer: what it draws, where it draws it, and the protocol.

Everything here runs headless, because every part of it can be measured
without a display - the cue is rendered through the real `Gtk.Snapshot` into a
real `Gsk` texture and the pixels are read back out of the PNG it writes. That
matters: a cue drawn in the wrong place is worse than no cue, because a person
watching believes it, and the only way to know where it landed is to look.

Two of these tests guard defects this build produced rather than hypothetical
ones, and both were found by running it:
- a ring drawn by a caller whose clock read early faded in from a *negative*
  alpha, which draws nothing - the clamp in `draw_cues`;
- `Gsk.FillNode` and `Gsk.BorderNode` segfault when constructed directly, and
  `cairo.Context`/`cairo.Region` cannot be marshalled at all, so the drawing
  uses only the snapshot calls that are measured to work.
"""

import json
import socket
import threading
import time

import pytest

from shani_chronoa import automation_overlay as ao
from shani_chronoa import ui_driver


# --- rendering the real thing --------------------------------------------------


@pytest.fixture(scope="module")
def render():
    """A function that turns cues into pixels, or skips if GTK cannot render."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gsk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Graphene", "1.0")
        from gi.repository import Gsk, Gtk
    except (ImportError, ValueError) as exc:  # pragma: no cover - GTK-less box
        pytest.skip(f"GTK4 is not usable here: {exc}")

    def _render(cues: dict, now: float, width: int = 400, height: int = 300):
        import struct
        import zlib

        snapshot = Gtk.Snapshot()
        ao.draw_cues(snapshot, cues, now, layout=None)
        node = snapshot.to_node()
        renderer = Gsk.CairoRenderer.new()
        try:
            renderer.realize(None)
            from gi.repository import Graphene

            viewport = Graphene.Rect.alloc()
            viewport.init(0, 0, width, height)
            texture = renderer.render_texture(node, viewport)
            texture.save_to_png("/tmp/opencode/.chronoa-cue-test.png")
        finally:
            # Not unrealizing aborts the process at exit - measured, and the
            # reason this is in a `finally`.
            renderer.unrealize()

        data = open("/tmp/opencode/.chronoa-cue-test.png", "rb").read()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        position, idat, meta = 8, b"", None
        while position < len(data):
            length, kind = struct.unpack(">I4s", data[position:position + 8])
            chunk = data[position + 8:position + 8 + length]
            if kind == b"IHDR":
                meta = struct.unpack(">IIBBBBB", chunk)
            elif kind == b"IDAT":
                idat += chunk
            position += 12 + length
        width, height, depth, colour = meta[0], meta[1], meta[2], meta[3]
        assert depth == 8, depth
        channels = {2: 3, 6: 4}[colour]
        raw = zlib.decompress(idat)
        stride = width * channels
        rows = bytearray()
        previous = bytearray(stride)
        cursor = 0
        for _row in range(height):
            filter_type = raw[cursor]
            line = bytearray(raw[cursor + 1:cursor + 1 + stride])
            cursor += 1 + stride
            for index in range(stride):
                a = line[index - channels] if index >= channels else 0
                b = previous[index]
                c = previous[index - channels] if index >= channels else 0
                if filter_type == 1:
                    line[index] = (line[index] + a) & 0xFF
                elif filter_type == 2:
                    line[index] = (line[index] + b) & 0xFF
                elif filter_type == 3:
                    line[index] = (line[index] + (a + b) // 2) & 0xFF
                elif filter_type == 4:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    best = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[index] = (line[index] + best) & 0xFF
            rows += line
            previous = line

        def at(x: int, y: int):
            index = (y * width + x) * channels
            red, green, blue = rows[index], rows[index + 1], rows[index + 2]
            alpha = rows[index + 3] if channels == 4 else 255
            return (red, green, blue, alpha)

        def box(x0: int, y0: int, size: int):
            return [at(x, y) for y in range(y0, y0 + size)
                    for x in range(x0, x0 + size)]

        return width, at, box

    return _render


def _is_cue_blue(value, min_alpha: int = 100) -> bool:
    """The cue's own blue: a blue-dominant pixel with enough alpha to see.

    `min_alpha` is a parameter because the highlight deliberately draws two
    borders - a soft wide one at 35% and a bright one on the window's own edge -
    and a single threshold either missed the soft one or passed the bright one.
    """
    return value[2] > 150 and value[2] - value[0] > 60 and value[3] > min_alpha


def test_the_pointer_is_drawn_where_it_was_sent(render) -> None:
    """Ink at the coordinates asked for, white with a dark edge, and not elsewhere."""
    overlay = ao.CueOverlay()
    overlay.glide_to(120, 90, animate=False)
    _width, at, box = render(overlay.cues, time.monotonic())

    pointer = box(120, 90, ao.POINTER_SIZE)
    inked = [value for value in pointer if value[3] > 40]
    assert len(inked) > 60, (
        f"only {len(inked)} of {len(pointer)} pointer pixels drew; a cue nobody "
        f"can see is not a cue")
    assert any(v[0] > 200 and v[1] > 200 for v in pointer), "the arrow is not white"
    assert any(v[0] < 90 and v[1] < 90 and v[3] > 200 for v in pointer), (
        "the arrow has no dark edge, so it would vanish on a dark guest")
    assert at(100, 100)[3] == 0, "ink appeared somewhere nobody was sent"


def test_a_highlight_stands_around_the_window_and_names_it(render) -> None:
    """The standing border is outside the window, and it keeps its name tab.

    The distinction that matters: a *ring* marks one click and fades, while a
    highlight answers "which window is being driven" for as long as it is. So
    the blue must be **outside** the given box (on its border), and the name tab
    must sit above it rather than over the app's own toolbar.
    """
    overlay = ao.CueOverlay()
    overlay.highlight(120, 90, 80, 50)
    _width, at, _box = render(overlay.cues, time.monotonic())

    # Two borders by design: a soft wide one outside the window, and a bright
    # one on its own edge. Both are cue blue; only the bright one is opaque.
    soft = at(160, 87)             # 3px above the window's top edge
    assert soft[3] > 40, f"no highlight border above the window: {soft}"
    assert _is_cue_blue(soft, min_alpha=40), f"the highlight is not cue blue: {soft}"
    bright = at(160, 91)           # on the window's own edge (measured: y=90-92)
    assert bright[3] > 150, f"the bright border is too faint to read: {bright}"
    assert _is_cue_blue(bright, min_alpha=150), f"the bright border is not cue blue: {bright}"
    # the name tab, above the window and never over its content
    assert at(130, 76)[3] > 40, f"no name tab above the window: {at(130, 76)}"
    assert at(125, 95)[3] == 0, (
        "the highlight covered the app's own content; a frame is meant to "
        "surround what the person is looking at, not stand in front of it")


def test_a_highlight_survives_a_second_message_and_clears() -> None:
    """A highlight that vanished on the next cue would answer nothing."""
    overlay = ao.CueOverlay()
    overlay.highlight(10, 10, 40, 40)
    overlay.apply({"glide": [400, 300]})
    assert "highlight" in overlay.cues, (
        "the highlight was dropped by the next message; a border that blinks "
        "out between two clicks is not an indication of anything")
    overlay.apply({"clear": True})
    assert "highlight" not in overlay.cues


def test_the_ring_outlines_and_is_hollow(render) -> None:
    """A ring is an outline: blue on its edge, nothing inside it."""
    overlay = ao.CueOverlay()
    overlay.glide_to(20, 20, animate=False)
    overlay.ring(200, 150, 60, 30)
    # No age is set on purpose: a ring is on at the instant it is drawn, which
    # is the whole point - the click is about to happen right now.
    _width, at, _box = render(overlay.cues, time.monotonic())

    assert _is_cue_blue(at(230, 151)), f"the ring's edge is not cue blue: {at(230, 151)}"
    assert at(240, 120)[3] == 0, "the ring filled in; it is an outline, and a fill would be claiming a target"


def test_a_click_ripples_where_it_happened(render) -> None:
    overlay = ao.CueOverlay()
    now = time.monotonic()
    overlay.glide_to(10, 10, animate=False)
    overlay.ripple(120, 90)
    overlay.cues["ripples"] = [(120.0, 90.0, now - 0.05)]
    _width, at, _box = render(overlay.cues, now)
    assert _is_cue_blue(at(120, 78)), f"no ripple at the clicked point: {at(120, 78)}"


def test_the_ring_is_on_at_once_and_fades_out_at_the_end(render) -> None:
    """A ring is visible the instant it is drawn, and gone when its life ends.

    The first draft faded *in*, which made it invisible at the only moment that
    matters - the click is about to happen - and a negative age (a caller whose
    clock reads a hair early) then produced a negative alpha, drawing nothing
    while the caller believed the ring was up. Both halves are asserted: new
    rings are fully on, and a ring past its life is not drawn at all.
    """
    overlay = ao.CueOverlay()
    overlay.glide_to(20, 20, animate=False)

    now = time.monotonic()
    overlay.ring(200, 150, 60, 30)
    _width, at, _box = render(overlay.cues, now)
    assert _is_cue_blue(at(230, 151)), (
        f"a ring the instant it was drawn was not visible: {at(230, 151)}")

    # Mid-life the ring is full; near the end of its life it is measurably
    # dimmer. Measured rather than asserted as present-or-absent, because a ring
    # that simply never fades is still "present" at both moments.
    overlay.cues["ring"] = (200.0, 150.0, 60.0, 30.0, now - 0.02)
    _width, at, _box = render(overlay.cues, now)
    full = at(230, 151)
    overlay.cues["ring"] = (200.0, 150.0, 60.0, 30.0, now - (ao.RING_SECONDS - 0.05))
    _width, at, _box = render(overlay.cues, now)
    dim = at(230, 151)
    assert full[3] > 200, f"a mid-life ring is not fully drawn: {full}"
    assert dim[3] < full[3] / 2, (
        f"a ring at the end of its life did not fade out: full={full} late={dim}")


# --- the timing, without a clock -----------------------------------------------


def test_the_pointer_arrives_over_the_glide_and_then_stops() -> None:
    overlay = ao.CueOverlay()
    overlay.glide_to(100, 100, animate=False)      # somewhere to start from
    overlay.glide_to(200, 100, animate=True)
    # A clock taken from the real one: an invented epoch can read ahead of
    # `monotonic()`, which after a restart is small, and the glide would look
    # finished before it began.
    start = time.monotonic()
    midway = overlay.pointer_now(start + ao.GLIDE_SECONDS / 2)
    assert 100 < midway[0] < 200, f"the pointer did not glide, it jumped: {midway}"
    # A pixel of slack: `start` is read a hair after the glide began, so the
    # first frame has already eased a fraction of a pixel along.
    assert abs(overlay.pointer_now(start)[0] - 100.0) < 1.0, (
        "the glide should begin at the previous position, not at the target")
    assert overlay.pointer_now(start + ao.GLIDE_SECONDS * 2) == (200.0, 100.0), (
        "the pointer kept moving after arriving")


def test_reduced_motion_means_the_pointer_is_already_there() -> None:
    overlay = ao.CueOverlay()
    overlay.glide_to(200, 100, animate=False)
    start = time.monotonic()
    assert overlay.pointer_now(start) == (200.0, 100.0)
    assert overlay.pointer_now(start + 0.01) == (200.0, 100.0)


def test_the_protocol_answers_a_ping_after_a_glide() -> None:
    overlay = ao.CueOverlay()
    assert overlay.apply({"ping": True})["pointer"] == [0.0, 0.0]
    overlay.apply({"glide": [50, 60], "ring": [0, 0, 10, 10]})
    assert overlay.cues["ring"][2] == 10
    overlay.apply({"glide": [70, 80]})
    overlay.apply({"clear": True})
    assert overlay.cues == {}


# --- the socket, with a stand-in layer ------------------------------------------


class _EchoLayer(threading.Thread):
    """A cue layer that answers pings with the pointer it was given.

    The real layer is a GTK process; what `cue()` and `wait_until_pointing_at()`
    need from it is only this, so the protocol can be tested without a display.
    """

    def __init__(self, path: str) -> None:
        super().__init__(daemon=True)
        self.path = path
        import os

        if os.path.exists(path):
            os.unlink(path)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(4)
        self.running = True

    def run(self) -> None:
        while self.running:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection) -> None:
        handle = connection.makefile("r")
        for line in handle:
            message = json.loads(line)
            if message.get("ping"):
                reply = {"ok": True, "pointer": message.get("pointer_at", [0.0, 0.0])}
                connection.sendall((json.dumps(reply) + "\n").encode())
        connection.close()

    def stop(self) -> None:
        self.running = False
        try:
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).connect(self.path)
        except OSError:
            pass
        self.server.close()


@pytest.fixture
def layer(tmp_path):
    server = _EchoLayer(str(tmp_path / "cue.sock"))
    server.start()
    yield server
    server.stop()


def test_a_message_reaches_the_layer_and_comes_back(layer) -> None:
    reply = ao.cue(layer.path, {"ping": True})
    assert reply == {"ok": True, "pointer": [0.0, 0.0]}, reply


def test_wait_until_pointing_at_waits_for_the_arrival(layer) -> None:
    """The click must not land before the cursor visibly does.

    With the layer reporting the pointer where the driver sent it, the wait
    returns immediately; with it reporting somewhere else, it gives up rather
    than lying about having waited.
    """
    assert ao.wait_until_pointing_at(layer.path, 0.0, 0.0) is True, (
        "the layer reports the pointer at the origin, so waiting for the origin "
        "must succeed immediately")
    assert ao.wait_until_pointing_at(layer.path, 400, 400, budget=0.3) is False, (
        "the layer never reports the pointer at (400,400), so the wait must give "
        "up rather than claim it arrived")


def test_no_layer_means_driving_still_works(layer) -> None:
    """A headless machine drives quietly rather than failing about a picture."""


    answer = ao.cue(layer.path + "-absent", {"ping": True})
    assert answer is None
    assert ao.wait_until_pointing_at(layer.path + "-absent", 10, 10) is True


def test_the_socket_path_lives_in_the_runtime_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert ao.socket_path("work") == str(tmp_path / "chronoa-cue-work.sock")


def test_a_socket_that_binds_before_it_listens_is_waited_for(tmp_path) -> None:
    """Regression: the first ping of a freshly started layer was refused.

    A unix socket file appears at `bind()` and only *listens* a moment later, so
    a client that connects in that window is refused - and a driver that read
    that as "no cue layer" silently drove the machine with no visible cursor,
    which is the one thing this module exists to prevent. Measured: the first
    ping of a live layer failed while every later one answered at once.
    """
    import os
    import threading

    path = str(tmp_path / "late.sock")
    if os.path.exists(path):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)          # the file exists from here...
    ready = threading.Event()

    def listen_later():
        time.sleep(0.6)        # ...but nothing listens yet
        server.listen(4)
        ready.set()
        connection, _ = server.accept()
        connection.sendall(b'{"ok": true, "pointer": [1.0, 2.0]}\n')
        connection.close()

    threading.Thread(target=listen_later, daemon=True).start()
    try:
        reply = ao.cue(path, {"ping": True}, timeout=3.0)
    finally:
        server.close()
    assert reply == {"ok": True, "pointer": [1.0, 2.0]}, (
        f"a layer still starting was treated as absent: {reply}")


def test_no_layer_is_absent_rather_than_slow(tmp_path) -> None:
    """The other half: a path with nothing behind it must still be None, or the
    retry above would turn every headless drive into a five-second wait."""
    assert ao.cue(str(tmp_path / "nothing.sock"), {"ping": True}, timeout=0.3) is None


# --- the cursor glyph and its label, both found by rendering and looking ------
#
# Both of these were reported as "the cursor isn't good" / "the blue box is in a
# different place", and neither is visible by reading the code: the glyph had no
# antialiasing and the label was drawn from two different origins. The images are
# in the commit message; the properties are here so they cannot come back.


def test_the_pointer_edge_is_antialiased_not_a_staircase() -> None:
    """Measured from the rendered sprite: the old one had **no** partially
    transparent pixels at all - one sample per pixel, written as fully on or
    fully off - so both diagonals of the arrow were a hard staircase."""
    from shani_chronoa import automation_overlay as ao
    pixels = ao._pointer_pixels()
    partial = sum(1 for index in range(3, len(pixels), 4)
                  if 0 < pixels[index] < 255)
    assert partial > 20, (
        f"only {partial} antialiased pixels - the glyph is being drawn with one "
        f"hard sample per pixel, which is what made it look broken")


def test_the_pointer_is_not_clipped_by_its_own_sprite() -> None:
    """The glyph must leave a margin on **every** side of its box.

    This is the property that actually distinguishes a pointer from the blob it
    replaced, and finding it took measuring both rather than picking a threshold:
    the head-to-tail width ratio is **2.0 for both shapes**, so a test written on
    that ratio passes the broken glyph and fails the fixed one - a check that
    cannot tell the two apart is not a check.

    The old quad's fourth corner sat at x=21 inside a 22-wide sprite and its
    tail reached y=21, so both were **flush against the edge** (right margin 0,
    bottom margin 0) and the arrow was cut off square - which is most of why it read
    as a smudge. Measured on the rendered pixels, so it is the shape a person
    sees rather than the corners a designer typed.
    """
    from shani_chronoa import automation_overlay as ao
    pixels = ao._pointer_pixels()
    size = ao.POINTER_SIZE
    filled = {(x, y) for y in range(size) for x in range(size)
              if pixels[(y * size + x) * 4 + 3] >= 128}
    assert filled, "the glyph drew nothing at all"
    # **The tip is exempt, and has to be.** A pointer's tip is its bounding-box
    # corner by definition - it is the hot spot, and it is drawn at exactly the
    # coordinate the click will use. Nudging it inward to give the sprite a
    # uniform margin would make the drawn arrow point somewhere other than where
    # the click lands, which is a far worse lie than a glyph touching its corner.
    # So only the two edges where clipping actually blunt the shape are checked.
    right = size - 1 - max(x for x, _ in filled)
    bottom = size - 1 - max(y for _, y in filled)
    for name, margin in (("right", right), ("bottom", bottom)):
        assert margin >= 1, (
            f"the glyph is flush against the {name} edge of its {size}px sprite "
            f"(margin {margin}px), so the arrow is being cut off square - that is "
            f"what made it read as a smudge")


def test_the_label_sits_beside_the_cursor_and_never_on_it() -> None:
    """**The box and the word were drawn from two different origins.**

    `append_layout` takes no position - it draws wherever the snapshot has been
    translated to. The old code drew the blue box at a fixed `+16, +16` while
    the text landed at `0, 0`, so the label was in two places at once: its word
    on the cursor tip and its box floating beside it. Measured here against the
    sprite's own bounds, which is the thing the offset has to clear.
    """
    from shani_chronoa import automation_overlay as ao
    tag_x = ao.POINTER_SIZE + ao.TAG_GAP
    assert tag_x >= ao.POINTER_SIZE, "the label must start beyond the sprite"
    assert tag_x > 16, (
        f"the label starts at {tag_x}px, inside the old fixed 16px offset that "
        f"put it on the arrow")
    # and the offset is derived, not typed, so redrawing the glyph cannot strand it
    assert (ao.TAG_GAP, ao.TAG_DROP) == (4, 2)


# --- the second cursor ---------------------------------------------------------
#
# The cue layer draws a "Chronoa" arrow and the system pointer is the real
# input. The module docstring says the two "read as one cursor", and that is
# true only while they are in the same place and only while one of them is the
# glide. At rest with both drawn, a person watching sees **two cursors** - which
# reports as two agents driving the mouse, or as the computer driving itself
# twice. Neither is true, and neither is what this module is for.


def test_the_drawn_cursor_can_be_retired_when_the_real_one_arrives() -> None:
    overlay = ao.CueOverlay()
    overlay.glide_to(400.0, 300.0, animate=False)
    assert overlay.cues.get("pointer") == (400.0, 300.0)

    overlay.hide()

    assert "pointer" not in overlay.cues, (
        "the drawn cursor is still on screen, so a person sees two arrows at "
        "the point where there should be one")


def test_a_ping_after_the_hide_still_says_where_the_pointer_went() -> None:
    """**Not** (0, 0): that reads to the driver as the cursor teleporting."""
    overlay = ao.CueOverlay()
    overlay.glide_to(400.0, 300.0, animate=False)
    overlay.hide()

    assert overlay.pointer_now() == (400.0, 300.0)
    # and through the protocol, which is how the driver actually asks
    assert overlay.apply({"ping": True}) == {
        "ok": True, "pointer": [400.0, 300.0], "drawn": False}


def test_a_ping_says_whether_the_cursor_is_still_on_screen() -> None:
    """The position alone cannot answer it, and that is the point.

    After a hide the remembered position is exactly where the arrow was retired,
    so a reader asking only for `pointer` is told the same thing either way -
    and "the second cursor is gone" would be a claim no measurement supports.
    """
    overlay = ao.CueOverlay()
    overlay.apply({"glide": [120, 90]})
    assert overlay.apply({"ping": True})["drawn"] is True

    overlay.apply({"hide": True})
    answered = overlay.apply({"ping": True})

    assert answered["drawn"] is False
    assert answered["pointer"] == [120.0, 90.0]


def test_hide_arrives_over_the_protocol() -> None:
    overlay = ao.CueOverlay()
    overlay.apply({"glide": [120, 90]})
    assert "pointer" in overlay.cues

    overlay.apply({"hide": True})

    assert "pointer" not in overlay.cues


def test_a_second_glide_starts_from_where_the_retired_one_left_off() -> None:
    """The hide kept the position, so the next glide does not start at 0,0.

    A glide that jumps in from the corner is the jump cut this module exists to
    avoid, reintroduced through the fix. Asked through `pointer_now` rather than
    through `glide_from`, because that is what the driver reads.
    """
    overlay = ao.CueOverlay()
    overlay.glide_to(400.0, 300.0, animate=False)
    overlay.hide()
    overlay.glide_to(410.0, 300.0, animate=True)

    assert overlay.pointer_now(now=1e9) == (410.0, 300.0), "it must land"
    # the first instant of the glide, which is where it left the last one
    started = overlay.cues["glide_started"]
    assert overlay.pointer_now(now=started) == (400.0, 300.0)


def test_clear_also_retires_the_cursor() -> None:
    overlay = ao.CueOverlay()
    overlay.glide_to(10.0, 20.0, animate=False)
    overlay.clear()
    assert "pointer" not in overlay.cues


# --- showing a layer that cannot be seen --------------------------------------


class _FakeWindow:
    def __init__(self):
        self.shown = 0
        self.presented = 0

    def show(self):
        self.shown += 1

    def present(self):
        self.presented += 1


def test_a_layer_without_layer_shell_is_raised_not_merely_mapped(capsys) -> None:
    """**`present()`, not `show()` - and this was got wrong once.**

    `present()` maps **and raises**; the raise is the whole feature, because an
    ordinary window that is merely mapped sits at the *bottom* of a Wayland
    stack, under the app it is annotating. Changing this to `show()` made the
    cue layer invisible and then claimed it could not be seen - the exact
    failure this module exists to prevent, introduced by its own fix.

    The cost is focus, and focus is a nuisance where invisibility is blindness:
    the widget is `set_can_target(False)`, so the layer takes no pointer input.
    """
    window = _FakeWindow()

    ao._show_unlayered(window)

    assert window.presented == 1, (
        "the layer was merely mapped, so it draws underneath the app it is "
        "annotating and nobody sees the feedback")
    assert window.shown == 0
    assert "focus" in capsys.readouterr().err, (
        "it did not admit that it takes keyboard focus")


def test_layer_shell_is_what_actually_puts_a_layer_above_others() -> None:
    """It must never claim a layer surface it does not have.

    **The premise of this test changed twice** and it was rewritten rather than
    loosened: it first asserted False *because the typelib was absent*, then
    the GTK 4 packages were installed, and now the library loads and the call
    runs - so the honest question is no longer "is it installed" but "does it
    ever say True when there is no layer surface".

    A plain `Gtk.Window` is an ordinary window and a Wayland compositor puts
    ordinary windows at the **bottom** of the stack; `set_default_size` sets a
    size, not a stacking level, so nothing in GTK4 or Xlib can raise one.
    """
    assert ao._make_it_a_layer(_FakeWindow()) is False, (
        "claimed a layer-shell surface for something that is not one")

    # **And the reason is kept, not replaced with a convenient one.** The
    # failure was reported as "gtk-layer-shell is not installed" on a machine
    # where it *was* installed - the wrong major version, which needs a
    # different fix entirely. A check that only returns False cannot tell those
    # apart, so the message is asserted to name the GTK 3/4 distinction.
    import logging
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger()
    # **`logging.info` is filtered at the root logger before any handler sees
    # it** - the root level defaults to WARNING, so adding a handler alone
    # captures nothing and the test passes for the wrong reason until it does
    # not. Measured: `records == []` with the handler attached and the level
    # untouched.
    was = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(Capture())
    try:
        ao._make_it_a_layer(_FakeWindow())
    finally:
        logger.handlers = [h for h in logger.handlers if not isinstance(h, Capture)]
        logger.setLevel(was)
    assert records, (
        "the reason was not logged, so an absent library, the wrong major "
        "version and a compositor that lacks the protocol are indistinguishable")
    # **A reason, not a state.** This message used to say "gtk-layer-shell is
    # not installed", which was true when it was written and became false the
    # moment the packages were installed - and the *cause* had changed
    # underneath it, from a missing library to a compositor that cannot speak
    # the protocol. A state goes stale silently; a cause does not.
    assert not any("is not installed" in m for m in records), (
        "the reason asserts a package state that changes the moment somebody "
        "installs something: %r" % records)

    # And the sentence the driver hands back must not be stale either.
    assert "not installed on this machine" not in ui_driver._CUE_LAYER_CAVEAT, (
        "the reported caveat still claims layer-shell is absent on a machine "
        "where it is installed and simply unusable by this compositor")


# --- the name inside the blue box ---------------------------------------------
#
# Reported as *"chronoa name not in the blue box"*. Reproduced by rendering and
# measuring, not by reading: `get_pixel_extents()` on a layout that has never
# been through a render pass returns **1x1**, so the box came out 13x8 around a
# word that renders about 11x12.

def _png_pixels(path):
    """Decode a PNG this renderer wrote, without a PNG library."""
    import struct
    import zlib

    data = open(path, "rb").read()
    width, height = struct.unpack(">II", data[16:24])
    colour = data[25]
    idat = b""
    position = 8
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        if kind == b"IDAT":
            idat += data[position + 8:position + 8 + length]
        position += 12 + length
    raw = zlib.decompress(idat)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    stride = width * channels
    previous = bytearray(stride)
    rows, position = [], 0
    for _ in range(height):
        kind = raw[position]
        position += 1
        line = bytearray(raw[position:position + stride])
        position += stride
        if kind == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 255
        elif kind == 2:
            for x in range(stride):
                line[x] = (line[x] + previous[x]) & 255
        elif kind == 3:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((a + previous[x]) >> 1)) & 255
        elif kind == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = previous[x]
                c = previous[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 255
        rows.append(bytes(line))
        previous = line
    return rows, channels


def _render_the_cue(tmp_path):
    """The real `_CueArea`, rendering the real cue, into a real texture."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gsk", "4.0")
    from gi.repository import Gsk, Graphene, Gtk

    if not Gtk.init_check():
        return None
    overlay = ao.CueOverlay()
    area = ao._CueArea(overlay)
    overlay.window = area
    overlay.cues = {"pointer": (20.0, 30.0)}
    snapshot = Gtk.Snapshot()
    area.do_snapshot(snapshot)
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(None)
    try:
        viewport = Graphene.Rect.alloc()
        viewport.init(0, 0, 300, 120)
        out = str(tmp_path / "cue.png")
        renderer.render_texture(snapshot.to_node(), viewport).save_to_png(out)
    finally:
        # measured: not unrealizing aborts the process at exit
        renderer.unrealize()
    return _png_pixels(out)


def _inside_box(blue, white):
    """White pixels within the blue box's bounds.

    **The cursor arrow has a white centre too**, so counting every white pixel
    on the texture measures the arrow and not the label - which is exactly what
    the first version of this probe did, and it reported "text is OUTSIDE the
    box" while the name was sitting in it.
    """
    if not blue:
        return []
    xs = [p[0] for p in blue]
    ys = [p[1] for p in blue]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    return [p for p in white if left <= p[0] <= right and top <= p[1] <= bottom]


def test_the_name_is_drawn_inside_its_box(tmp_path) -> None:
    rendered = _render_the_cue(tmp_path)
    if rendered is None:
        pytest.skip("no display backend for a real render")
    rows, channels = rendered
    height, width = len(rows), len(rows[0]) // channels
    blue, white = [], []
    for y in range(height):
        for x in range(width):
            px = rows[y][x * channels:x * channels + 3]
            alpha = rows[y][x * channels + 3] if channels == 4 else 255
            if alpha > 40 and all(abs(px[k] - int(ao.CUE_BLUE * 255)) < 30
                                  for k in range(3)):
                blue.append((x, y))
            if alpha > 40 and all(c > 200 for c in px):
                white.append((x, y))

    assert blue, "the blue box was not drawn at all"
    inside = _inside_box(blue, white)

    assert len(inside) > 30, (
        "the box is drawn but holds %d white pixels - the name is not in it "
        "(the box is sized from a layout that measures 1x1 before its first "
        "render pass)" % len(inside))


def test_the_highlight_tab_draws_its_name_not_just_a_blue_box(tmp_path) -> None:
    """Reported as *"chronoa name not in the blue box"*.

    **Not a positioning fault: there was nothing in the box to be misplaced.**
    `draw_highlight` measured the label, filled and outlined a box around that
    measurement, and never drew the label. It could not: `_pango_layout` called
    `Pango.FontMap.get_default()`, which **does not exist in this PyGObject**
    (`AttributeError`, and `get()` is absent too), so it returned `None` on
    every call and the box came from a character-count guess that looked
    entirely plausible - "Chronoa" guessed at 47x13 against a real 43x10.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gsk", "4.0")
    from gi.repository import Gsk, Graphene, Gtk

    if not Gtk.init_check():
        pytest.skip("no display backend for a real render")
    snapshot = Gtk.Snapshot()
    ao.draw_cues(snapshot,
                 {"highlight": (60.0, 90.0, 300.0, 110.0, "Chronoa")}, 0.0, None)
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(None)
    try:
        viewport = Graphene.Rect.alloc()
        viewport.init(0, 0, 420, 220)
        out = str(tmp_path / "hl.png")
        renderer.render_texture(snapshot.to_node(), viewport).save_to_png(out)
    finally:
        renderer.unrealize()
    rows, channels = _png_pixels(out)
    height, width = len(rows), len(rows[0]) // channels
    blue, white = [], []
    for y in range(height):
        for x in range(width):
            px = rows[y][x * channels:x * channels + 3]
            alpha = rows[y][x * channels + 3] if channels == 4 else 255
            if alpha > 40 and all(abs(px[k] - int(ao.CUE_BLUE * 255)) < 30
                                  for k in range(3)):
                blue.append((x, y))
            if alpha > 40 and all(c > 200 for c in px):
                white.append((x, y))

    # the tab is drawn above the window's top edge; the border lines are not it
    assert blue, "no blue at all - the highlight was not drawn"
    assert [p for p in white if p[1] < 89], (
        "the tab is an empty blue box: %d px of name above the window's edge"
        % len([p for p in white if p[1] < 89]))


def test_the_label_layout_is_measurable_and_not_the_character_count_guess() -> None:
    """The bug was swallowed by a guess that looked right.

    `layout_size` falls back to `len(text) * px * 0.62` when no layout can be
    built, and for this label the guess is 47x13 against a real 43x10 - close
    enough that nothing looked wrong. So the control asserts the real path is
    the one being taken.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    if not Gtk.init_check():
        pytest.skip("no display backend for a real render")
    layout = ao._pango_layout("Chronoa")

    assert layout is not None, (
        "no measurable layout - every tab will fall back to a character-count "
        "guess and draw an empty box")
    ink, _logical = layout.get_pixel_extents()
    assert ink.width > 10 and ink.height > 4, (
        "the layout measures %dx%d, which is the degenerate reading that "
        "starts this bug" % (ink.width, ink.height))


# --- putting the layer back on top --------------------------------------------
#
# An ordinary `Gtk.Window` has no layer to sit in, so anything that raises
# itself afterwards covers the feedback. Measured: the same probe found 5,845
# cue-blue pixels, then **0** on the next run with nothing changed except that a
# terminal had raised itself in between.


class _CountingWindow:
    def __init__(self):
        self.presents = 0
        self.shows = 0

    def present(self):
        self.presents += 1

    def show(self):
        self.shows += 1


def test_drawing_something_asks_for_the_window_to_be_raised_again() -> None:
    overlay = ao.CueOverlay()
    overlay.window = _CountingWindow()

    overlay.apply({"glide": [10, 20]})
    overlay.service_window()

    assert overlay.window.presents == 1, (
        "a glide was drawn and the window was left wherever it was")


def test_the_raise_is_coalesced_rather_than_done_per_message() -> None:
    """A 0.55s glide is ~33 frames; presenting per message is work for nothing."""
    overlay = ao.CueOverlay()
    overlay.window = _CountingWindow()

    for step in range(20):
        overlay.apply({"ripple": [step, step]})
    overlay.service_window()
    overlay.service_window()

    assert overlay.window.presents == 1, (
        "presented %d times for 20 messages" % overlay.window.presents)


def test_a_message_that_draws_nothing_raises_nothing() -> None:
    """`hide` and `clear` draw nothing, so they must not raise anything."""
    overlay = ao.CueOverlay()
    overlay.window = _CountingWindow()
    overlay.apply({"glide": [1, 2]})
    overlay.service_window()

    overlay.apply({"hide": True})
    overlay.apply({"clear": True})
    overlay.service_window()

    assert overlay.window.presents == 1, (
        "something that draws nothing raised the window anyway")


def test_a_window_that_cannot_be_presented_is_not_a_crash() -> None:
    """`present()` on a withdrawn window must not take the frame loop with it."""
    overlay = ao.CueOverlay()

    class Withdrawn:
        def present(self):
            raise RuntimeError("window was withdrawn")

    overlay.window = Withdrawn()
    overlay.apply({"glide": [1, 2]})
    overlay.service_window()          # must not raise


def test_the_frame_tick_is_what_actually_raises_the_window() -> None:
    """The rule the loop runs, checked without a main loop.

    `main()` opens a `GLib.MainLoop`, so a rule only reachable by starting one
    is a rule no test ever checks - and the tick really did leave the raise out
    once, with the file green.
    """
    overlay = ao.CueOverlay()
    overlay.window = _CountingWindow()

    class Area:
        redrawn = 0

        def queue_draw(self):
            self.redrawn += 1

    area = Area()
    overlay.apply({"glide": [5, 5]})

    assert ao._frame(overlay, area) is True, (
        "a falsy return stops the GLib timeout, so the layer draws one frame "
        "and then goes quiet for good")
    assert overlay.window.presents == 1, "the tick did not raise the window"
    assert area.redrawn == 1
    # and a second tick with nothing new does not present again
    ao._frame(overlay, area)
    assert overlay.window.presents == 1
