"""The mascot's note table: one sound per face kind, replaceable per person.

`mascot_sounds.py` pins two promises that a notice can lean on:

1. **The table is the face kinds.** `Mascot.notice(kind)` is called with the
   same kind strings the bus routes and the landline calls - a sound keyed on
   anything else would be two facts about one moment. So `SOUND_FOR` is pinned
   kind-for-kind against the mascot's own kind list, and every file it names
   is shipped (a table entry pointing at a missing file is a silent lie).
2. **The person's folder wins, file by file.** `user_dir()` is
   `$XDG_DATA_HOME/shani-chronoa/mascot-sounds` (or `~/.local/share/...`) and
   `resolve()` prefers a file of that exact name there, falling back to the
   shipped set, and answering `None` - silence - for unwired kinds.

These tests never touch audio hardware: `resolve`, `user_dir` and the table
are pure path questions, which is exactly where the drop-in replacement
promise lives.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import mascot_sounds as MS  # noqa: E402

_SHIPPED = _REPO / "usr" / "share" / "shani-chronoa" / "mascot-sounds"


class TestTheTable:
    def test_keys_are_face_kinds(self):
        """Keyed on the mascot's face kinds, nothing else. The mascot's own
        kind set is read from `mascot.py`; anything `emit` can show the table
        may be asked about."""
        from shani_chronoa import mascot as mascot_module

        kinds = set(getattr(mascot_module, "_EVENT_FACE", {}))
        unknown = set(MS.SOUND_FOR) - kinds
        assert not unknown, f"SOUND_FOR keys are not face kinds: {sorted(unknown)}"

    def test_exactly_the_four_wired_kinds(self):
        """The count is stated in the module: four wired kinds. A fifth entry
        means buzzing on a refusal; a missing one means a decision without its
        note."""
        assert set(MS.SOUND_FOR) == {"call", "greet", "meeting", "build-ok"}

    def test_every_named_file_ships(self):
        missing = [n for n in MS.SOUND_FOR.values() if not (_SHIPPED / n).is_file()]
        assert not missing, f"table names files that did not ship: {missing}"

    def test_named_files_are_friendly_names(self):
        """Coucou's receipt names files by the event they report
        (finish/approval/greet), so the shipped names stay in that style."""
        allowed = {"greet.wav", "approval.wav", "finish.wav"}
        bad = set(MS.SOUND_FOR.values()) - allowed
        assert not bad, f"unexpected shipped sound names: {sorted(bad)}"


class TestUserDir:
    def test_respects_xdg_data_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert MS.user_dir() == tmp_path / "shani-chronoa" / "mascot-sounds"

    def test_default_is_local_share(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        home = Path.home()
        assert MS.user_dir() == home / ".local" / "share" / "shani-chronoa" / "mascot-sounds"

    def test_returns_a_path_even_in_a_strange_environ(self, monkeypatch):
        """A folder name must never fail a note: even with junk in the env the
        function answers a usable Path, not an exception."""
        monkeypatch.setenv("XDG_DATA_HOME", "\n\n")
        assert isinstance(MS.user_dir(), Path)


class TestResolve:
    def test_unwired_kind_is_silent(self):
        assert MS.resolve("error") is None
        assert MS.resolve("build-fail") is None
        assert MS.resolve("") is None

    def test_shipped_file_resolves(self):
        p = MS.resolve("build-ok")
        assert p is not None and p.is_file()
        assert p.name == "finish.wav"

    def test_person_folder_wins_file_by_file(self, tmp_path, monkeypatch):
        """Drop-in by name: `meeting` has its own file elsewhere in the folder,
        the other kinds keep the shipped set - the win is per file, not all-or-
        nothing."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        own = MS.user_dir()
        own.mkdir(parents=True)
        (own / "approval.wav").write_bytes(b"RIFF-personal")

        assert MS.resolve("meeting") == own / "approval.wav"
        assert MS.resolve("build-ok") == _SHIPPED / "finish.wav"

    def test_unknown_name_in_own_folder_does_not_shadow(self, monkeypatch, tmp_path):
        """Only files the table names count; junk in the folder is ignored."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        own = MS.user_dir()
        own.mkdir(parents=True)
        (own / "some-other.wav").write_bytes(b"junk")

        assert MS.resolve("greet") == _SHIPPED / "greet.wav"

    def test_dir_instead_of_file_in_own_folder_loses(self, tmp_path, monkeypatch):
        """`is_file`, not `exists`: a directory named like the note is not a
        note."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        (MS.user_dir() / "greet.wav").mkdir(parents=True)
        assert MS.resolve("greet") == _SHIPPED / "greet.wav"
