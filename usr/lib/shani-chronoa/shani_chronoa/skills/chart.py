"""Skill: make a chart, an icon or a diagram as an SVG file, drawn from data.

The drawing is `shani_chronoa.charts` - layout computed from the numbers, no
plotting library, nothing to install. This is the boundary: resolve the output
path, refuse to overwrite a file that is already there (as `write_text_file`
does), and read the SVG back to confirm it is well-formed before reporting it.

A chart that belongs *inside* a workbook or deck is a native chart instead - ask
`office_document` to create the .xlsx/.pptx with a chart; this skill is for a
standalone .svg the user opens, embeds in a page, or hands on.
"""

from __future__ import annotations

import xml.dom.minidom as minidom
from pathlib import Path

from shani_chronoa import charts, files
from shani_chronoa.skills import Skill

_ACTIONS = ("chart", "icon", "diagram")
_KINDS = ("bar", "line", "pie")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "chart",
        "description": (
            "Make an SVG file from data, drawn on this computer with no install. "
            "chart: kind=bar|line|pie with data (bar/pie: [[label, number], ...]; line: [[x, y], ...], "
            "or several lines as series {name: [[x, y], ...]}), plus title, x_label, y_label. "
            "icon: a stroke icon by name (" + ", ".join(charts.icon_names()) + "). "
            "diagram: a top-to-bottom flow, steps=[\"Read\", \"Edit\", \"Test\"] with optional arrows "
            "[[from, to, label], ...]. output is the .svg path, e.g. '~/Pictures/sales.svg'."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "output": {"type": "string", "description": "The .svg file to write, e.g. '~/Pictures/chart.svg'."},
            "kind": {"type": "string", "enum": list(_KINDS), "description": "chart: bar, line or pie."},
            "data": {"type": "array", "description": "chart: [[label, number], ...] or [[x, y], ...].",
                     "items": {"type": "array"}},
            "series": {"type": "object", "description": "line chart with several lines: {name: [[x, y], ...]}."},
            "steps": {"type": "array", "items": {"type": "string"}, "description": "diagram: the box labels, in order."},
            "arrows": {"type": "array", "items": {"type": "array"},
                       "description": "diagram (optional): [[from_index, to_index, label], ...]; default is a straight chain."},
            "name": {"type": "string", "description": "icon: the icon name."},
            "title": {"type": "string"},
            "x_label": {"type": "string"},
            "y_label": {"type": "string"},
        }, "required": ["action", "output"]},
    },
}


def _num(value) -> float:
    return float(str(value).strip())


def _pairs(data, want_numeric_label: bool):
    """[(label_or_x, value), ...] from the model's `data`, numbers coerced."""
    out = []
    for row in data or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise ValueError("each data row is a pair, like [\"Jan\", 30] or [1, 25]")
        first = _num(row[0]) if want_numeric_label else str(row[0])
        out.append((first, _num(row[1])))
    if not out:
        raise ValueError("data is empty")
    return out


def _svg(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    title = str(arguments.get("title") or "")
    if action == "icon":
        return charts.icon(arguments.get("name") or "")
    if action == "diagram":
        steps = [str(s) for s in (arguments.get("steps") or [])]
        arrows = None
        if arguments.get("arrows"):
            arrows = [(int(a[0]), int(a[1]), str(a[2]) if len(a) > 2 else "")
                      for a in arguments["arrows"] if isinstance(a, (list, tuple)) and len(a) >= 2]
        return charts.diagram(steps, title=title, arrows=arrows)
    # chart
    kind = (arguments.get("kind") or "bar").strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {', '.join(_KINDS)}")
    x_label, y_label = str(arguments.get("x_label") or ""), str(arguments.get("y_label") or "")
    if kind == "bar":
        return charts.bar_chart(_pairs(arguments.get("data"), False), title=title, y_label=y_label)
    if kind == "pie":
        return charts.pie_chart(_pairs(arguments.get("data"), False), title=title)
    # line: one series from `data`, or several from `series`
    if arguments.get("series"):
        series = [(str(name), _pairs(points, True)) for name, points in arguments["series"].items()]
    else:
        series = [("", _pairs(arguments.get("data"), True))]
    return charts.line_chart(series, title=title, x_label=x_label, y_label=y_label)


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    try:
        target = files.resolve_in_home(arguments.get("output") or "")
    except files.PathProblem as exc:
        return str(exc)
    if not str(arguments.get("output") or "").strip():
        return "Give the output .svg path."
    if target.suffix.lower() != ".svg":
        target = target.with_suffix(".svg")
    if target.exists():
        return f"{target} already exists; give another output."
    try:
        svg = _svg(arguments)
    except (ValueError, TypeError, KeyError) as exc:
        return f"Could not make it: {exc}"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8") as handle:
        handle.write(svg)
    return f"Saved {action} to {target} ({len(svg)} bytes of SVG)."


def _verify_chart(arguments: dict, tool=None):
    """Post-condition: the file exists and is well-formed SVG, not an empty stub."""
    out = str(arguments.get("output") or "").strip()
    if not out:
        return None
    target = Path(out)
    if target.suffix.lower() != ".svg":
        target = target.with_suffix(".svg")
    if not target.exists():
        return (False, f"{target.name} does not exist, so nothing was drawn")
    size = target.stat().st_size
    if size == 0:
        return (False, f"{target.name} is empty")
    try:
        doc = minidom.parse(str(target))
    except Exception as exc:  # noqa: BLE001 - a parse failure is the finding
        return (False, f"{target.name} is {size} bytes but is not well-formed SVG: {type(exc).__name__}")
    if doc.documentElement.tagName != "svg":
        return (False, f"{target.name} parses but its root is <{doc.documentElement.tagName}>, not <svg>")
    return (True, f"{target.name} is well-formed SVG, {size} bytes")


POST_CONDITION = _verify_chart

SKILLS = [Skill(name="chart", schema=SCHEMA, run=_run)]

#: This skill's own declaration of what it wrote, read out of the success line.
DECLARES_ARTIFACTS = files.paths_in(r"^Saved \w+ to (.+?) \(")
