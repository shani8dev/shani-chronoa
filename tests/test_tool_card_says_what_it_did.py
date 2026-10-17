"""A tool card's one line says what the call did, not the raw arguments.

Measured in the 2026-10-15 coding take: every row read like
`action=add, id=1, content=Fix pricing.py…` or `action=list, id=1`.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from shani_chronoa.gui.blocks import call_phrase  # noqa: E402


@pytest.mark.parametrize("name, args, want", [
    ("todo_list", {"action": "add", "content": "Fix pricing.py"}, "Planned: “Fix pricing.py”"),
    ("todo_list", {"action": "list", "id": 1}, "Checked the plan"),
    ("todo_list", {"action": "update", "id": 2, "status": "completed"}, "Marked step 2 done"),
    ("todo_list", {"action": "complete", "id": 1}, "Marked step 1 done"),
    ("edit_file", {"path": "/home/x/shop/pricing.py", "old_string": "a"}, "Changed pricing.py"),
    ("run_command", {"command": "pytest -q"}, "Ran “pytest -q”"),
    ("run_command", {"command": "make", "where": "container"}, "Ran “make” in a container"),
    ("set_timer", {"seconds": 300, "label": "tea"}, "Timer for 5 min - tea"),
    ("get_weather", {"place": "Pune"}, "Weather in Pune"),
])
def test_common_calls_read_as_sentences(name, args, want):
    assert call_phrase(name, args) == want


def test_anything_else_keeps_the_raw_line():
    assert call_phrase("get_battery_status", {}) == ""
    assert call_phrase("some_tool", {"a": 1}) == "a=1"


def test_a_call_missing_its_key_argument_is_not_a_half_sentence():
    """`write_text_file` with no path read "Wrote " - worse than the raw line."""
    assert call_phrase("write_text_file", {"note": "x"}) == "note=x"
    assert call_phrase("run_command", {}) == ""


def test_an_odd_argument_falls_back_instead_of_raising():
    assert call_phrase("set_timer", {"seconds": "soon"}) == "seconds=soon"
