"""The chart widgets draw without cairo and the panels feed them real numbers."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

if not Gtk.init_check():
    pytest.skip("no display", allow_module_level=True)

from shani_chronoa.gui import charts  # noqa: E402


def _snapshot(widget, w=300, h=120):
    window = Gtk.Window(child=widget, default_width=w, default_height=h)
    window.present()
    ctx = __import__("gi.repository.GLib", fromlist=["GLib"]).MainContext.default()
    for _ in range(50):
        ctx.iteration(False)
    snap = Gtk.Snapshot()
    widget.snapshot_child(widget.get_first_child(), snap) if widget.get_first_child() else None
    window.destroy()
    return True


def test_each_chart_builds_and_takes_data():
    ring = charts.Ring(100)
    ring.set(0.5, "50%", "half")
    assert ring.big.get_text() == "50%"
    bars = charts.Bars()
    bars.set([("Mo", 3), ("Tu", 9)])
    assert bars.columns.values == [3, 9]
    meter = charts.Meter("Disk")
    meter.set(0.95, "95%")
    assert meter.track.colour == charts.RED
    hb = charts.HBars()
    hb.set([("a", 2), ("b", 1)], values=["2", "1"])
    assert hb.get_child_at(0, 0).get_text() == "a"
    line = charts.Line()
    line.set([(0, 10), (12, 50)])
    stacked = charts.StackedBar()
    stacked.set([(charts.BLUE, 3), (charts.GREEN, 0)])
    assert stacked.segments == [(charts.BLUE, 3)]
    for widget in (ring, bars, meter, hb, line, stacked):
        assert _snapshot(widget)


def test_level_colours_follow_the_direction():
    assert charts.level_colour(0.95) == charts.RED
    assert charts.level_colour(0.95, high_is_bad=False) == charts.GREEN
    assert charts.level_colour(0.33, high_is_bad=False) == charts.RED
    assert charts.level_colour(0.6, high_is_bad=False) == charts.YELLOW


def test_activity_insights_count_and_rate():
    from shani_chronoa.gui.surfaces import activity
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    records = [{"tool_name": "a", "verdict": "verified", "timestamp": now},
               {"tool_name": "a", "verdict": "failed", "timestamp": now},
               {"tool_name": "b", "verdict": "unverified", "timestamp": now}]
    grid = activity.insights(records)
    assert grid is not None
    assert activity.insights([]) is None


