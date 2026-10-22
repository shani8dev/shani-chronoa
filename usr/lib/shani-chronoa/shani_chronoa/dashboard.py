"""A dashboard as one self-contained HTML page, built from data on this computer.

Adopted from harness-studio's dashboards - headline numbers, charts and tables
on a page - but kept to what a privacy-first assistant should own: a *static*
page written from the data it is given, with the charts drawn by
`shani_chronoa.charts` as inline SVG (no plotting library, nothing fetched, no
script runs). Refreshing it is re-running the skill with new numbers; a live,
self-refreshing version would hang off Chronoa's own `watch` triggers rather
than run scripts of its own, which is why that machinery is not reinvented here.

The page carries its own light/dark CSS and needs no network, so it opens the
same in Chronoa's browser pane, a real browser, or anywhere it is sent.
"""

from __future__ import annotations

import html
from typing import List

from shani_chronoa import charts


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _cards(cards: List[dict]) -> str:
    out = []
    for card in cards or []:
        label = _esc(card.get("label", ""))
        value = _esc(card.get("value", ""))
        sub = card.get("sub") or card.get("detail") or ""
        sub_el = f'<div class="sub">{_esc(sub)}</div>' if sub else ""
        out.append(f'<div class="card"><div class="value">{value}</div>'
                   f'<div class="label">{label}</div>{sub_el}</div>')
    return f'<section class="cards">{"".join(out)}</section>' if out else ""


def _chart_svg(spec: dict) -> str:
    """One chart spec -> inline SVG, via the standalone chart engine."""
    kind = str(spec.get("type") or spec.get("kind") or "bar").strip().lower()
    title = str(spec.get("title") or "")
    data = spec.get("data") or []
    if kind == "pie":
        return charts.pie_chart([(d[0], float(d[1])) for d in data], title=title)
    if kind == "line":
        if spec.get("series"):
            series = [(str(n), [(float(x), float(y)) for x, y in pts]) for n, pts in spec["series"].items()]
        else:
            series = [("", [(float(x), float(y)) for x, y in data])]
        return charts.line_chart(series, title=title,
                                 x_label=str(spec.get("x_label") or ""),
                                 y_label=str(spec.get("y_label") or ""))
    return charts.bar_chart([(d[0], float(d[1])) for d in data], title=title,
                            y_label=str(spec.get("y_label") or ""))


def _charts(specs: List[dict]) -> str:
    cells = []
    for spec in specs or []:
        try:
            svg = _chart_svg(spec)
        except (ValueError, TypeError, KeyError, IndexError):
            continue  # a malformed chart is dropped, not drawn wrong
        cells.append(f'<div class="chart">{svg}</div>')
    return f'<section class="charts">{"".join(cells)}</section>' if cells else ""


def _tables(tables: List[dict]) -> str:
    out = []
    for table in tables or []:
        rows = table.get("rows") or []
        if not rows:
            continue
        title = table.get("title") or ""
        head = "".join(f"<th>{_esc(c)}</th>" for c in rows[0])
        body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>"
                       for row in rows[1:])
        cap = f"<h2>{_esc(title)}</h2>" if title else ""
        out.append(f'<div class="table-wrap">{cap}<table><thead><tr>{head}</tr></thead>'
                   f'<tbody>{body}</tbody></table></div>')
    return f'<section class="tables">{"".join(out)}</section>' if out else ""


_CSS = """
:root { color-scheme: light dark;
  --bg:#f6f7f9; --fg:#1b1f24; --muted:#5b6673; --card:#ffffff; --line:#e3e7ec; --accent:#0072b2; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#13161a; --fg:#e8ebee; --muted:#9aa4b0; --card:#1c2127; --line:#2a313a; --accent:#56b4e9; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif; padding:24px; }
header h1 { margin:0 0 2px; font-size:22px; }
header .when { color:var(--muted); font-size:13px; margin-bottom:20px; }
.cards { display:flex; flex-wrap:wrap; gap:16px; margin-bottom:24px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:16px 20px; min-width:150px; flex:1 1 150px; }
.card .value { font-size:28px; font-weight:650; }
.card .label { color:var(--muted); font-size:13px; margin-top:4px; }
.card .sub { color:var(--accent); font-size:12px; margin-top:6px; }
.charts { display:flex; flex-wrap:wrap; gap:20px; margin-bottom:24px; }
.chart { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px; }
.chart svg { max-width:100%; height:auto; }
.tables { display:flex; flex-wrap:wrap; gap:20px; }
.table-wrap { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; }
.table-wrap h2 { font-size:15px; margin:4px 0 10px; }
table { border-collapse:collapse; font-size:13px; }
th,td { text-align:left; padding:6px 14px 6px 0; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-weight:600; }
"""


def build_html(spec: dict) -> str:
    """A full HTML dashboard page from a spec of cards, charts and tables."""
    title = str(spec.get("title") or "Dashboard")
    when = str(spec.get("when") or spec.get("subtitle") or "")
    when_el = f'<div class="when">{_esc(when)}</div>' if when else ""
    body = _cards(spec.get("cards")) + _charts(spec.get("charts")) + _tables(spec.get("tables"))
    if not body:
        raise ValueError("a dashboard needs at least one card, chart or table")
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{_esc(title)}</title><style>{_CSS}</style></head>'
            f'<body><header><h1>{_esc(title)}</h1>{when_el}</header>{body}</body></html>')
