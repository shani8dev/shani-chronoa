"""Skill: answer questions about a spreadsheet or CSV with read-only SQL, and chart it.

"Which month did I spend most?" over a bank export is a SQL question, and a
model doing that arithmetic in its head over pasted rows is how wrong totals
get said with confidence. So the file is loaded into an in-memory SQLite
database (standard library) as table `data` - one table per sheet for a
workbook - and the model writes the query. Harvested from agno's
`csv_toolkit` (DuckDB `read_csv` + query) and pydantic-ai's data-analyst
example, without DuckDB or pandas.

Read-only is enforced by SQLite itself, not by inspecting the SQL text: an
authorizer allows only SELECT, reads and functions, so `DROP`, `ATTACH`,
`PRAGMA` and writes are refused by the engine whatever the query looks like,
and a progress handler stops a runaway query. The source file is never
written. `chart` draws a bar, line or pie chart as an SVG file (no
matplotlib), and `save_as` writes a query's result to a new .csv or .xlsx.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import time
from pathlib import Path
from xml.sax.saxutils import escape

from shani_chronoa import files, office
from shani_chronoa.skills import Skill

MAX_ROWS_LOADED = 200_000
MAX_ROWS_SHOWN = 50
QUERY_SECONDS = 10
_ACTIONS = ("describe", "query", "chart")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "analyze_table",
        "description": (
            "Ask questions of a .csv, .tsv, .xlsx or .json (list of records) file with read-only SQLite SQL. "
            "describe: the tables, columns, types, row counts and a sample - do this first. query: run one "
            "SELECT (the table is `data`, or one table per sheet name for a workbook); save_as='~/x.csv' or "
            ".xlsx keeps the full result. chart: bar, line or pie chart saved as .svg from a SELECT returning "
            "(label, value) columns."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "path": {"type": "string"},
            "sql": {"type": "string"},
            "save_as": {"type": "string"},
            "kind": {"type": "string", "enum": ["bar", "line", "pie"]},
            "title": {"type": "string"},
            "output": {"type": "string", "description": "chart: where to save the .svg."},
        }, "required": ["action", "path"]},
    },
}


class TableError(Exception):
    pass


def _ident(name: str, used: set) -> str:
    clean = re.sub(r"\W+", "_", str(name).strip().lower()).strip("_") or "col"
    if clean[0].isdigit():
        clean = "c_" + clean
    base, n = clean, 2
    while clean in used:
        clean = f"{base}_{n}"
        n += 1
    used.add(clean)
    return clean


def _number(text: str):
    """int/float for '1200', '1,200.50', '₹ 900', '-3e2'; None for text and for codes like '007'."""
    t = re.sub(r"^(-?)[₹$€£]\s?", r"\1", text.strip())
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", t):
        t = t.replace(",", "")
    if re.fullmatch(r"-?0\d+", t):  # a code (007, 010), not a number
        return None
    if re.fullmatch(r"-?\d+", t) and not (len(t.lstrip("-")) > 1 and t.lstrip("-").startswith("0")):
        return int(t)
    if re.fullmatch(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", t):
        return float(t)
    return None


def _typed(rows: "list[list[str]]") -> "tuple[list[str], list[list]]":
    """Header plus rows; a column whose every non-empty value is numeric becomes numbers."""
    if not rows:
        raise TableError("the file has no rows")
    width = max(len(r) for r in rows)
    header = [h or f"column_{i + 1}" for i, h in enumerate(rows[0] + [""] * (width - len(rows[0])))]
    body = [r + [""] * (width - len(r)) for r in rows[1:]]
    numeric = []
    for c in range(width):
        vals = [r[c] for r in body if str(r[c]).strip() != ""]
        numeric.append(bool(vals) and all(_number(str(v)) is not None for v in vals))
    typed = [[(_number(str(v)) if numeric[c] and str(v).strip() else (None if str(v).strip() == "" else v))
              for c, v in enumerate(r)] for r in body]
    return header, typed


def load_tables(path: Path) -> "dict[str, tuple[list[str], list[list]]]":
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv", ".txt"):
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        sample = raw[:20000]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters="\t" if ext == ".tsv" else ",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if ext == ".tsv" else csv.excel
        rows = []
        for i, r in enumerate(csv.reader(raw.splitlines(), dialect)):
            if i > MAX_ROWS_LOADED:
                break
            rows.append(r)
        return {"data": _typed([r for r in rows if any(c.strip() for c in r)])}
    if ext == ".xlsx":
        sheets = office.read_xlsx_rows(path)
        tables = {}
        used: set = set()
        for i, (name, rows) in enumerate(sheets.items()):
            rows = [r for r in rows if any(str(c).strip() for c in r)]
            if rows:
                tables["data" if i == 0 else _ident(name, used | {"data"})] = _typed(rows[: MAX_ROWS_LOADED + 1])
        if not tables:
            raise TableError("the workbook's sheets are empty")
        return tables
    if ext == ".json":
        records = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = next((v for v in records.values() if isinstance(v, list)), [])
        if not isinstance(records, list) or not records or not all(isinstance(r, dict) for r in records):
            raise TableError("a .json file must hold a list of records ({...}, {...})")
        keys = list(dict.fromkeys(k for r in records for k in r))
        rows = [keys] + [["" if r.get(k) is None else (json.dumps(r[k]) if isinstance(r.get(k), (dict, list))
                                                        else str(r[k])) for k in keys] for r in records]
        return {"data": _typed(rows[: MAX_ROWS_LOADED + 1])}
    raise TableError(f"{ext or 'that'} is not a table file (.csv, .tsv, .xlsx, .json)")


def _authorizer(action, *_):
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    recursive = getattr(sqlite3, "SQLITE_RECURSIVE", None)
    if recursive is not None:
        allowed.add(recursive)
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def connect(tables) -> "tuple[sqlite3.Connection, dict]":
    db = sqlite3.connect(":memory:")
    columns = {}
    for name, (header, rows) in tables.items():
        used: set = set()
        cols = [_ident(h, used) for h in header]
        columns[name] = list(zip(cols, header))
        db.execute(f'CREATE TABLE "{name}" ({", ".join(chr(34) + c + chr(34) for c in cols)})')
        db.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(cols))})', rows)
    db.commit()
    db.set_authorizer(_authorizer)
    return db, columns


def run_query(db: sqlite3.Connection, sql: str) -> "tuple[list[str], list[tuple]]":
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        raise TableError("give the SELECT to run in sql")
    if ";" in sql:
        raise TableError("one statement at a time")
    deadline = time.monotonic() + QUERY_SECONDS
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = db.execute(sql)
        names = [d[0] for d in cur.description or []]
        return names, cur.fetchmany(MAX_ROWS_LOADED)
    except sqlite3.DatabaseError as exc:
        msg = str(exc)
        if "not authorized" in msg:
            msg = "only reading is allowed - this runs SELECT queries, it never changes data"
        elif "interrupted" in msg:
            msg = f"the query ran past {QUERY_SECONDS}s and was stopped"
        raise TableError(msg) from exc
    finally:
        db.set_progress_handler(None, 0)


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}" if abs(v) < 1e15 else repr(v)
    return str(v)


def _table_text(names, rows) -> str:
    shown = rows[:MAX_ROWS_SHOWN]
    out = ["| " + " | ".join(names) + " |"] + ["| " + " | ".join(_fmt(v) for v in r) + " |" for r in shown]
    if len(rows) > len(shown):
        out.append(f"... {len(rows) - len(shown)} more row(s); use save_as to keep them all, or LIMIT/aggregate.")
    return "\n".join(out)


def describe(tables, columns) -> str:
    out = []
    for name, (header, rows) in tables.items():
        out.append(f"Table {name}: {len(rows)} rows")
        for i, (col, original) in enumerate(columns[name]):
            vals = [r[i] for r in rows if r[i] is not None]
            if vals and all(isinstance(v, (int, float)) for v in vals):
                info = f"number, min {_fmt(min(vals))}, max {_fmt(max(vals))}, mean {_fmt(sum(vals) / len(vals))}"
            else:
                distinct = len(set(map(str, vals)))
                info = f"text, {distinct} distinct" + (f", e.g. {', '.join(sorted(set(map(str, vals)))[:3])}" if vals else "")
            label = f"{col}" + (f' (was "{original}")' if col != original else "")
            out.append(f"- {label}: {info}, {len(rows) - len(vals)} empty")
        out.append("Sample:\n" + _table_text([c for c, _ in columns[name]], [tuple(r) for r in rows[:5]]))
    return "\n".join(out)


# --- SVG charts ------------------------------------------------------------

_PALETTE = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#ca8a04", "#db2777")


def svg_chart(kind: str, labels: "list[str]", values: "list[float]", title: str = "") -> str:
    W, H, pad, top = 720, 420, 60, 50
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            'font-family="sans-serif" font-size="12"><rect width="100%" height="100%" fill="white"/>'
            f'<text x="{W / 2}" y="28" text-anchor="middle" font-size="16" font-weight="bold">{escape(title)}</text>')
    if kind == "pie":
        total = sum(v for v in values if v > 0)
        if total <= 0:
            raise TableError("a pie chart needs positive values")
        cx, cy, r, angle, parts = 260, 235, 160, -math.pi / 2, []
        for i, (lab, v) in enumerate(zip(labels, values)):
            if v <= 0:
                continue
            sweep = 2 * math.pi * v / total
            x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            x2, y2 = cx + r * math.cos(angle + sweep), cy + r * math.sin(angle + sweep)
            large = 1 if sweep > math.pi else 0
            colour = _PALETTE[i % len(_PALETTE)]
            path = (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{colour}"/>' if sweep >= 2 * math.pi - 1e-9 else
                    f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f} Z" fill="{colour}"/>')
            parts.append(path)
            ly = 80 + i * 22
            parts.append(f'<rect x="470" y="{ly - 11}" width="14" height="14" fill="{colour}"/>'
                         f'<text x="490" y="{ly}">{escape(lab)} ({v / total:.0%})</text>')
            angle += sweep
        return head + "".join(parts) + "</svg>"
    lo, hi = min(0.0, min(values)), max(0.0, max(values))
    span = (hi - lo) or 1.0
    plot_w, plot_h = W - 2 * pad, H - top - pad
    y_of = lambda v: top + plot_h * (hi - v) / span
    parts = [f'<line x1="{pad}" y1="{y_of(0):.1f}" x2="{W - pad}" y2="{y_of(0):.1f}" stroke="#555"/>',
             f'<line x1="{pad}" y1="{top}" x2="{pad}" y2="{top + plot_h}" stroke="#555"/>']
    for k in range(5):
        v = lo + span * k / 4
        parts.append(f'<text x="{pad - 6}" y="{y_of(v) + 4:.1f}" text-anchor="end">{escape(_fmt(round(v, 2)))}</text>'
                     f'<line x1="{pad}" y1="{y_of(v):.1f}" x2="{W - pad}" y2="{y_of(v):.1f}" stroke="#eee"/>')
    n = len(values)
    step = plot_w / max(n, 1)
    every = max(1, math.ceil(n / 20))
    if kind == "bar":
        for i, v in enumerate(values):
            x = pad + i * step + step * 0.15
            y0, y1 = sorted((y_of(0), y_of(v)))
            parts.append(f'<rect x="{x:.1f}" y="{y0:.1f}" width="{step * 0.7:.1f}" height="{max(y1 - y0, 0.5):.1f}" '
                         f'fill="{_PALETTE[0]}"><title>{escape(labels[i])}: {escape(_fmt(v))}</title></rect>')
    else:
        pts = " ".join(f"{pad + (i + 0.5) * step:.1f},{y_of(v):.1f}" for i, v in enumerate(values))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{_PALETTE[0]}" stroke-width="2"/>')
        parts += [f'<circle cx="{pad + (i + 0.5) * step:.1f}" cy="{y_of(v):.1f}" r="3" fill="{_PALETTE[0]}"/>'
                  for i, v in enumerate(values)]
    for i, lab in enumerate(labels):
        if i % every == 0:
            x = pad + (i + 0.5) * step
            parts.append(f'<text x="{x:.1f}" y="{top + plot_h + 16}" text-anchor="end" '
                         f'transform="rotate(-30 {x:.1f} {top + plot_h + 16})">{escape(lab[:18])}</text>')
    return head + "".join(parts) + "</svg>"


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    try:
        source = files.resolve(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    if not source.is_file():
        return f"{source} does not exist."
    try:
        tables = load_tables(source)
        db, columns = connect(tables)
        if action == "describe":
            return describe(tables, columns)
        names, rows = run_query(db, arguments.get("sql") or "")
        if action == "query":
            text = _table_text(names, rows) if rows else "The query returned no rows."
            save = (arguments.get("save_as") or "").strip()
            if save and rows:
                target = files.resolve_in_home(save)
                if target.exists():
                    return f"{text}\n\nNot saved: {target} already exists."
                if target.suffix.lower() == ".xlsx":
                    target.write_bytes(office.make_xlsx({"Result": [names] + [list(r) for r in rows]}))
                else:
                    with target.open("w", newline="", encoding="utf-8") as handle:
                        csv.writer(handle).writerows([names] + [list(r) for r in rows])
                text += f"\n\nSaved all {len(rows)} row(s) to {target}."
            return text
        # chart
        if len(names) < 2 or not rows:
            return "chart needs a SELECT returning a label column and a value column, with rows."
        if len(rows) > 200:
            return f"That is {len(rows)} points; aggregate or LIMIT it to 200 or fewer for a readable chart."
        try:
            values = [float(r[1]) for r in rows]
        except (TypeError, ValueError):
            return f"The second column ({names[1]}) must be numbers to chart."
        labels = [_fmt(r[0]) for r in rows]
        kind = (arguments.get("kind") or "bar").lower()
        title = arguments.get("title") or f"{names[1]} by {names[0]}"
        target = files.resolve_in_home(arguments.get("output") or f"~/Pictures/{re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-')[:40] or 'chart'}.svg")
        if target.suffix.lower() != ".svg":
            target = target.with_suffix(".svg")
        if target.exists():
            return f"{target} already exists; give another output."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(svg_chart(kind, labels, values, title), encoding="utf-8")
        return f"Saved a {kind} chart of {len(rows)} point(s) to {target}."
    except (TableError, office.OfficeError, files.PathProblem) as exc:
        return f"Could not do that: {exc}."
    except (UnicodeDecodeError, ValueError) as exc:
        return f"Could not read {source.name}: {exc}."


def _verify_chart(arguments: dict, tool=None):
    """Post-condition: for `chart`, is the SVG a real chart?

    `analyze_table`'s other actions are read-only SQL and return their answer as
    text, which needs no check. `chart` writes a file, and a chart that is a
    well-formed but empty SVG is a chart of nothing - so the check confirms the
    file holds actual drawn marks rather than only a header.

    Matched on the `output` the skill was given, and reported as UNVERIFIED
    rather than failed if the file merely has no bars in it, because an empty
    result set legitimately produces an empty chart.
    """
    action = str(arguments.get("action") or "").strip().lower()
    if action != "chart":
        return None  # a query returns its answer as text; nothing to check
    from pathlib import Path as _P
    out = str(arguments.get("output") or "").strip()
    if not out:
        return None
    target = _P(out)
    if not target.exists():
        return (False, f"{target.name} does not exist, so no chart was written")
    size = target.stat().st_size
    if size == 0:
        return (False, f"{target.name} is empty")
    try:
        body = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (False, f"could not read {target.name}: {exc}")
    if "<svg" not in body:
        return (False, f"{target.name} is {size} bytes but holds no <svg> element")
    marks = sum(body.count(tag) for tag in ("<rect", "<path", "<circle", "<line"))
    if marks == 0:
        return (True, f"{target.name} is a valid but empty SVG ({size} bytes, "
                      f"no drawn marks - consistent with an empty result set)")
    return (True, f"{target.name} is a valid SVG of {size} bytes holding {marks} "
                  f"drawn mark(s)")


POST_CONDITION = _verify_chart

SKILLS = [Skill(name="analyze_table", schema=SCHEMA, run=_run)]
