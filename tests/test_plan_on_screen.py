"""The checklist, on screen: "Chronoa · step 2 of 4 (1 done) · Run the tests"."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import desktop_cue  # noqa: E402
from shani_chronoa.skills import todo_list  # noqa: E402


class _On:
    def get_bool(self, key, default=False):
        return True


def test_the_plan_line_follows_the_checklist(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("CHRONOA_PROJECT", raising=False)
    monkeypatch.setattr(todo_list, "ChronoaConfig", lambda: _On())
    assert desktop_cue.plan_text() == ""
    for step in ("Fix the discount", "Run the tests", "Write the note"):
        todo_list._run({"action": "add", "content": step})
    todo_list._run({"action": "complete", "id": 1})
    todo_list._run({"action": "start", "id": 2})
    assert desktop_cue.plan_text() == "Chronoa · step 2 of 3 (1 done) · Run the tests"
    todo_list._run({"action": "complete", "id": 2})
    todo_list._run({"action": "complete", "id": 3})
    assert desktop_cue.plan_text() == "Chronoa · all 3 steps done"
