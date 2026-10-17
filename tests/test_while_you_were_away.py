"""On unlock, one sentence about what Chronoa did while the screen was locked."""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import away  # noqa: E402

LOCKED = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def _at(minutes, tool, args=None, **extra):
    return {"timestamp": (LOCKED + timedelta(minutes=minutes)).isoformat(),
            "tool_name": tool, "args": args or {}, **extra}


def test_only_what_happened_after_the_lock_is_told():
    records = [_at(-5, "get_battery_status"),
               _at(3, "edit_file", {"path": "/x/notes.md"}),
               _at(7, "run_command", {"command": "pytest"}, result="ERROR(exit=1): 2 failed")]
    text = away.summary(records, LOCKED)
    assert text.startswith("While you were away, Chronoa did 2 things:"), text
    assert "Changed notes.md" in text and "Ran “pytest”" in text
    assert text.endswith("1 did not work."), text
    assert "battery" not in text.lower()


def test_nothing_happened_nothing_is_said():
    assert away.summary([_at(-1, "get_datetime")], LOCKED) == ""
