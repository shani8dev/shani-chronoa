"""Tests for tool_tracking module."""

import csv
import json
import os
import sys
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


class TestTheRecordCarriesTheVerdict:
    """`execute_tool` recorded the call *before* running verification, so the
    record was written at a moment when the verdict did not exist yet and could
    not contain it. The one file whose entire job is telling a failed action
    apart from a successful one could not be sorted on either.
    """

    def test_a_verdict_is_stored_on_the_record(self, tracker: ToolTracker) -> None:
        record = tracker.record_call(
            "brightness", {"level": 40}, "Set to 40%", 12.0, verdict="verified"
        )
        assert record.verdict == "verified"

    def test_a_call_with_no_verdict_records_none_rather_than_inventing_one(
        self, tracker: ToolTracker
    ) -> None:
        """None means "never reached verification" - a non-zero exit, or an
        exception. Recording 'unverified' there would be a different claim,
        and recording 'verified' would be a lie."""
        record = tracker.record_call("brightness", {}, "exit 1", 5.0)
        assert record.verdict is None

    def test_the_verdict_survives_into_the_serialised_record(
        self, tracker: ToolTracker
    ) -> None:
        record = tracker.record_call("t", {}, "r", 1.0, verdict="failed")
        assert record.to_dict()["verdict"] == "failed"
        assert record.to_log_dict()["verdict"] == "failed"

    def test_the_verdict_reaches_the_on_disk_log(self, tracker: ToolTracker) -> None:
        tracker.record_call("t", {}, "r", 1.0, verdict="failed")
        line = tracker.log_file.read_text().strip().splitlines()[-1]
        assert json.loads(line)["verdict"] == "failed"

    def test_the_verdict_and_evidence_are_csv_columns(
        self, tracker: ToolTracker, tmp_path: Path
    ) -> None:
        """Otherwise the verdict is lost the moment anyone exports for
        analysis - which is what an audit trail is exported for."""
        tracker.record_call("t", {}, "r", 1.0, verdict="failed", evidence="never changed")
        out = tmp_path / "out.csv"
        tracker.export_csv(out)
        # Read it back with the csv module rather than splitting on commas:
        # evidence is free text and routinely contains them.
        with out.open() as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["verdict"] == "failed"
        assert rows[0]["evidence"] == "never changed"

    def test_a_failed_action_is_distinguishable_from_a_good_one(
        self, tracker: ToolTracker
    ) -> None:
        tracker.record_call("brightness", {}, "done", 1.0, verdict="verified")
        tracker.record_call("brightness", {}, "done", 1.0, verdict="failed")
        verdicts = [c.verdict for c in tracker.get_calls()]
        assert verdicts == ["verified", "failed"]
        # The two results strings are byte-identical here, which is the whole
        # problem: identical text, opposite outcomes.
        assert [c.result for c in tracker.get_calls()] == ["done", "done"]


def _skill(tmp_path, name, body, monkeypatch):
    """Install a throwaway skill module and return its dotted name.

    `sys.modules` is purged first: importlib checks it before sys.path, so
    without this a module of the same name left by another test wins and the
    body written here is silently ignored - which is how two test files came
    to disagree about the same module name.
    """
    path = tmp_path / f"{name}.py"
    path.write_text(body)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv(
        "PYTHONPATH", f"{tmp_path}{os.pathsep}{existing}" if existing else str(tmp_path)
    )
    return name


class TestEndToEndThroughExecuteTool:
    """Not a unit test of the tracker: the real dispatch path, with a real
    skill module whose post-condition fails, and the real module-level
    tracker that `execute_tool` writes to."""

    @pytest.fixture
    def real_tracker(self, tmp_path, monkeypatch):
        from shani_chronoa import tools

        monkeypatch.setattr(tools, "_TRACKER", ToolTracker(log_dir=tmp_path))
        return tools

    def test_a_skill_that_lies_about_success_is_logged_as_failed(
        self, tmp_path, monkeypatch, real_tracker
    ):
        import importlib

        from shani_chronoa import tools

        mod = _skill(
            tmp_path,
            "tt_liar",
            "def _c(a):\n    return False, 'the display never changed'\n"
            "POST_CONDITION = _c\ndef run(a):\n    return 'All done successfully.'\n",
            monkeypatch,
        )
        tools._HANDLER_FNS[mod] = importlib.import_module(mod).run
        out = tools.execute_tool(mod, {})

        assert "VERIFICATION FAILED" in out
        (record,) = tools._TRACKER.get_calls()
        assert record.verdict == "failed", (
            "the audit record said nothing about a failed action"
        )
        # The evidence has to be stored too. `result` holds the skill's own
        # account, which for a failed action is a confident claim that it
        # worked - so a record carrying the verdict but not the reason reads
        # "All done successfully." next to failed, and never says why.
        assert record.evidence == "the display never changed"

    def test_a_verified_action_is_logged_as_verified(
        self, tmp_path, monkeypatch, real_tracker
    ):
        import importlib

        from shani_chronoa import tools

        mod = _skill(
            tmp_path,
            "tt_honest",
            "def _c(a):\n    return True, 'the display is at 40%'\n"
            "POST_CONDITION = _c\ndef run(a):\n    return 'Set to 40%'\n",
            monkeypatch,
        )
        tools._HANDLER_FNS[mod] = importlib.import_module(mod).run
        tools.execute_tool(mod, {})

        (record,) = tools._TRACKER.get_calls()
        assert record.verdict == "verified"

    def test_a_skill_with_no_post_condition_is_logged_as_unverified(
        self, tmp_path, monkeypatch, real_tracker
    ):
        """Silence is not confirmation, and the audit trail must not quietly
        upgrade it."""
        import importlib

        from shani_chronoa import tools

        mod = _skill(tmp_path, "tt_silent", "def run(a):\n    return 'done'\n", monkeypatch)
        tools._HANDLER_FNS[mod] = importlib.import_module(mod).run
        tools.execute_tool(mod, {})

        (record,) = tools._TRACKER.get_calls()
        assert record.verdict == "unverified"

    def test_a_crashing_call_is_logged_with_no_verdict(
        self, tmp_path, monkeypatch, real_tracker
    ):
        import importlib

        from shani_chronoa import tools

        mod = _skill(
            tmp_path, "tt_boom", "def run(a):\n    raise SystemExit(3)\n", monkeypatch
        )
        tools._HANDLER_FNS[mod] = importlib.import_module(mod).run
        tools.execute_tool(mod, {})

        (record,) = tools._TRACKER.get_calls()
        assert record.verdict is None

    def test_execute_tool_outcome_agrees_with_the_record(
        self, tmp_path, monkeypatch, real_tracker
    ):
        """The structured accessor and the audit trail must not disagree."""
        import importlib

        from shani_chronoa import tools
        from shani_chronoa.verification import Verdict

        mod = _skill(
            tmp_path,
            "tt_liar_repeat",
            "def _c(a):\n    return False, 'nothing happened'\n"
            "POST_CONDITION = _c\ndef run(a):\n    return 'ok'\n",
            monkeypatch,
        )
        tools._HANDLER_FNS[mod] = importlib.import_module(mod).run
        outcome = tools.execute_tool_outcome(mod, {})

        assert outcome.verdict is Verdict.FAILED
        assert "nothing happened" in outcome.evidence
        (record,) = tools._TRACKER.get_calls()
        assert record.verdict == outcome.verdict.value
