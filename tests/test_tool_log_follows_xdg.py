"""The tool-call log follows XDG_DATA_HOME at write time, not at import.

`tool_tracking.LOG_DIR` was computed at import and `tools._TRACKER` is built at
import - during test collection, before conftest redirects XDG_DATA_HOME - so
every test that dispatched a tool appended to the real user's log (374 `liar`,
378 `unver` fixture calls found in the development machine's 18,000 lines, which
the outcome model then trained on).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import dream, tool_tracking, tools  # noqa: E402


def test_the_import_time_tracker_writes_under_the_current_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    tools._TRACKER.record_call("probe_tool", {}, "ok", 1.0)
    log = tmp_path / "data" / "shani-chronoa" / "logs" / "tool_calls.log"
    assert log.exists() and "probe_tool" in log.read_text()
    assert tool_tracking.LOG_FILE == log
    assert dream.LOG_FILE == log


def test_an_explicit_directory_is_still_fixed(tmp_path, monkeypatch):
    tracker = tool_tracking.ToolTracker(log_dir=tmp_path / "fixed")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "elsewhere"))
    tracker.record_call("probe_tool", {}, "ok", 1.0)
    assert "probe_tool" in (tmp_path / "fixed" / "tool_calls.log").read_text()
