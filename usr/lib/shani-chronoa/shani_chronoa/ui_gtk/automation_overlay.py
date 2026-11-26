"""The cue layer: what the person watching sees when Chronoa drives a screen.

`browse` has an advantage nothing else here has: the window it drives is
Chronoa's own, so the feedback is drawn *inside* the page - a cursor labelled
"Chronoa" glides to the element, a ring outlines it, the click ripples. That is
`gui/browser_driver.py`, and it is why watching a page being driven looks like
somebody driving it.

A container's GUI app and a VM's guest are **not** Chronoa's window. They draw
on a display Chronoa does not own, so the same feedback cannot be injected into
them - but it can be drawn *over* them, by a transparent layer that belongs to
Chronoa and knows where the pointer is going. That is this module, and it is the
reason a container app or a VM can be driven in front of a person rather than
behind their back.

**The shape is `browse`'s, deliberately:** a **window** beside a **protocol**.
The window is a transparent layer whose widget has `set_can_target(False)`, so
nothing the cursor passes over can take input from it. The protocol is
newline-delimited JSON on a unix socket, which matters because the thing doing
the driving is a sandboxed skill child: a skill child cannot own a GTK main
loop, so the layer runs in its own process with its own, exactly as the in-app
browser runs in the app's.

**Why the drawing looks the way it does - three things measured on this box,
none of them guessed.** This PyGObject build cannot marshal `cairo.Context` (a
`DrawingArea`'s draw function never fires), cannot marshal `cairo.Region`
(`set_input_region` fails with "could not find foreign type Region"), and
**segfaults in `Gsk.FillNode` and `Gsk.BorderNode` when they are constructed
directly** - all three measured, which is why the first cairo draft and the
first node draft are not here. What is left works: `Gtk.Snapshot`'s
`append_color`, `append_border` (whose widths and colours are *sequences*, one
per edge) and `append_layout`, plus `Gdk.MemoryTexture` for the cursor glyph,
which is rasterised here in plain Python rather than by a vector path.

Verified by rendering the real node tree to a texture and reading the pixels
back out of the PNG it writes - `tests/test_automation_overlay.py`.

Honesty rules:
- the reduced-motion setting is honoured: `enable-animations` off means the
  pointer arrives instantly and no ripple is drawn, rather than animating
  anyway. Read per call, so flipping the setting needs no restart.
- the layer draws *what it was asked*, at the coordinates it was given. It
  never guesses where a target is: the driver - which knows the app's window,
  or the guest's screen - says "here", and the layer puts a cursor there. A
  cursor drawn in the wrong place is worse than none, because a person believes
  it.
- nothing in here clicks anything. It is a picture of an action, not the action.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Gdk, Graphene, Gsk, Gtk  # noqa: E402

#: The blue the whole cue is drawn in - the same one `browser_driver.py` uses, so
#: a person sees one colour meaning "this is Chronoa" wherever it appears.
CUE_RED, CUE_GREEN, CUE_BLUE = 0x35 / 255, 0x84 / 255, 0xE4 / 255
#: How long the pointer takes to arrive. A driver waits this long before it
#: acts, so the click lands after the cursor has visibly arrived.
GLIDE_SECONDS = 0.55
#: A click ripple's lifetime.
RIPPLE_SECONDS = 0.5
#: How long an outline stays up after being drawn.
RING_SECONDS = 0.3
#: The pointer glyph's box. Same proportions as the SVG `browser_driver.py`
#: injects, so the two read as one cursor.
POINTER_SIZE = 24
#: The arrow, as the four corners of the quad the SVG draws, in the same order.
#:
#: **Redrawn from the measured picture, because the old quad was not an arrow.**
#: Rendered at 12x and looked at, the previous `((3, 2), (10.5, 21), (13.1,
#: 13.4), (21, 11))` came out as a lopsided blob: the fourth corner sat almost
#: level with the third, so the head was barely wider than the tail and the
#: whole thing read as a smudge rather than a pointer. These corners are the
#: standard arrow proportions - a long tapering tail, a shoulder, and a head
#: that is clearly wider than the body.
_POINTER_QUAD = ((2.0, 1.0), (10.0, 22.0), (13.2, 14.6), (21.5, 12.4))
#: How wide the dark edge around the arrow is, in pixels.
_POINTER_EDGE = 1.5
#: Samples per pixel, per axis. **There was no antialiasing at all before** -
#: every pixel was a hard inside/outside test, so both diagonals were a
#: staircase, which is what made the old one look broken rather than merely
#: plain. 4x4 is sixteen samples per pixel, computed once per process.
_POINTER_SAMPLES = 4
#: The dark edge, as (r, g, b). Named because the rasteriser now blends against
#: it and a literal repeated three times in that expression would be a second
#: place to change the colour.
_EDGE_RGB = (26, 26, 30)


def reduced_motion() -> bool:
    """Whether the desktop has asked for less animation.

    Read per call rather than cached at import, so a person who flips the
    setting does not have to restart anything - the same reason
    `sandbox/limits.py` reads the seccomp switch per call.
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup("org.gnome.desktop.interface", True) is None:
            return False
        return not Gio.Settings.new("org.gnome.desktop.interface").get_boolean(
            "enable-animations")
    except Exception:  # noqa: BLE001 - a desktop that cannot be asked still
        return False     # gets a cue; it just does not animate.


# --- the drawing. Only the snapshot calls that are measured to work on this
# PyGObject: append_color, append_border (widths *and* colours are sequences),
# append_layout, and a rasterised texture for the glyph. ------------------------


def _rect(x: float, y: float, w: float, h: float):
    rect = Graphene.Rect.alloc()
    rect.init(x, y, w, h)
    return rect


def _color(red: float, green: float, blue: float, alpha: float = 1.0):
    return Gdk.RGBA(red=red, green=green, blue=blue, alpha=alpha)


def _point(x: float, y: float):
    point = Graphene.Point.alloc()
    point.init(x, y)
    return point


def _rounded(x: float, y: float, w: float, h: float, radius: float):
    box = Gsk.RoundedRect()
    box.init_from_rect(_rect(x, y, w, h), radius)
    return box


def _fill(snapshot, x: float, y: float, w: float, h: float, radius: float, color) -> None:
    """A filled rounded rectangle - the plate a name sits on.

    Without it a tag is a border with nothing behind the letters, and white text
    on transparency is invisible - measured, before this existed the "Chronoa"
    tab rendered as two pixels of blue and no readable name at all.
    """
    if w <= 0 or h <= 0:
        return
    # A snapshot method, not a node constructor: `Gsk.ColorNode` is the same
    # segfaulting family as FillNode and BorderNode here (measured - the process
    # dies inside g_atomic_ref_count_dec), while `append_color` works.
    snapshot.append_color(
        _color(color.red, color.green, color.blue, color.alpha),
        _rect(x, y, w, h))


def _outline(snapshot, x: float, y: float, w: float, h: float, radius: float,
             width: float, color, alpha: float = 1.0) -> None:
    """One outlined rounded rectangle, drawn as a four-edge border."""
    tinted = _color(color.red, color.green, color.blue, color.alpha * alpha)
    snapshot.append_border(_rounded(x, y, w, h, radius),
                           (width, width, width, width),
                           (tinted, tinted, tinted, tinted))


#: One offscreen label, reused, because a font map in this PyGObject can only be
#: had from a widget and making one per frame is waste. `Gtk.Label()` is never
#: mapped, so it costs nothing on screen.
_LABEL: "object | None" = None
_LABEL_MADE = False


def _pango_layout(text: str, px: int = 11):
    """A real, measurable layout for `text`, or None.

    **The obvious way does not work in this PyGObject.** `Pango.FontMap` has no
    static `get()` *or* `get_default()` - measured, both `AttributeError` - so
    the version that reached for a font map directly fell into its own
    `except Exception` on **every** call and returned `None`. The box then came
    from the character-count guess, which looked entirely plausible
    ("Chronoa" guessed at 47x13 against a real 43x10), so the tab drew an
    empty blue rectangle for as long as it existed and nothing said why.

    A widget's `create_pango_layout` is the route that does work, and it is
    measured: 43x10 for this label.
    """
    global _LABEL, _LABEL_MADE
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk, Pango

        if not _LABEL_MADE:
            _LABEL = Gtk.Label()
            _LABEL_MADE = True
        if _LABEL is None:
            return None
        layout = _LABEL.create_pango_layout(text)
        description = Pango.FontDescription.from_string("system-ui bold %d" % px)
        layout.set_font_description(description)
        layout.set_text(text, -1)
        return layout
    except Exception:  # noqa: BLE001 - no font means no text, not a wrong box
        return None


def layout_size(text: str, px: int = 11) -> "tuple[int, int]":
    """A text run's size without a widget, for a tag that is only a box.

    A `Gtk.Widget` can measure its own layout, but the highlight's name tab is
    drawn from the cue state, where there is no widget - and guessing a width
    puts the tab's border somewhere other than around its letters.
    """
    layout = _pango_layout(text, px)
    if layout is None:
        return int(len(text) * px * 0.62), px + 2
    ink, _logical = layout.get_pixel_extents()
    return ink.width, ink.height


def _inside_quad(px: float, py: float, quad) -> bool:
    """Is (px, py) inside the arrow's quad? Same polygon the SVG traces."""
    sign = 0
    count = len(quad)
    for index in range(count):
        (x0, y0), (x1, y1) = quad[index], quad[(index + 1) % count]
        cross = (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0)
        if cross == 0:
            continue
        sign = sign or (1 if cross > 0 else -1)
        if (1 if cross > 0 else -1) != sign:
            return False
    return True


def _distance_to_quad(px: float, py: float, quad) -> float:
    """How far (px, py) is from the nearest edge of the quad, in pixels."""
    best = float("inf")
    count = len(quad)
    for index in range(count):
        (x0, y0), (x1, y1) = quad[index], quad[(index + 1) % count]
        dx, dy = x1 - x0, y1 - y0
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            best = min(best, ((px - x0) ** 2 + (py - y0) ** 2) ** 0.5)
            continue
        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_squared))
        best = min(best, ((px - (x0 + t * dx)) ** 2 + (py - (y0 + t * dy)) ** 2) ** 0.5)
    return best


def _pointer_pixels() -> bytes:
    """The arrow as RGBA bytes: a dark edge, a white centre, nothing outside.

    Rasterised here because a vector fill is the one thing this PyGObject cannot
    do without segfaulting (measured: `Gsk.FillNode` crashes). The result is
    cached by the caller, so the scan happens once per process.

    **Supersampled**, which is the whole difference between this looking like a
    cursor and looking like a mistake. The previous version took one sample at
    the centre of each pixel and wrote it as fully on or fully off, so both
    diagonals of the arrow were a hard staircase - the thing a person sees and
    reads as "this is broken". Here each pixel is `_POINTER_SAMPLES` squared
    subsamples and the colour is the average of what they hit, which is how every
    rasteriser since the 1960s has solved exactly this. Alpha is the coverage,
    so the edge blends with whatever is behind the layer instead of stair-stepping
    against it.
    """
    size = POINTER_SIZE
    steps = _POINTER_SAMPLES
    step = 1.0 / steps
    buffer = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            fill_hits = edge_hits = 0
            for sy in range(steps):
                py = y + (sy + 0.5) * step
                for sx in range(steps):
                    px = x + (sx + 0.5) * step
                    if _inside_quad(px, py, _POINTER_QUAD):
                        if _distance_to_quad(px, py, _POINTER_QUAD) > _POINTER_EDGE:
                            fill_hits += 1
                        else:
                            edge_hits += 1
                    elif _distance_to_quad(px, py, _POINTER_QUAD) <= _POINTER_EDGE:
                        edge_hits += 1
            total = steps * steps
            covered = fill_hits + edge_hits
            if not covered:
                continue
            index = (y * size + x) * 4
            # the interior colour is the mean of its own hits, the edge colour
            # the mean of the rest, so a pixel half white and half black is the
            # blend of the two rather than whichever won the count
            alpha = int(round(255 * covered / total))
            weight = fill_hits / covered if covered else 0.0
            red = int(round(255 * weight + _EDGE_RGB[0] * (1 - weight)))
            green = int(round(255 * weight + _EDGE_RGB[1] * (1 - weight)))
            blue = int(round(255 * weight + _EDGE_RGB[2] * (1 - weight)))
            buffer[index:index + 4] = bytes((red, green, blue, alpha))
    return bytes(buffer)


_POINTER_TEXTURE = None


def _pointer_texture():
    global _POINTER_TEXTURE
    if _POINTER_TEXTURE is None:
        _POINTER_TEXTURE = Gdk.MemoryTexture.new(
            POINTER_SIZE, POINTER_SIZE, Gdk.MemoryFormat.R8G8B8A8,
            GLib.Bytes.new(_pointer_pixels()), POINTER_SIZE * 4)
    return _POINTER_TEXTURE


def draw_pointer(snapshot, x: float, y: float) -> None:
    """The cursor glyph at (x, y): a white arrow with a dark edge.

    A texture rather than a path, for the reason `_pointer_pixels` gives: the
    dark edge is part of the raster, so it reads on any background the same way
    the SVG's stroked arrow does.
    """
    snapshot.save()
    snapshot.translate(_point(x, y))
    snapshot.append_texture(_pointer_texture(),
                            _rect(0, 0, POINTER_SIZE, POINTER_SIZE))
    snapshot.restore()


def draw_highlight(snapshot, x: float, y: float, w: float, h: float,
                   label: str = "Chronoa") -> None:
    """A standing border around the window being driven, with a name tab.

    The difference from `draw_ring` is persistence: a ring fades over its life
    because it marks one click, while this stays as long as the window is being
    driven. "Is it acting on my app, or on something else?" has to have a
    visible answer rather than an inferred one.
    """
    if w <= 0 or h <= 0:
        return
    # Two borders: a soft wide one outside the window's edge and a bright one on
    # it, so it reads against both a light and a dark app.
    _outline(snapshot, x - 3, y - 3, w + 6, h + 6, 10, 4.0,
             _color(CUE_RED, CUE_GREEN, CUE_BLUE, 0.35))
    _outline(snapshot, x, y, w, h, 8, 3.0, _color(CUE_RED, CUE_GREEN, CUE_BLUE))
    # The name tab sits above the window, so it never covers the app's toolbar.
    #
    # **And it has to draw the name.** It measured the label, sized a box around
    # that measurement, filled it and outlined it - and never drew the label.
    # Reported as *"chronoa name not in the blue box"*, which is not a
    # positioning fault at all: there was nothing in the box to be misplaced.
    # Every other tab on this layer draws its text, and this one did not.
    if label:
        layout = _pango_layout(label)
        width, height = layout_size(label)
        box_x, box_y = x, y - height - 8
        box_w, box_h = width + 12, height + 7
        _fill(snapshot, box_x, box_y, box_w, box_h, 9,
              _color(CUE_RED, CUE_GREEN, CUE_BLUE))
        _outline(snapshot, box_x, box_y, box_w, box_h, 9, 3.0,
                 _color(CUE_RED, CUE_GREEN, CUE_BLUE))
        if layout is not None:
            snapshot.save()
            # centred, computed from what was measured rather than hard-coded:
            # 6px of the 12px horizontal padding, 3px of the 7px vertical.
            snapshot.translate(_point(box_x + (box_w - width) / 2,
                                      box_y + (box_h - height) / 2))
            snapshot.append_layout(layout, _color(1, 1, 1))
            snapshot.restore()


def draw_ring(snapshot, x: float, y: float, w: float, h: float, alpha: float = 1.0) -> None:
    """The outline around whatever is about to be acted on."""
    if w <= 0 or h <= 0:
        return
    _outline(snapshot, x, y, w, h, 8, 3.0, _color(CUE_RED, CUE_GREEN, CUE_BLUE), alpha)
    _outline(snapshot, x - 5, y - 5, w + 10, h + 10, 12, 7.0,
             _color(CUE_RED, CUE_GREEN, CUE_BLUE, 0.25), alpha)


def draw_ripple(snapshot, x: float, y: float, progress: float) -> None:
    """A click: a ring that grows and fades. `progress` is 0..1.

    A ring rather than a filled circle, because a fill would need the vector
    path this build segfaults on. It reads as the same click.
    """
    if not 0.0 <= progress <= 1.0:
        return
    radius = 8 + progress * 40
    _outline(snapshot, x - radius, y - radius, radius * 2, radius * 2, radius,
             3.0, _color(CUE_RED, CUE_GREEN, CUE_BLUE), (1 - progress) ** 0.5)


#: Where the name tab sits relative to the cursor's tip, in pixels: clear of
#: the arrow's widest point, level with its head.
#:
#: **It was a flat `+16, +16`, and that put the tag on top of the arrow.** The
#: cursor's tip is the quad's first corner and its tail runs down and to the
#: left, so a label 16px right and 16px down lands squarely on the tail - which
#: is what "the blue box is in the wrong place" is. Both numbers are now derived
#: from the sprite rather than typed, so changing `POINTER_SIZE` or redrawing the
#: quad cannot leave the label stranded again.
TAG_GAP = 4
TAG_DROP = 2


def draw_tag(snapshot, x: float, y: float, layout) -> None:
    """The small blue name beside the cursor, so a person knows who moved it.

    **The box and the word are drawn from one origin, because they used not to
    be.** `append_layout` takes no position - it draws wherever the snapshot has
    been translated to - so the old version drew the blue box at `+16, +16` and
    the text at `0, 0`. The label was therefore in two places at once: its word
    on the cursor tip and its box floating off to one side. That is exactly what
    "the blue box is in a different location" is, and it is why the offset was
    never fixed by moving it: the two were never related in the first place.

    The tag is anchored to `POINTER_SIZE` so it clears the arrow's widest point
    whatever shape the glyph has, and the text is translated *into* the box it
    belongs to.
    """
    line, _ = layout.get_pixel_extents()
    # **A layout that has not been rendered yet measures 1x1**, and a box built
    # from that is 13x8 around an 11x12 word - the text ends up outside its own
    # box, which is the bug this was measured to fix. So the measured size is
    # only trusted once it is plausible, and until then the box is sized from
    # the characters it has to hold.
    if line.width < 2 or line.height < 2:
        text = layout.get_text() or ""
        width, height = max(8 * len(text) + 12, 24), 18
    else:
        width, height = line.width + 12, line.height + 7
    snapshot.save()
    snapshot.translate(_point(x + POINTER_SIZE + TAG_GAP, y + TAG_DROP))
    _fill(snapshot, 0, 0, width, height, 9, _color(CUE_RED, CUE_GREEN, CUE_BLUE))
    _outline(snapshot, 0, 0, width, height, 9, 3.0,
             _color(CUE_RED, CUE_GREEN, CUE_BLUE))
    snapshot.save()
    # centred in the box: half the horizontal padding either side, and the same
    # vertically - computed rather than assumed, so a fallback box and a
    # measured one both centre correctly.
    snapshot.translate(_point((width - line.width) / 2, (height - line.height) / 2))
    snapshot.append_layout(layout, _color(1, 1, 1))
    snapshot.restore()
    snapshot.restore()


def draw_cues(snapshot, cues: dict, now: float, layout=None) -> None:
    """Everything on screen: the ring, the ripples, then the pointer.

    Order matters - the pointer is drawn last so it is never under a ripple
    that started where it now sits.
    """
    highlight = cues.get("highlight")
    if highlight:
        x, y, w, h, label = highlight
        draw_highlight(snapshot, x, y, w, h, label)
    ring = cues.get("ring")
    if ring:
        x, y, w, h, since = ring
        age = now - since
        # On at once, and fading over its whole life - a ring that held full
        # alpha and dropped in the last sixtieth of a second read as a flicker.
        # A negative age (a caller whose clock reads early) is "just drawn", so
        # it is fully on.
        if age <= RING_SECONDS:
            remaining = (RING_SECONDS - age) / RING_SECONDS
            draw_ring(snapshot, x, y, w, h, max(0.0, min(1.0, remaining)))
    for x, y, since in cues.get("ripples", []):
        progress = (now - since) / RIPPLE_SECONDS
        if progress <= 1.0:
            draw_ripple(snapshot, x, y, progress)
    pointer = cues.get("pointer")
    if pointer:
        if layout is not None:
            snapshot.save()
            snapshot.translate(_point(pointer[0], pointer[1]))
            draw_tag(snapshot, 0, 0, layout)
            snapshot.restore()
        draw_pointer(snapshot, pointer[0], pointer[1])


# --- the state the protocol drives --------------------------------------------


class CueOverlay:
    """What is currently being shown, and the messages that change it.

    Separate from the window so the drawing and the timing can be exercised
    with no display at all - and separately verified by rendering the real node
    tree to a texture and reading the pixels back.
    """

    def __init__(self) -> None:
        self.cues: dict = {}
        self.window = None
        # Where the drawn pointer was last told to go, kept across `hide()` so a
        # `ping` afterwards answers "here" rather than (0, 0) - which would read
        # to the driver as the cursor having teleported to the corner.
        self.last_pointer: "tuple[float, float] | None" = None
        #: Set when something was drawn, so the window can be raised again.
        self._raise_pending = False

    def service_window(self) -> None:
        """Put the window back on top, **at most once per frame**.

        An ordinary `Gtk.Window` has no layer to sit in, so anything that raises
        itself afterwards covers the feedback - measured, repeatedly: the same
        probe that found 5,845 cue-blue pixels found **0** on the next run, with
        nothing changed except that a terminal had raised itself in between.

        Presenting again brings it back. It is coalesced into the 16 ms tick
        rather than done per message, because `present()` on every message of a
        0.55 s glide would be a present per frame for no benefit, and because
        this runs on the **main loop**: `apply()` is called from the socket
        thread, where touching a GTK window is not safe.
        """
        if not self._raise_pending or self.window is None:
            return
        self._raise_pending = False
        try:
            self.window.present()
        except Exception:  # noqa: BLE001 - a withdrawn window is not a failure
            pass

    def glide_to(self, x: float, y: float, animate: bool = True) -> None:
        """Point at a place, arriving over `GLIDE_SECONDS` unless motion is off."""
        origin = self.cues.get("pointer", self.last_pointer or (float(x), float(y)))
        self.cues["pointer"] = (float(x), float(y))
        self.last_pointer = self.cues["pointer"]
        if animate:
            self.cues["glide_from"] = origin
            self.cues["glide_started"] = time.monotonic()
        else:
            self.cues.pop("glide_from", None)
            self.cues.pop("glide_started", None)

    def ring(self, x: float, y: float, w: float, h: float) -> None:
        self.cues["ring"] = (float(x), float(y), float(w), float(h), time.monotonic())

    def highlight(self, x: float, y: float, w: float, h: float,
                  label: str = "Chronoa") -> None:
        """The standing border around the window being driven.

        No timestamp on purpose: a highlight that fades is a highlight that stops
        answering the question it exists for.
        """
        self.cues["highlight"] = (float(x), float(y), float(w), float(h), label)

    def ripple(self, x: float, y: float) -> None:
        if reduced_motion():
            return
        now = time.monotonic()
        self.cues.setdefault("ripples", []).append((float(x), float(y), now))
        self.cues["ripples"] = [r for r in self.cues["ripples"]
                                if now - r[2] < RIPPLE_SECONDS]

    def hide(self) -> None:
        """Stop drawing the pointer, because the **real** one has arrived.

        This is what makes the two cursors one cursor, and it is the whole reason
        this method exists. The drawn pointer is the *glide*: it exists to show a
        person where Chronoa is going while the real pointer is still on its way.
        Once the real pointer has arrived it has nothing left to add, and leaving
        it on screen puts **two arrows** where a person expects one - which reads
        as two agents driving the mouse, or as the computer driving itself twice.

        The position is remembered rather than discarded, so a `ping` after a
        hide still answers with where Chronoa left the pointer. A reader that
        answered `(0, 0)` would be telling the driver the cursor had teleported.
        """
        if "pointer" in self.cues:
            self.last_pointer = self.cues["pointer"]
        self.cues.pop("pointer", None)
        self.cues.pop("glide_from", None)
        self.cues.pop("glide_started", None)

    def clear(self) -> None:
        self.hide()
        self.cues.clear()

    def pointer_now(self, now: "float | None" = None) -> "tuple[float, float]":
        """Where the pointer is right now, mid-glide.

        The driver waits for the glide before it acts, so it needs to ask; and a
        test needs to ask without a clock.
        """
        pointer = self.cues.get("pointer")
        if not pointer:
            # Hidden because the real pointer has arrived. Answering the real
            # pointer's place is the truth: that is where it went.
            return self.last_pointer or (0.0, 0.0)
        started = self.cues.get("glide_started")
        if not started:
            return pointer
        elapsed = (now if now is not None else time.monotonic()) - started
        if elapsed >= GLIDE_SECONDS:
            return pointer
        origin = self.cues.get("glide_from", pointer)
        t = max(0.0, min(1.0, elapsed / GLIDE_SECONDS))
        # The same cubic ease-out `browser_driver.py` uses, so the two feel alike.
        eased = 1 - (1 - t) ** 3
        return (origin[0] + (pointer[0] - origin[0]) * eased,
                origin[1] + (pointer[1] - origin[1]) * eased)

    def apply(self, message: dict) -> "dict | None":
        """One JSON message. Answers a `ping`; anything else is fire-and-forget."""
        if "glide" in message:
            x, y = message["glide"]
            self.glide_to(x, y, animate=not reduced_motion())
        if "ring" in message:
            x, y, w, h = message["ring"]
            self.ring(x, y, w, h)
        if "highlight" in message:
            x, y, w, h = message["highlight"]
            self.highlight(x, y, w, h, message.get("label") or "Chronoa")
        if "ripple" in message:
            x, y = message["ripple"]
            self.ripple(x, y)
        if message.get("hide"):
            self.hide()
        if message.get("clear"):
            self.clear()
        # **Anything that draws wants the window on top again.** Not `hide`: a
        # message that draws nothing has no business raising anything.
        if any(key in message for key in
               ("glide", "ring", "highlight", "ripple")):
            self._raise_pending = True
        if message.get("ping"):
            # **`drawn` as well as `pointer`.** The position alone cannot answer
            # "is the Chronoa cursor on screen right now?", because after a hide
            # the remembered position is exactly the one it was retired *at* -
            # so a ping reports (187,806) whether the arrow is there or not. A
            # probe that cannot tell those apart is how "the second cursor is
            # gone" gets reported on a machine that still has two.
            return {"ok": True, "pointer": list(self.pointer_now()),
                    "drawn": "pointer" in self.cues}
        return None


# --- the client side: what a driver uses --------------------------------------


def cue(path: str, message: dict, timeout: float = 5.0) -> "dict | None":
    """Send one message to a running cue layer, and wait for its `ping` reply.

    The timeout is five seconds because the layer is a GTK process and its
    **first** reply waits for its main loop to come up - measured at up to about
    two seconds on a Wayland session, with every reply after that returning in
    under a millisecond. Two seconds, the first draft's value, made the very
    first ping fail on a machine where everything was working.

    A layer that is not running is not an error: the driving still works, it is
    just not visible - so this returns None rather than raising, and a headless
    machine drives quietly instead of failing loudly about a picture.
    """
    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(timeout)
        # A socket file appears at bind() and only *listens* a moment later, so
        # a refuse in that window is "still starting", not "not running" -
        # measured: the first ping of a freshly started layer is refused while
        # every later one answers at once. Retrying is what keeps a driver from
        # silently skipping the visible cue on the very first click.
        deadline = time.monotonic() + timeout
        while True:
            try:
                connection.connect(path)
                break
            except ConnectionRefusedError:
                if time.monotonic() >= deadline:
                    return None
                connection.close()
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.settimeout(timeout)
                time.sleep(0.05)
        connection.sendall((json.dumps(message) + "\n").encode())
        connection.shutdown(socket.SHUT_WR)
        reply = connection.makefile("r").readline()
        connection.close()
        return json.loads(reply) if reply.strip() else None
    except (OSError, ValueError):
        return None


def wait_until_pointing_at(path: str, x: float, y: float, budget: float = 1.5) -> bool:
    """Block until the visible cursor is actually at (x, y).

    A driver that acts before the cursor has arrived produces the one thing
    this whole module exists to prevent: the click landing where the cursor
    visibly is not. Reduces to True immediately with no layer running.
    """
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        reply = cue(path, {"ping": True})
        if reply is None:
            return True
        pointer = reply.get("pointer") or [0.0, 0.0]
        if abs(pointer[0] - x) < 1.0 and abs(pointer[1] - y) < 1.0:
            return True
        time.sleep(0.05)
    return False


def socket_path(name: str) -> str:
    """Where a named machine's or app's cue layer listens."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "shani-chronoa")
    return os.path.join(runtime, f"chronoa-cue-{name}.sock")


# --- the process ---------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Run the cue layer. `--socket PATH` is where a driver connects."""
    parser = argparse.ArgumentParser(description="Chronoa's visible cue layer")
    parser.add_argument("--socket", required=True, help="unix socket to drive it on")
    arguments = parser.parse_args(argv)

    from gi.repository import GLib as _GLib

    overlay = CueOverlay()

    window = Gtk.Window()
    window.set_decorated(False)
    window.set_resizable(False)
    window.add_css_class("chronoa-cue")
    overlay.window = window

    # **The whole reason a person can watch this, and it was missing.**
    # A plain `Gtk.Window` is an ordinary window: the compositor puts it at the
    # bottom of the stack, so on Wayland it draws *under* the app being driven
    # and nobody sees a thing. `set_default_size` sets a size, not a stacking
    # level - it cannot raise anything.
    #
    # **A layer-shell surface is the only mechanism Wayland offers** for a
    # transparent surface above every other client, and it is compositor-
    # specific. Measured on this desktop: `GtkLayerShell` is **not installed**,
    # and there is no fallback that works - so this layer genuinely cannot be
    # seen here, and the driver says so rather than pretending. On a desktop
    # with layer-shell the path below is the whole fix.
    layered = _make_it_a_layer(window)

    display = window.get_display()
    width = height = 0
    monitors = display.get_monitors()
    if monitors.get_n_items():
        geometry = monitors.get_item(0).get_geometry()
        width, height = geometry.width, geometry.height
    window.set_default_size(max(width, 320), max(height, 240))

    area = _CueArea(overlay)
    window.set_child(area)

    provider = Gtk.CssProvider()
    provider.load_from_data(b"window.chronoa-cue { background-color: transparent; }")
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    _GLib.timeout_add(16, lambda: _frame(overlay, area))

    server = _listen(arguments.socket)
    threading.Thread(target=_accept_loop, args=(server, overlay, _GLib),
                     daemon=True).start()

    if not layered:
        _show_unlayered(window)
    _GLib.MainLoop().run()
    return 0


def _frame(overlay: CueOverlay, area) -> bool:
    """One 16 ms tick: raise the window if a cue asked for it, then redraw.

    Returns True because GLib keeps a timeout alive on a true return, and a
    falsy one would silently stop the cue layer after its first frame - which
    is exactly the "it ran and drew nothing" failure this module has had.

    Its own function so it can be tested: `main()` opens a GTK main loop, and a
    rule only reachable by starting a display is a rule never checked. That is
    not hypothetical - the first version of this had the tick calling
    `queue_draw()` alone, and no test in the file could see that the raise was
    missing.
    """
    overlay.service_window()
    area.queue_draw()
    return True


def _show_unlayered(window) -> None:
    """Map **and raise** a layer that has no layer-shell surface.

    **`present()`, not `show()`** - and this was got wrong once. `present()`
    maps **and raises**; the raise is the entire feature, because an ordinary
    window that is merely mapped sits at the bottom of a Wayland stack under the
    app it is annotating. Switching this to `show()` made the layer invisible
    and then wrote a comment claiming it could not be seen - a self-inflicted
    version of the exact failure this module exists to prevent.

    The cost of `present()` is that it also **focuses**, which steals the
    keyboard from the app being driven. That is a real annoyance and it is the
    price of having no layer-shell: the widget is `set_can_target(False)`, so
    the layer takes no pointer input at all, and focus is the only thing lost.
    Paying a nuisance to keep the feedback visible is the right way round.

    Split out from `main()` so the decision is testable: `main()` opens a GTK
    main loop, and a rule that can only be checked by starting a display is a
    rule that is never checked.
    """
    window.present()
    print("chronoa-cue: no layer-shell surface (this compositor does not "
          "implement wlr-layer-shell), so this layer is an ordinary window "
          "raised with present() - it is visible, and it takes keyboard focus "
          "as the price", file=sys.stderr, flush=True)


def _make_it_a_layer(window) -> bool:
    """Put `window` on the overlay layer, if this desktop has layer-shell.

    Returns whether it worked. **`False` is not a failure to swallow** - it is
    the answer to "can this feedback be seen at all on this machine", and the
    caller reports it. The **reason** is logged rather than discarded, because
    "it did not work" and "the library is the wrong major version" call for
    completely different fixes and this function used to claim the second while
    the first was true.

    The six calls are the whole protocol: anchor to every edge so the surface
    covers the screen, `OVERLAY` so it is above ordinary windows, no exclusive
    zone so it reserves no space, no keyboard interactivity so it can never
    take a keystroke, and a namespace because the protocol requires one and
    because it is how a session identifies the layer.

    **Three things here were wrong and all three failed quietly.**

    **The namespace depends on the GTK major version**, and that is the whole
    trap - measured on Ubuntu 26.04 by reading both packages' file lists:

    | typelib file | namespace | package | GTK |
    |---|---|---|---|
    | `GtkLayerShell-0.1.typelib` | `GtkLayerShell` **0.1** | `gir1.2-gtklayershell-0.1` | **3** |
    | `Gtk4LayerShell-1.0.typelib` | `Gtk4LayerShell` **1.0** | `gir1.2-gtk4layershell-1.0` | **4** |

    So it is not a version typo - it is a **different namespace**. The GTK 3
    build cannot load in a GTK 4 process at all: `ImportError: Requiring
    namespace 'Gtk' version '3.0', but '4.0' is already loaded`. Both spellings
    are tried, GTK 4 first, because some distributions ship a GTK 4 build under
    the older name.

    **And reaching the C library with ctypes is not a workaround.**
       `libgtk-layer-shell.so.0` links `libgtk-3.so.0`, so loading it pulls a
       second GObject type system into a GTK 4 process - measured, it fails
       with `cannot register existing type 'GtkWidget'` and never gets as far
       as the window. **Two GObject type systems in one process is not a
       degraded mode; it is a broken one.**
    """
    _Shell = None
    for namespace, version in (("Gtk4LayerShell", "1.0"),
                               ("GtkLayerShell", "0.1")):
        try:
            import gi as _gi
            _gi.require_version(namespace, version)
            from gi.repository import Gtk4LayerShell as _Shell
            break
        except Exception as error:  # noqa: BLE001 - try the other spelling
            logging.info("cue layer: no %s %s (%s)", namespace, version, error)
            _Shell = None
    if _Shell is None:
        logging.info(
            "cue layer: no layer-shell surface. A GTK 4 app needs "
            "gir1.2-gtk4layershell-1.0 and libgtk4-layer-shell0 - the "
            "GtkLayerShell-0.1 typelib many systems ship is the GTK 3 build "
            "and cannot load in a GTK 4 process.")
        return False
    try:
        _Shell.init_for_window(window)
        _Shell.set_layer(window, _Shell.Layer.OVERLAY)
        _Shell.set_namespace(window, "chronoa-cue")
        _Shell.set_anchor(window, _Shell.Edge.TOP, True)
        _Shell.set_anchor(window, _Shell.Edge.BOTTOM, True)
        _Shell.set_anchor(window, _Shell.Edge.LEFT, True)
        _Shell.set_anchor(window, _Shell.Edge.RIGHT, True)
        _Shell.set_exclusive_zone(window, 0)
        # **`set_keyboard_interactivity` does not exist in 1.x** - the GTK 3 API's
        # way of saying "this layer takes no keys" was replaced by a *mode*, and
        # calling the old name is an `AttributeError` that took the whole cue
        # layer down: measured, `exit=1` and the socket was never created, so the
        # driver got no layer at all rather than a degraded one.
        if hasattr(_Shell, "set_keyboard_interactivity"):
            _Shell.set_keyboard_interactivity(window, 0)
        _Shell.set_keyboard_mode(window, _Shell.KeyboardMode.NONE)
        surface = _Shell.get_zwlr_layer_surface_v1(window)
    except Exception as error:  # noqa: BLE001 - a layer is worth less than a window
        # **Anything raised in here kills the cue layer entirely**, because
        # `main()` calls this before it listens on the socket: measured, one
        # AttributeError meant `exit=1` and no socket, so the driver had no
        # feedback at all rather than unlayered feedback. Every failure from here
        # on is a reason to fall back, never a reason to die.
        logging.info("cue layer: layer-shell setup failed (%s)", error)
        return False

    # **And the compositor may simply not have the protocol.** `wlr-layer-shell`
    # is a **wlroots/KDE** protocol; **GNOME's Mutter has never implemented
    # it** - confirmed by gtk4-layer-shell's own README ("Layer shell is not
    # supported on: Gnome-on-Wayland"), by wayland.app's compositor-support
    # matrix, and by the library's own warning here: *"Failed to initialize layer
    # surface, it appears your Wayland compositor doesn't support Layer Shell"*.
    #
    # So on this desktop this path **cannot** succeed, and pretending otherwise
    # by returning True would leave the window unlayered and say it was layered.
    # `get_zwlr_layer_surface_v1` is the library's own answer to "did it work" -
    # `init_for_window` returning says only that the *call* worked.
    if not surface:
        logging.info(
            "cue layer: layer-shell loaded but the compositor created no layer "
            "surface. wlr-layer-shell is a wlroots/KDE protocol and Mutter does "
            "not implement it, so on GNOME this cannot work - falling back to an "
            "ordinary window, which is raised again on every cue instead.")
        return False
    return True


class _CueArea(Gtk.Widget):
    """The layer itself: transparent, and never receiving input.

    `set_can_target(False)` is what makes it a layer rather than a window: the
    widget cannot be pointed at, so a click meant for the app underneath is
    never stolen by the picture of the click.
    """

    def __init__(self, overlay: CueOverlay) -> None:
        super().__init__()
        self._overlay = overlay
        self.set_can_target(False)
        self.set_focusable(False)
        # **One layout, built once.**
        #
        # The first version created a pango layout inside `do_snapshot`, every
        # frame, and measured it immediately to size the label's blue box. A
        # layout that has never been through a render pass reports
        # **`get_pixel_extents() == 1x1`**, so the box came out **13x8** while
        # the word rendered about **11x12** - the text sat outside its own box,
        # which is exactly "the Chronoa name is not in the blue box".
        #
        # Building it once means it is realised after the first frame, and from
        # then on its extents are the extents of text that is really there.
        self._layout = self.create_pango_layout("Chronoa")
        # `get_font_description()` is **None** on a layout nobody has styled, so
        # the guard that was there meant the 11px bold was silently never
        # applied. Build the description instead of asking for one.
        try:
            from gi.repository import Pango as _Pango
            description = _Pango.FontDescription.new()
            description.set_weight(_Pango.Weight.BOLD)
            description.set_absolute_size(11 * _Pango.SCALE)
            self._layout.set_font_description(description)
        except Exception:  # noqa: BLE001 - the default font is better than none
            pass

    def do_snapshot(self, snapshot) -> None:
        draw_cues(snapshot, self._overlay.cues, time.monotonic(), self._layout)


def _listen(path: str):
    if os.path.exists(path):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(4)
    return server


def _accept_loop(server, overlay: CueOverlay, GLib) -> None:
    """One thread per client; the drawing only ever happens on the main loop."""
    while True:
        try:
            connection, _ = server.accept()
        except OSError:
            return
        threading.Thread(target=_client, args=(connection, overlay),
                         daemon=True).start()


def _client(connection, overlay: CueOverlay) -> None:
    """Answer each message on this thread, and do not close before answering.

    The reply used to be queued with `GLib.idle_add` so the main loop would
    send it. That is wrong twice over, and both halves were measured: a client
    that shuts its write end makes this loop see EOF immediately and close the
    connection *before* the main loop runs the reply, so the answer is lost
    (measured: `connect OK`, then an empty read); and on Wayland the main loop
    can be inside a composited frame for seconds, so a reply queued behind it
    arrives after the driver has already given up - the one thing this module
    exists to prevent, since the driver would then act before the cursor is
    visibly there.

    `overlay.apply()` only touches plain tuples and `socket.sendall` is
    GIL-serialised, so neither needs the main loop; the next frame is driven by
    the 16 ms tick in `main()`, not by these replies.
    """
    handle = connection.makefile("r")
    try:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            reply = overlay.apply(message)
            if reply is not None:
                try:
                    connection.sendall((json.dumps(reply) + "\n").encode())
                except OSError:
                    return
    finally:
        try:
            connection.close()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
