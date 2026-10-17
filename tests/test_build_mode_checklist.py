"""Build mode keeps its own checklist: per project, offered, and shown as progress."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import buildmode  # noqa: E402
from shani_chronoa.skills import todo_list  # noqa: E402


class _On:
    def get_bool(self, key, default=False):
        return True


def test_items_belong_to_the_project_they_were_added_in(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(todo_list, "ChronoaConfig", lambda: _On())
    monkeypatch.delenv("CHRONOA_PROJECT", raising=False)
    todo_list._run({"action": "add", "content": "buy milk"})
    monkeypatch.setenv("CHRONOA_PROJECT", "/home/x/shop")
    todo_list._run({"action": "add", "content": "fix the discount"})
    listed = todo_list._run({"action": "list"})
    assert "fix the discount" in listed and "buy milk" not in listed, listed
    assert "1 task(s) belong to other lists" not in listed  # only shown when empty
    todo_list._run({"action": "clear"})
    monkeypatch.delenv("CHRONOA_PROJECT")
    assert "buy milk" in todo_list._run({"action": "list"}), "clear in a project removed other lists"


def test_build_mode_offers_the_checklist_and_says_how_to_use_it(monkeypatch):
    assert "todo_list" in buildmode.BUILD_TOOLS
    monkeypatch.setattr(buildmode, "project", lambda: "/home/x/shop")
    clause = buildmode.prompt_clause()
    assert "todo_list" in clause and "in_progress" in clause and "edit_file" in clause


def test_a_status_word_as_the_action_updates_that_step(tmp_path, monkeypatch):
    """Qwen3-1.7B sent action "complete" and was refused (measured 2026-10-15)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(todo_list, "ChronoaConfig", lambda: _On())
    monkeypatch.delenv("CHRONOA_PROJECT", raising=False)
    todo_list._run({"action": "add", "content": "fix the discount"})
    out = todo_list._run({"action": "complete", "id": 1})
    assert "Action must be" not in out, out
    assert todo_list._load()[0][0]["status"] == "completed"
    todo_list._run({"action": "start", "id": 1})
    assert todo_list._load()[0][0]["status"] == "in_progress"


def test_a_garbled_path_finds_the_projects_one_file(tmp_path, monkeypatch):
    """`~/<half the project path>/pricing.py` went nowhere twice (2026-10-15)."""
    from shani_chronoa import files
    monkeypatch.setenv("HOME", str(tmp_path))
    shop = tmp_path / "shop"
    (shop / "pkg").mkdir(parents=True)
    (shop / "pkg" / "pricing.py").write_text("x = 1\n")
    (shop / "a.py").write_text("")
    (shop / "b").mkdir()
    (shop / "b" / "a.py").write_text("")
    monkeypatch.setenv("CHRONOA_PROJECT", str(shop))
    assert files.resolve("~/no/such/dir/pricing.py") == (shop / "pkg" / "pricing.py").resolve()
    # A new file in a real folder is left as named, and two candidates are not guessed.
    assert files.resolve("new.py") == (shop / "new.py").resolve()
    assert files.resolve("~/no/such/a.py") == (tmp_path / "no" / "such" / "a.py").resolve()
    monkeypatch.delenv("CHRONOA_PROJECT")
    assert files.resolve("~/no/such/dir/pricing.py") == (tmp_path / "no/such/dir/pricing.py").resolve()
