"""Small charts for the panels: ring, bars, ranked bars, meter, line, stacked strip.

**Why one module.** The panels read real numbers - steps, battery wear, disk
use, how often each tool worked, bytes sent per host - and printed every one
of them as a sentence. The watch dashboard drew its own charts; these are those,
generalised, so any panel can show a number as a shape.

**Drawn with GTK's snapshot API (`Gsk.PathBuilder`), not cairo.** A cairo draw
function needs PyGObject's cairo converter, which a machine can lack - measured
on the dev box, every chart came out blank. GTK 4.14+ draws the same shapes
with nothing extra. **Text is real labels**, not drawn glyphs, so it follows the
theme, is selectable where it matters, and reaches a screen reader.

Colours come from the widget's own foreground for axes and tracks (so dark and
light themes both work) and from `PALETTE` for data.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional, Sequence, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

#: GNOME palette: green, blue, orange, purple, red, yellow, teal.
PALETTE = ((0.15, 0.64, 0.41), (0.21, 0.52, 0.89), (0.90, 0.38, 0.0), (0.57, 0.25, 0.67),
           (0.75, 0.11, 0.16), (0.96, 0.76, 0.27), (0.13, 0.55, 0.55))
GREEN, BLUE, ORANGE, PURPLE, RED, YELLOW, TEAL = PALETTE


def rgba(rgb, alpha: float = 1.0) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.red, colour.green, colour.blue, colour.alpha = (*rgb, alpha)
    return colour


def fg(widget: Gtk.Widget, alpha: float) -> Gdk.RGBA:
    c = widget.get_color()
    return rgba((c.red, c.green, c.blue), alpha)


def rect(snapshot, x, y, w, h, colour) -> None:
    snapshot.append_color(colour, Graphene.Rect().init(x, y, max(w, 0), max(h, 0)))


def rounded(snapshot, x, y, w, h, radius, colour) -> None:
    if w <= 0 or h <= 0:
        return
    r = min(radius, w / 2, h / 2)
    shape = Gsk.RoundedRect()
    shape.init_from_rect(Graphene.Rect().init(x, y, w, h), r)
    snapshot.push_rounded_clip(shape)
    snapshot.append_color(colour, Graphene.Rect().init(x, y, w, h))
    snapshot.pop()


def stroke(snapshot, path, width: float, colour, round_caps: bool = False) -> None:
    s = Gsk.Stroke.new(width)
    if round_caps:
        s.set_line_cap(Gsk.LineCap.ROUND)
        s.set_line_join(Gsk.LineJoin.ROUND)
    snapshot.append_stroke(path, s, colour)


def _label(text: str = "", *classes: str, xalign: float = 0.0) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign, wrap=True, max_width_chars=30)
    for css in classes:
        label.add_css_class(css)
    return label


def level_colour(fraction: float, high_is_bad: bool = True):
    """Green / yellow / red for a 0..1 reading - full disks bad, full battery good."""
    if high_is_bad:          # a disk filling up: fine until it is nearly full
        return GREEN if fraction < 0.7 else YELLOW if fraction < 0.9 else RED
    # A success rate or a charge: green only when it is actually good. The
    # flipped disk scale called 33% success green (measured on Learning).
    return GREEN if fraction >= 0.8 else YELLOW if fraction >= 0.5 else RED


# -- ring ----------------------------------------------------------------------

class _Arc(Gtk.Widget):
    def __init__(self, size: int, width: float) -> None:
        super().__init__()
        self.set_size_request(size, size)
        self.fraction, self.colour, self.width = 0.0, GREEN, width

    def do_snapshot(self, snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        r = min(w, h) / 2 - self.width / 2 - 2
        cx, cy = w / 2, h / 2
        track = Gsk.PathBuilder.new()
        track.add_circle(Graphene.Point().init(cx, cy), r)
        stroke(snapshot, track.to_path(), self.width, fg(self, 0.12))
        f = min(self.fraction, 1.0)
        if f <= 0:
            return
        arc = Gsk.PathBuilder.new()
        if f >= 0.999:
            arc.add_circle(Graphene.Point().init(cx, cy), r)
        else:
            angle = -math.pi / 2 + 2 * math.pi * f
            arc.move_to(cx, cy - r)
            arc.svg_arc_to(r, r, 0, f > 0.5, True, cx + r * math.cos(angle), cy + r * math.sin(angle))
        stroke(snapshot, arc.to_path(), self.width, rgba(self.colour), round_caps=True)


class Ring(Gtk.Overlay):
    """A progress ring with a number and a caption in the middle."""

    def __init__(self, size: int = 132, width: float = 10) -> None:
        super().__init__(halign=Gtk.Align.CENTER)
        self.arc = _Arc(size, width)
        self.set_child(self.arc)
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                         halign=Gtk.Align.CENTER)
        self.big = _label("-", "title-2" if size >= 110 else "title-4", xalign=0.5)
        self.small = _label("", "dim-label", "caption", xalign=0.5)
        middle.append(self.big)
        middle.append(self.small)
        self.add_overlay(middle)

    def set(self, fraction: float, big: str, small: str = "", colour=None) -> None:
        self.arc.fraction = max(0.0, fraction)
        if colour is not None:
            self.arc.colour = colour
        self.big.set_text(big)
        self.small.set_text(small)
        self.arc.queue_draw()


# -- vertical bars -------------------------------------------------------------

class _Columns(Gtk.Widget):
    def __init__(self, height: int) -> None:
        super().__init__(hexpand=True)
        self.set_size_request(-1, height)
        self.values: "list[float]" = []
        self.colour = GREEN
        self.highlight_last = True

    def do_snapshot(self, snapshot) -> None:
        if not self.values:
            return
        w, h = self.get_width(), self.get_height()
        top = max(self.values) or 1
        slot = w / len(self.values)
        for i, value in enumerate(self.values):
            bar = (h - 2) * value / top
            alpha = 1.0 if (not self.highlight_last or i == len(self.values) - 1) else 0.5
            rounded(snapshot, i * slot + slot * 0.2, h - bar, slot * 0.6, bar, 3,
                    rgba(self.colour, alpha))
        rect(snapshot, 0, h - 1, w, 1, fg(self, 0.15))


class Bars(Gtk.Box):
    """Labelled columns: values above, names below. For days, hours, tiers."""

    def __init__(self, height: int = 70, colour=GREEN, highlight_last: bool = True,
                 number=lambda v: f"{v:,.0f}") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.numbers = Gtk.Box(homogeneous=True)
        self.columns = _Columns(height)
        self.columns.colour, self.columns.highlight_last = colour, highlight_last
        self.names = Gtk.Box(homogeneous=True)
        self.number = number
        for box in (self.numbers, self.columns, self.names):
            self.append(box)

    def set(self, values: "Sequence[Tuple[str, float]]", show_numbers: bool = True) -> None:
        for box in (self.numbers, self.names):
            while box.get_first_child() is not None:
                box.remove(box.get_first_child())
        for label, value in values:
            if show_numbers:
                self.numbers.append(_label(self.number(value), "caption", xalign=0.5))
            self.names.append(_label(label, "dim-label", "caption", xalign=0.5))
        self.numbers.set_visible(show_numbers)
        self.columns.values = [v for _l, v in values]
        self.columns.queue_draw()


# -- horizontal meter and ranked bars -----------------------------------------

class _Track(Gtk.Widget):
    def __init__(self, height: int = 10) -> None:
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER)
        self.set_size_request(60, height)
        self.fraction, self.colour = 0.0, GREEN

    def do_snapshot(self, snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        rounded(snapshot, 0, 0, w, h, h / 2, fg(self, 0.12))
        rounded(snapshot, 0, 0, w * min(max(self.fraction, 0), 1), h, h / 2, rgba(self.colour))


class Meter(Gtk.Box):
    """`Title ............ 72%` with a rounded track under it."""

    def __init__(self, title: str = "", high_is_bad: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head = Gtk.Box(spacing=8)
        self.title = _label(title)
        self.title.set_hexpand(True)
        self.value = _label("", "dim-label", xalign=1.0)
        head.append(self.title)
        head.append(self.value)
        self.append(head)
        self.track = _Track()
        self.append(self.track)
        self.high_is_bad = high_is_bad

    def set(self, fraction: float, value_text: str, title: Optional[str] = None,
            colour=None) -> None:
        if title is not None:
            self.title.set_text(title)
        self.value.set_text(value_text)
        self.track.fraction = fraction
        self.track.colour = colour or level_colour(fraction, self.high_is_bad)
        self.track.queue_draw()


class HBars(Gtk.Grid):
    """Ranked horizontal bars - `name | ████████ | value` - for top-N lists."""

    def __init__(self, colour=BLUE, number=lambda v: f"{v:,.0f}") -> None:
        super().__init__(column_spacing=10, row_spacing=6, hexpand=True)
        self.colour, self.number = colour, number

    def set(self, rows: "Sequence[Tuple[str, float]]", top: Optional[float] = None,
            colours: "Optional[Sequence]" = None, values: "Optional[Sequence[str]]" = None) -> None:
        while self.get_first_child() is not None:
            self.remove(self.get_first_child())
        biggest = top or max((v for _n, v in rows), default=1) or 1
        for i, (name, value) in enumerate(rows):
            label = _label(name, xalign=0.0)
            label.set_max_width_chars(22)
            label.set_ellipsize(3)  # Pango.EllipsizeMode.END
            label.set_wrap(False)
            track = _Track(8)
            track.fraction = value / biggest
            track.colour = colours[i] if colours else self.colour
            shown = _label(values[i] if values else self.number(value), "dim-label", "caption",
                           xalign=1.0)
            self.attach(label, 0, i, 1, 1)
            self.attach(track, 1, i, 1, 1)
            self.attach(shown, 2, i, 1, 1)


# -- stacked strip and line ----------------------------------------------------

class StackedBar(Gtk.Widget):
    """Segments left to right - sleep stages through a night, a disk's parts."""

    def __init__(self, height: int = 26) -> None:
        super().__init__(hexpand=True)
        self.set_size_request(-1, height)
        self.segments: "list[tuple[object, float]]" = []

    def set(self, segments: "Sequence[Tuple[object, float]]") -> None:
        """`(rgb, amount)` pairs."""
        self.segments = [(c, a) for c, a in segments if a]
        self.queue_draw()

    def do_snapshot(self, snapshot) -> None:
        total = sum(a for _c, a in self.segments)
        if not total:
            return
        w, h = self.get_width(), self.get_height()
        x = 0.0
        for colour, amount in self.segments:
            seg = w * amount / total
            rect(snapshot, x, 0, max(seg - 1, 1), h, rgba(colour))
            x += seg


class Swatch(Gtk.Widget):
    def __init__(self, rgb) -> None:
        super().__init__(valign=Gtk.Align.CENTER)
        self.set_size_request(10, 10)
        self.rgb = rgb

    def do_snapshot(self, snapshot) -> None:
        rounded(snapshot, 0, 0, self.get_width(), self.get_height(), 2, rgba(self.rgb))


class Line(Gtk.Widget):
    """A line over x in [x0, x1], y in [y0, y1], with optional horizontal bands."""

    def __init__(self, height: int = 70, colour=ORANGE, y_range=(0.0, 100.0),
                 x_range=(0.0, 24.0), bands: Sequence[float] = ()) -> None:
        super().__init__(hexpand=True)
        self.set_size_request(-1, height)
        self.points: "list[tuple[float, float]]" = []
        self.colour, self.y_range, self.x_range, self.bands = colour, y_range, x_range, bands
        self.fill = True

    def set(self, points: "Sequence[Tuple[float, float]]") -> None:
        self.points = list(points)
        self.queue_draw()

    def set_times(self, readings: "Sequence[Tuple[datetime, float]]") -> None:
        """Readings across one day, x in hours."""
        self.set([(w.hour + w.minute / 60, v) for w, v in readings])

    def _xy(self, x, y, w, h):
        (x0, x1), (y0, y1) = self.x_range, self.y_range
        return (w * (x - x0) / ((x1 - x0) or 1),
                h - h * (y - y0) / ((y1 - y0) or 1))

    def do_snapshot(self, snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        for band in self.bands:
            _x, y = self._xy(self.x_range[0], band, w, h)
            rect(snapshot, 0, y, w, 1, fg(self, 0.12))
        rect(snapshot, 0, h - 1, w, 1, fg(self, 0.15))
        if not self.points:
            return
        pts = [self._xy(x, y, w, h) for x, y in self.points]
        if self.fill and len(pts) > 1:
            area = Gsk.PathBuilder.new()
            area.move_to(pts[0][0], h)
            for x, y in pts:
                area.line_to(x, y)
            area.line_to(pts[-1][0], h)
            area.close()
            snapshot.append_fill(area.to_path(), Gsk.FillRule.WINDING, rgba(self.colour, 0.18))
        line = Gsk.PathBuilder.new()
        for i, (x, y) in enumerate(pts):
            (line.move_to if i == 0 else line.line_to)(x, y)
        stroke(snapshot, line.to_path(), 2, rgba(self.colour), round_caps=True)
        x, y = pts[-1]
        dot = Gsk.PathBuilder.new()
        dot.add_circle(Graphene.Point().init(x, y), 3.5)
        snapshot.append_fill(dot.to_path(), Gsk.FillRule.WINDING, rgba(self.colour))


def stat(title: str, value: str, detail: str = "") -> Gtk.Box:
    """A big-number tile: `title` small above, `value` large, `detail` dim."""
    tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
    tile.add_css_class("card")
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                    margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
    inner.append(_label(title, "caption", "dim-label"))
    big = _label(value, "title-2")
    inner.append(big)
    if detail:
        inner.append(_label(detail, "caption", "dim-label"))
    tile.append(inner)
    tile.value_label = big
    return tile


def card(title: str, icon: str = "") -> "tuple[Gtk.Box, Gtk.Box]":
    """A padded card with a heading; returns (card, body)."""
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.START)
    outer.add_css_class("card")
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                    margin_top=12, margin_bottom=14, margin_start=14, margin_end=14)
    outer.append(inner)
    head = Gtk.Box(spacing=6)
    if icon:
        head.append(Gtk.Image(icon_name=icon))
    head.append(_label(title, "heading"))
    inner.append(head)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    inner.append(body)
    return outer, body
