"""Tests for tool_tracking module."""

import json
import tempfile
from pathlib import Path

import pytest

from shani_chronoa.tool_tracking import ToolTracker, ToolCallRecord


@pytest.fixture
def tracker(tmp_path: Path) -> ToolTracker:
    """Create a ToolTracker with a temporary log directory."""
    return ToolTracker(log_dir=tmp_path)


def test_record_call(tracker: ToolTracker) -> None:
    """Test that record_call works correctly."""
    record = tracker.record_call("test_tool", {"arg1": "val1"}, "result1", 100.0)
    assert record.tool_name == "test_tool"
    assert record.args == {"arg1": "val1"}
    assert record.result == "result1"
    assert record.duration_ms == 100.0
    assert len(tracker.get_calls()) == 1


def test_get_calls(tracker: ToolTracker) -> None:
    """Test get_calls returns expected results."""
    tracker.record_call("tool_a", {}, "r1", 50.0)
    tracker.record_call("tool_b", {}, "r2", 75.0)
    calls = tracker.get_calls()
    assert len(calls) == 2
    assert calls[0].tool_name == "tool_a"
    assert calls[1].tool_name == "tool_b"


def test_get_calls_by_tool(tracker: ToolTracker) -> None:
    """Test get_calls_by_tool filters correctly."""
    tracker.record_call("shared_tool", {}, "r1", 50.0)
    tracker.record_call("other_tool", {}, "r2", 75.0)
    tracker.record_call("shared_tool", {}, "r3", 25.0)
    calls = tracker.get_calls_by_tool("shared_tool")
    assert len(calls) == 2
    assert all(c.tool_name == "shared_tool" for c in calls)


def test_export_csv(tracker: ToolTracker, tmp_path: Path) -> None:
    """Test export_csv produces valid CSV."""
    tracker.record_call("tool_x", {"k": "v"}, "result", 42.0)
    csv_path = tmp_path / "tool_calls.csv"
    tracker.export_csv(csv_path)
    with open(csv_path) as f:
        content = f.read()
    assert "tool_name" in content
    assert "tool_x" in content
