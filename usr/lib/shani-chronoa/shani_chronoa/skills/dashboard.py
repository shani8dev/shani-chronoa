"""Skill: build a dashboard as one self-contained HTML page from data.

The page is `shani_chronoa.dashboard` - headline numbers, charts (drawn as inline
SVG by the chart engine) and tables, with its own light/dark styling and no
network or scripts. This is the boundary: resolve the output path, refuse to
overwrite a file already there, and read the page back to confirm it is real
HTML before reporting it.

Static on purpose: refreshing a dashboard is running this again with new numbers.
It opens in Chronoa's browser (or any browser) from the artifact row's Open.
"""

from __future__ import annotations

from pathlib import Path

from shani_chronoa import dashboard, files
from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "dashboard",
        "description": (
            "Build a dashboard as one self-contained HTML page (headline numbers, charts and tables), "
            "drawn on this computer with no install, no network and no scripts. Give any of: "
            "cards [{label, value, sub}], charts [{type: bar|line|pie, title, data: [[label, number], ...]}], "
            "tables [{title, rows: [[header...], [row...]]}], plus title and when (a 'as of ...' line). "
            "output is the .html path, e.g. '~/Documents/sales-dashboard.html'. Static: run again to refresh it."
        ),
        "parameters": {"type": "object", "properties": {
            "output": {"type": "string", "description": "The .html file to write."},
            "title": {"type": "string"},
            "when": {"type": "string", "description": "A subtitle like 'as of 22 Oct 2026'."},
            "cards": {"type": "array", "items": {"type": "object"},
                      "description": "Headline numbers: [{label, value, sub}]."},
            "charts": {"type": "array", "items": {"type": "object"},
                       "description": "[{type: bar|line|pie, title, data: [[label, number], ...]}]."},
            "tables": {"type": "array", "items": {"type": "object"},
                       "description": "[{title, rows: [[header...], ...]}]."},
        }, "required": ["output"]},
    },
}


def _run(arguments: dict) -> str:
    if not str(arguments.get("output") or "").strip():
        return "Give the output .html path."
    try:
        target = files.resolve_in_home(arguments["output"])
    except files.PathProblem as exc:
        return str(exc)
    if target.suffix.lower() not in (".html", ".htm"):
        target = target.with_suffix(".html")
    if target.exists():
        return f"{target} already exists; give another output."
    try:
        page = dashboard.build_html({
            "title": arguments.get("title") or target.stem,
            "when": arguments.get("when") or "",
            "cards": arguments.get("cards") or [],
            "charts": arguments.get("charts") or [],
            "tables": arguments.get("tables") or [],
        })
    except (ValueError, TypeError, KeyError) as exc:
        return f"Could not build it: {exc}"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8") as handle:
        handle.write(page)
    pieces = []
    for key in ("cards", "charts", "tables"):
        n = len(arguments.get(key) or [])
        if n:
            pieces.append(f"{n} {key[:-1] if n == 1 else key}")
    made = ", ".join(pieces) or "an empty page"
    return f"Saved a dashboard ({made}) to {target} ({len(page)} bytes of HTML)."


def _verify_dashboard(arguments: dict, tool=None):
    """Post-condition: the file exists and is a non-empty HTML page."""
    out = str(arguments.get("output") or "").strip()
    if not out:
        return None
    target = Path(out)
    if target.suffix.lower() not in (".html", ".htm"):
        target = target.with_suffix(".html")
    if not target.exists():
        return (False, f"{target.name} does not exist, so no dashboard was written")
    try:
        head = target.read_text(encoding="utf-8", errors="replace")[:200].lower()
    except OSError as exc:
        return (False, f"could not read {target.name}: {exc}")
    size = target.stat().st_size
    if size == 0:
        return (False, f"{target.name} is empty")
    if "<!doctype html" not in head and "<html" not in head:
        return (False, f"{target.name} is {size} bytes but does not start as an HTML page")
    return (True, f"{target.name} is an HTML dashboard, {size} bytes")


POST_CONDITION = _verify_dashboard

SKILLS = [Skill(name="dashboard", schema=SCHEMA, run=_run)]

#: The dashboard page is an artifact, so it gets a viewer row with Open/Preview.
DECLARES_ARTIFACTS = files.paths_in(r"^Saved a dashboard \(.*?\) to (.+?) \(")
