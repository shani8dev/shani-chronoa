"""Charts, icons and diagrams as SVG, drawn from data with the layout computed.

No plotting library and no install: every coordinate here is worked out from the
numbers (nice axis ticks at 1/2/5 x a power of ten, linear scales inside fixed
margins), so the picture is the data and not an eyeballed sketch. The approach is
adopted from harness-studio's charts-and-diagrams skill - compute the layout,
label everything a reader needs, use colour-blind-safe hues, and round
coordinates - reimplemented in Python with no dependency.

The three makers return an SVG string with an explicit `viewBox`, real `<text>`
elements in a system font stack, and `text-anchor` for alignment, so the file
opens the same in a browser, a slide, or the Files pane. `shani_chronoa.office`
embeds the same chart data as a *native* chart when the target is a workbook or
deck; this module is the standalone-file half.
"""

from __future__ import annotations

import html
import math
from typing import List, Optional, Sequence, Tuple

#: Okabe-Ito, the eight-colour set designed to stay distinct for the common
#: forms of colour blindness. One series uses the first; several cycle through.
PALETTE = ("#0072b2", "#e69f00", "#009e73", "#d55e00",
           "#cc79a7", "#56b4e9", "#f0e442", "#000000")

#: A system font stack, so no font file has to travel with the SVG.
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"

Point = Tuple[float, float]


def _esc(text: object) -> str:
    """Text safe to drop between SVG tags (and inside attributes)."""
    return html.escape(str(text), quote=True)


def _num(value: float) -> str:
    """A coordinate rounded to one decimal place, without a trailing '.0'."""
    r = round(float(value), 1)
    return str(int(r)) if r == int(r) else str(r)


def nice_ticks(lo: float, hi: float, target: int = 5,
               include_zero: bool = True) -> List[float]:
    """4-6 round tick values spanning [lo, hi], at 1/2/5 times a power of ten.

    Bars read from zero, so `include_zero` folds zero into the range. A flat
    series (lo == hi) still gets a sane pair of ticks rather than a divide by
    zero.
    """
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi == lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(target, 1)
    power = math.floor(math.log10(raw)) if raw > 0 else 0
    base = raw / (10 ** power)
    step = (1 if base <= 1 else 2 if base <= 2 else 5 if base <= 5 else 10) * (10 ** power)
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    ticks, value, guard = [], start, 0
    while value <= end + step / 2 and guard < 1000:
        # Snap -0.0 and float fuzz to a clean number for the label.
        ticks.append(round(value, 10) + 0.0)
        value += step
        guard += 1
    return ticks


def _fmt_tick(value: float) -> str:
    """A tick label: integers plain, fractions to a short decimal."""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _frame(width: int, height: int, title: str, body: str) -> str:
    title_el = (f'<text x="{width/2:.0f}" y="24" text-anchor="middle" '
                f'font-size="16" font-weight="600" fill="#111">{_esc(title)}</text>'
                if title else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" font-family="{FONT}">'
            f'<rect width="{width}" height="{height}" fill="#ffffff"/>'
            f'{title_el}{body}</svg>')


def bar_chart(data: Sequence[Tuple[str, float]], title: str = "",
              y_label: str = "", width: int = 640, height: int = 400) -> str:
    """A vertical bar chart. `data` is (label, value) pairs; values may be negative."""
    if not data:
        raise ValueError("a bar chart needs at least one bar")
    labels = [str(l) for l, _ in data]
    values = [float(v) for _, v in data]
    left, right, top, bottom = 64, 24, 48 if title else 24, 56
    plot_w, plot_h = width - left - right, height - top - bottom
    ticks = nice_ticks(min(values), max(values), include_zero=True)
    lo, hi = ticks[0], ticks[-1]
    span = hi - lo or 1.0

    def y_of(v: float) -> float:
        return top + plot_h * (1 - (v - lo) / span)

    parts = []
    # y grid + tick labels, and axis titles
    for t in ticks:
        y = y_of(t)
        parts.append(f'<line x1="{left}" y1="{_num(y)}" x2="{left+plot_w}" y2="{_num(y)}" '
                     f'stroke="#e6e6e6"/>')
        parts.append(f'<text x="{left-8}" y="{_num(y+4)}" text-anchor="end" '
                     f'font-size="12" fill="#444">{_esc(_fmt_tick(t))}</text>')
    if y_label:
        parts.append(f'<text x="16" y="{top+plot_h/2:.0f}" text-anchor="middle" font-size="12" '
                     f'fill="#444" transform="rotate(-90 16 {top+plot_h/2:.0f})">{_esc(y_label)}</text>')
    zero_y = y_of(0.0)
    slot = plot_w / len(data)
    bar_w = slot * 0.62
    show_values = len(data) <= 12
    for i, (label, value) in enumerate(zip(labels, values)):
        cx = left + slot * (i + 0.5)
        y = y_of(value)
        bar_top, bar_h = min(y, zero_y), abs(y - zero_y)
        color = PALETTE[0]
        parts.append(f'<rect x="{_num(cx-bar_w/2)}" y="{_num(bar_top)}" width="{_num(bar_w)}" '
                     f'height="{_num(bar_h)}" fill="{color}"/>')
        parts.append(f'<text x="{_num(cx)}" y="{height-bottom+18}" text-anchor="middle" '
                     f'font-size="12" fill="#444">{_esc(label)}</text>')
        if show_values:
            vy = bar_top - 5 if value >= 0 else bar_top + bar_h + 14
            parts.append(f'<text x="{_num(cx)}" y="{_num(vy)}" text-anchor="middle" '
                         f'font-size="11" fill="#111">{_esc(_fmt_tick(value))}</text>')
    parts.append(f'<line x1="{left}" y1="{_num(zero_y)}" x2="{left+plot_w}" y2="{_num(zero_y)}" '
                 f'stroke="#888"/>')
    return _frame(width, height, title, "".join(parts))


def line_chart(series: Sequence[Tuple[str, Sequence[Tuple[float, float]]]],
               title: str = "", x_label: str = "", y_label: str = "",
               width: int = 640, height: int = 400) -> str:
    """One or more labelled lines. `series` is (name, [(x, y), ...]) pairs."""
    pts = [(name, [(float(x), float(y)) for x, y in points]) for name, points in series]
    pts = [(n, p) for n, p in pts if p]
    if not pts:
        raise ValueError("a line chart needs at least one point")
    xs = [x for _, p in pts for x, _ in p]
    ys = [y for _, p in pts for _, y in p]
    left, right, top, bottom = 64, 24, 48 if title else 24, 56
    legend_h = 24 if len(pts) > 1 else 0
    plot_w, plot_h = width - left - right, height - top - bottom - legend_h
    yt = nice_ticks(min(ys), max(ys), include_zero=False)
    ylo, yhi = yt[0], yt[-1]
    xlo, xhi = min(xs), max(xs)
    xspan, yspan = (xhi - xlo) or 1.0, (yhi - ylo) or 1.0

    def X(x: float) -> float:
        return left + plot_w * (x - xlo) / xspan

    def Y(y: float) -> float:
        return top + plot_h * (1 - (y - ylo) / yspan)

    parts = []
    for t in yt:
        y = Y(t)
        parts.append(f'<line x1="{left}" y1="{_num(y)}" x2="{left+plot_w}" y2="{_num(y)}" stroke="#e6e6e6"/>')
        parts.append(f'<text x="{left-8}" y="{_num(y+4)}" text-anchor="end" font-size="12" '
                     f'fill="#444">{_esc(_fmt_tick(t))}</text>')
    for t in nice_ticks(xlo, xhi, include_zero=False):
        if t < xlo or t > xhi:
            continue
        x = X(t)
        parts.append(f'<text x="{_num(x)}" y="{height-bottom-legend_h+18}" text-anchor="middle" '
                     f'font-size="12" fill="#444">{_esc(_fmt_tick(t))}</text>')
    if y_label:
        parts.append(f'<text x="16" y="{top+plot_h/2:.0f}" text-anchor="middle" font-size="12" fill="#444" '
                     f'transform="rotate(-90 16 {top+plot_h/2:.0f})">{_esc(y_label)}</text>')
    if x_label:
        parts.append(f'<text x="{left+plot_w/2:.0f}" y="{height-legend_h-6}" text-anchor="middle" '
                     f'font-size="12" fill="#444">{_esc(x_label)}</text>')
    for i, (name, points) in enumerate(pts):
        color = PALETTE[i % len(PALETTE)]
        d = " ".join(f'{"M" if j == 0 else "L"}{_num(X(x))} {_num(Y(y))}' for j, (x, y) in enumerate(points))
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        for x, y in points:
            parts.append(f'<circle cx="{_num(X(x))}" cy="{_num(Y(y))}" r="2.5" fill="{color}"/>')
        if len(pts) > 1:
            lx = left + i * 140
            parts.append(f'<rect x="{lx}" y="{height-16}" width="12" height="12" fill="{color}"/>')
            parts.append(f'<text x="{lx+16}" y="{height-6}" font-size="12" fill="#444">{_esc(name)}</text>')
    return _frame(width, height, title, "".join(parts))


def pie_chart(data: Sequence[Tuple[str, float]], title: str = "",
              width: int = 480, height: int = 400) -> str:
    """A pie chart. Negative or zero slices are dropped, with their labels."""
    slices = [(str(l), float(v)) for l, v in data if float(v) > 0]
    if not slices:
        raise ValueError("a pie chart needs at least one positive value")
    total = sum(v for _, v in slices)
    cx, cy = width * 0.36, height * 0.54
    r = min(cx, cy - (24 if title else 0)) - 24
    parts, angle = [], -math.pi / 2  # start at twelve o'clock
    for i, (label, value) in enumerate(slices):
        frac = value / total
        end = angle + frac * 2 * math.pi
        x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
        large = 1 if frac > 0.5 else 0
        color = PALETTE[i % len(PALETTE)]
        if frac >= 0.999:  # a single full slice is a circle, not an arc
            parts.append(f'<circle cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(r)}" fill="{color}"/>')
        else:
            parts.append(f'<path d="M{_num(cx)} {_num(cy)} L{_num(x1)} {_num(y1)} '
                         f'A{_num(r)} {_num(r)} 0 {large} 1 {_num(x2)} {_num(y2)} Z" fill="{color}"/>')
        ly = height * 0.2 + i * 22
        parts.append(f'<rect x="{width*0.72:.0f}" y="{_num(ly)}" width="12" height="12" fill="{color}"/>')
        parts.append(f'<text x="{width*0.72+16:.0f}" y="{_num(ly+10)}" font-size="12" fill="#444">'
                     f'{_esc(label)} ({frac*100:.0f}%)</text>')
        angle = end
    return _frame(width, height, title, "".join(parts))


#: A small stroke-icon set on a 24x24 grid: fill none, stroke currentColor, so a
#: page or app can recolour them. Paths are drawn once and reused.
_ICONS = {
    "calendar": '<rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9h18M8 2.5v4M16 2.5v4"/>',
    "check": '<path d="M4 12.5l5 5 11-11"/>',
    "close": '<path d="M5 5l14 14M19 5L5 19"/>',
    "arrow-right": '<path d="M4 12h15M13 6l6 6-6 6"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "home": '<path d="M3 11l9-7 9 7M6 10v9h12v-9"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 4-6 8-6s8 2 8 6"/>',
    "folder": '<path d="M3 6a1 1 0 011-1h5l2 2h9a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1z"/>',
    "star": '<path d="M12 3l2.9 5.9 6.1.9-4.5 4.4 1.1 6.1-5.6-2.9-5.6 2.9 1.1-6.1L3 9.8l6.1-.9z"/>',
    "heart": '<path d="M12 20s-7-4.6-7-10a4 4 0 017-2.6A4 4 0 0119 10c0 5.4-7 10-7 10z"/>',
    "settings": '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v3M12 18v3M4.2 7l2.6 1.5M17.2 15.5l2.6 1.5M4.2 17l2.6-1.5M17.2 8.5l2.6-1.5"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
    "bell": '<path d="M6 9a6 6 0 0112 0c0 5 2 6 2 6H4s2-1 2-6M10 20a2 2 0 004 0"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
}


def icon_names() -> List[str]:
    return sorted(_ICONS)


def icon(name: str, size: int = 24, stroke: str = "currentColor") -> str:
    """One stroke icon on a 24x24 viewBox, scaled to `size`."""
    key = str(name).strip().lower()
    if key not in _ICONS:
        raise ValueError(f"no icon named {name!r}; have: {', '.join(icon_names())}")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'width="{size}" height="{size}" fill="none" stroke="{stroke}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{_ICONS[key]}</svg>')


def diagram(steps: Sequence[str], title: str = "", width: int = 520,
            arrows: Optional[Sequence[Tuple[int, int, str]]] = None) -> str:
    """A top-to-bottom flow of labelled boxes joined by arrows.

    `steps` are the box labels. `arrows` defaults to a straight chain
    (0->1->2...), or give (from, to, label) triples for a custom flow.
    """
    boxes = [str(s) for s in steps]
    if not boxes:
        raise ValueError("a diagram needs at least one box")
    if arrows is None:
        arrows = [(i, i + 1, "") for i in range(len(boxes) - 1)]
    box_w, box_h, gap = 240, 56, 40
    top = 48 if title else 24
    height = top + len(boxes) * box_h + (len(boxes) - 1) * gap + 24
    cx = width / 2
    defs = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0 0L10 5L0 10z" fill="#555"/></marker></defs>')

    def box_top(i: int) -> float:
        return top + i * (box_h + gap)

    parts = [defs]
    for a, b, label in arrows:
        if not (0 <= a < len(boxes) and 0 <= b < len(boxes)):
            continue
        y1 = box_top(a) + box_h if b > a else box_top(a)
        y2 = box_top(b) if b > a else box_top(b) + box_h
        parts.append(f'<line x1="{_num(cx)}" y1="{_num(y1)}" x2="{_num(cx)}" y2="{_num(y2-2)}" '
                     f'stroke="#555" stroke-width="1.5" marker-end="url(#ah)"/>')
        if label:
            parts.append(f'<text x="{_num(cx+8)}" y="{_num((y1+y2)/2+4)}" font-size="11" '
                         f'fill="#555">{_esc(label)}</text>')
    for i, label in enumerate(boxes):
        y = box_top(i)
        color = PALETTE[i % len(PALETTE)]
        parts.append(f'<rect x="{_num(cx-box_w/2)}" y="{_num(y)}" width="{box_w}" height="{box_h}" '
                     f'rx="8" fill="#f5f8fc" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<text x="{_num(cx)}" y="{_num(y+box_h/2+5)}" text-anchor="middle" '
                     f'font-size="14" fill="#111">{_esc(label)}</text>')
    return _frame(width, int(height), title, "".join(parts))
