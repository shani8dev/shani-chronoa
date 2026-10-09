"""The notes skill, driven through dispatch (no watch, phone or network needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import tools  # noqa: E402


def _notes(action, **kwargs):
    out = tools.execute_tool_outcome("notes", {"action": action, **kwargs})
    assert out.ran, f"notes did not run: {out}"
    return str(out.text)


def test_a_note_survives_a_round_trip(tmp_path, monkeypatch):
    """add -> list -> search -> remove, through the real dispatch path.

    Driven through `execute_tool_outcome` rather than calling `notes._run`,
    because the private function being correct proves nothing about the skill
    being reachable - the failure mode this repo keeps documenting.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    assert "no notes" in _notes("list").lower()

    _notes("add", text="wifi guest password is hunter2")
    _notes("add", text="milk")

    listing = _notes("list")
    assert "milk" in listing and "hunter2" in listing, listing

    found = _notes("search", query="milk")
    assert "milk" in found, found
    assert "hunter2" not in found, "search returned a note it should not have: " + found

    # Remove the note search matched, then it - and only it - is gone.
    note_id = [tok for tok in found.split() if tok.startswith("n") and "_" in tok][0]
    _notes("remove", id=note_id)
    listing_after = _notes("list")
    assert "milk" not in listing_after, listing_after
    assert "hunter2" in listing_after, "remove deleted the wrong note: " + listing_after


def test_it_writes_nothing_outside_the_state_home(tmp_path, monkeypatch):
    """The store is resolved per call, so a redirected run cannot touch the real one."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    _notes("add", text="contained")
    assert (tmp_path / "shani-chronoa" / "notes.json").is_file()
    real = Path.home() / ".local" / "state" / "shani-chronoa" / "notes.json"
    assert not real.exists() or real.stat().st_mtime < (tmp_path.stat().st_mtime)


def test_it_is_a_registered_skill_and_not_just_a_module():
    """The regression this guards: a module that is written and unit-tested and
    never reachable. That class of defect has shipped here before."""
    names = [x.get("name") or x.get("function", {}).get("name") for x in tools.TOOLS]
    assert "notes" in names, "notes is not in the tool registry"