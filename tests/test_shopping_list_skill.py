"""The shopping-list skill, driven through the real dispatch path.

A list of groceries is the user's own prose, like `notes`: it must be readable
without a consent switch and must live under the redirected state directory,
not the developer's real one. Every assertion here goes through
`execute_tool_outcome` so a correct private `_run` cannot stand in for a skill
nobody can call.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa")
)

from shani_chronoa import tools  # noqa: E402


def _run(action, **kwargs):
    out = tools.execute_tool_outcome("shopping_list", {"action": action, **kwargs})
    assert out.ran, f"shopping_list did not run: {out}"
    return str(out.text)


def test_an_item_survives_add_list_check_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    assert "empty" in _run("list").lower()
    added = _run("add", text="milk")
    assert "Added" in added and "milk" in added, added
    ident = [
        tok.rstrip(":") for tok in added.split() if tok.startswith("s") and "_" in tok
    ][0]

    listed = _run("list")
    assert "milk" in listed and "[ ]" in listed, listed
    assert "[x]" in _run("check", id=ident), _run("check", id=ident)
    assert "[x]" in _run("list")
    assert "[ ]" in _run("uncheck", id=ident)
    assert "Removed" in _run("remove", id=ident)
    assert "milk" not in _run("list")


def test_it_writes_only_under_the_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    _run("add", text="bread")
    path = tmp_path / "shani-chronoa" / "shopping_list.json"
    assert path.is_file(), "the list was not written where the skill says"
    assert (path.stat().st_mode & 0o777) == 0o600
    real = Path.home() / ".local" / "state" / "shani-chronoa" / "shopping_list.json"
    assert not real.exists(), "a redirected run wrote into the real state home"


def test_a_corrupt_store_is_refused_not_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "shani-chronoa" / "shopping_list.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json")

    answer = _run("list")
    assert "Refusing" in answer and "not valid JSON" in answer, answer
    assert path.read_text() == "not json", "the corrupt store was overwritten"


def test_it_is_registered_as_a_skill():
    names = {t.get("function", {}).get("name") for t in tools.TOOLS}
    assert "shopping_list" in names, "shopping_list is not in the tool registry"
