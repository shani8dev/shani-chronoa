"""Charts, icons and diagrams as SVG: the layout is computed from the data.

Harvested from harness-studio's charts-and-diagrams skill, reimplemented with no
plotting library. The point these tests hold: every bar height and tick comes
from the numbers (not an eyeballed sketch), the file written is well-formed SVG,
and the skill refuses junk and never overwrites.
"""
import sys
import xml.dom.minidom as minidom
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import charts  # noqa: E402


def test_nice_ticks_are_round_and_frame_the_data():
    assert charts.nice_ticks(0, 95) == [0.0, 20.0, 40.0, 60.0, 80.0, 100.0]
    assert charts.nice_ticks(3, 7)[0] == 0.0, "a bar chart's ticks include zero"
    neg = charts.nice_ticks(-3, 5)
    assert neg[0] <= -3 and neg[-1] >= 5 and 0.0 in neg
    flat = charts.nice_ticks(5, 5)  # a flat series must not divide by zero
    assert flat[0] == 0.0 and flat[-1] >= 5


def test_every_maker_produces_well_formed_svg_rooted_at_svg():
    svgs = {
        "bar": charts.bar_chart([("Jan", 30), ("Feb", 45), ("Mar", -8)], title="Sales", y_label="units"),
        "line": charts.line_chart([("rev", [(1, 10), (2, 25), (3, 18)])], title="Trend", x_label="q", y_label="k"),
        "line2": charts.line_chart([("a", [(1, 1), (2, 3)]), ("b", [(1, 2), (2, 1)])]),
        "pie": charts.pie_chart([("A", 50), ("B", 30), ("C", 20)], title="Share"),
        "icon": charts.icon("calendar"),
        "diagram": charts.diagram(["Read", "Edit", "Test"], title="Loop"),
    }
    for name, svg in svgs.items():
        doc = minidom.parseString(svg)
        assert doc.documentElement.tagName == "svg", name
        assert "viewBox" in svg, name
        if name != "icon":  # icons carry no text, so no font stack
            assert "font-family" in svg, name


def test_a_bars_height_tracks_its_value():
    """The taller value draws the shorter y (SVG y grows downward); a fixed check
    that the geometry follows the data rather than the order."""
    svg = charts.bar_chart([("small", 10), ("big", 100)], y_label="n")
    rects = minidom.parseString(svg).getElementsByTagName("rect")
    bars = [r for r in rects if r.getAttribute("fill") == charts.PALETTE[0]]
    assert len(bars) == 2
    small_y = float(bars[0].getAttribute("y"))
    big_y = float(bars[1].getAttribute("y"))
    assert big_y < small_y, "the bigger value's bar must start higher up"


def test_text_is_escaped_against_a_label_with_markup():
    svg = charts.bar_chart([("<b>&x", 5)], title="a<b>&c")
    assert "<b>&x" not in svg and "&lt;b&gt;&amp;x" in svg
    minidom.parseString(svg)  # still well-formed with the nasty label


def test_bad_inputs_are_refused_not_drawn_wrong():
    for call in (lambda: charts.bar_chart([]),
                 lambda: charts.pie_chart([("A", 0), ("B", -1)]),
                 lambda: charts.line_chart([]),
                 lambda: charts.icon("no-such-icon"),
                 lambda: charts.diagram([])):
        with pytest.raises(ValueError):
            call()


# --- the skill around the drawing -------------------------------------------


def test_the_skill_writes_verifies_and_guards(tmp_path, monkeypatch):
    from shani_chronoa.skills import chart
    monkeypatch.setenv("HOME", str(tmp_path))
    out = str(tmp_path / "pics" / "sales.svg")
    said = chart._run({"action": "chart", "kind": "bar",
                       "data": [["Jan", 30], ["Feb", "45"]], "title": "Sales", "output": out})
    assert "Saved chart to" in said, said
    ok, detail = chart._verify_chart({"action": "chart", "output": out})
    assert ok and "well-formed SVG" in detail, detail
    # never overwrites
    assert "already exists" in chart._run({"action": "chart", "kind": "bar",
                                           "data": [["x", 1]], "output": out})
    # junk data is a message, not a broken file
    bad = str(tmp_path / "bad.svg")
    assert "Could not make it" in chart._run({"action": "chart", "kind": "bar",
                                              "data": [["x"]], "output": bad})
    assert not Path(bad).exists(), "a refused chart leaves no file behind"


def test_the_skill_is_offered_with_its_actions(monkeypatch):
    from shani_chronoa import tools
    offered = [e["function"]["parameters"]["properties"]["action"]["enum"]
               for e in tools.TOOLS if e["function"]["name"] == "chart"]
    assert offered, "chart is not in the registry"
    assert set(offered[0]) == {"chart", "icon", "diagram"}, offered[0]
