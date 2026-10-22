"""A dashboard as one self-contained HTML page, built from data.

Harvested from harness-studio's dashboards, kept static and script-free: the
charts are inline SVG from our own engine, the page carries its own styling and
fetches nothing. The point these tests hold: the numbers, charts and tables that
go in come out in the page; a malformed chart is dropped, not drawn wrong; and
the skill writes a real HTML file, refuses to overwrite, and declares it so the
viewer can show it.
"""
import re
import sys
import xml.dom.minidom as minidom
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import dashboard  # noqa: E402

SPEC = {
    "title": "Sales", "when": "as of 22 Oct 2026",
    "cards": [{"label": "Revenue", "value": "$1.2M", "sub": "+8%"}, {"label": "Orders", "value": "3,401"}],
    "charts": [{"type": "bar", "title": "By month", "data": [["Jan", 30], ["Feb", 45]]},
               {"type": "pie", "title": "Share", "data": [["A", 50], ["B", 50]]}],
    "tables": [{"title": "Top", "rows": [["Item", "Sold"], ["Train", 120]]}],
}


def test_the_page_holds_the_numbers_charts_and_tables_given():
    page = dashboard.build_html(SPEC)
    assert page.lower().startswith("<!doctype html")
    assert "Revenue" in page and "$1.2M" in page and "+8%" in page
    assert "By month" in page and page.count("<svg") == 2
    assert "<table>" in page and "Train" in page and "120" in page
    for svg in re.findall(r"<svg.*?</svg>", page, re.S):
        minidom.parseString(svg)  # each chart is well-formed SVG


def test_it_carries_its_own_styling_and_no_script_or_network():
    page = dashboard.build_html(SPEC)
    assert "<style>" in page and "prefers-color-scheme" in page
    assert "<script" not in page.lower(), "a dashboard that runs scripts is not what this is"
    # Nothing is fetched: no external resources (the only http(s) is the SVG
    # xmlns declaration, which is an identifier, not a request).
    assert "src=" not in page and 'href="http' not in page and "@import" not in page
    assert "fetch(" not in page and "XMLHttpRequest" not in page


def test_a_malformed_chart_is_dropped_not_drawn_wrong():
    page = dashboard.build_html({"charts": [
        {"type": "bar", "title": "good", "data": [["a", 1]]},
        {"type": "bar", "title": "bad", "data": [["a"]]},          # missing value
        {"type": "pie", "title": "empty", "data": []},             # nothing to draw
    ]})
    assert page.count("<svg") == 1, "only the good chart survives"


def test_text_is_escaped():
    page = dashboard.build_html({"cards": [{"label": "<b>&", "value": "x"}]})
    assert "<b>&amp;" not in page or "&lt;b&gt;&amp;" in page
    assert "&lt;b&gt;" in page


def test_an_empty_dashboard_is_refused():
    with pytest.raises(ValueError):
        dashboard.build_html({"title": "nothing"})


# --- the skill around it ----------------------------------------------------


def test_the_skill_writes_verifies_declares_and_guards(tmp_path, monkeypatch):
    from shani_chronoa import tools
    from shani_chronoa.skills import dashboard as dskill
    monkeypatch.setenv("HOME", str(tmp_path))
    out = str(tmp_path / "reports" / "sales.html")
    said = dskill._run({"output": out, **SPEC})
    assert "Saved a dashboard" in said and "2 cards" in said, said
    ok, detail = dskill._verify_dashboard({"output": out})
    assert ok and "HTML dashboard" in detail, detail
    # declared as an artifact so the viewer can show it
    assert any(str(p) == str(Path(out)) for p in tools.artifacts_for("dashboard", said))
    # never overwrites, and an empty spec is a message not a file
    assert "already exists" in dskill._run({"output": out, "cards": [{"label": "x", "value": "1"}]})
    assert "Could not build it" in dskill._run({"output": str(tmp_path / "e.html")})


def test_the_skill_is_offered(monkeypatch):
    from shani_chronoa import tools
    assert any(e["function"]["name"] == "dashboard" for e in tools.TOOLS), "dashboard is not registered"
