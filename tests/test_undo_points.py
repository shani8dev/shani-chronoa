"""Every write skill leaves an undo point, and the points can be listed (cline/gemini checkpoints, scoped to Chronoa)."""

import os
from pathlib import Path

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import find_and_replace, undo_last_change, write_text_file


def test_overwrite_append_and_bulk_replace_are_undoable():
    home = Path(os.environ["HOME"])
    ChronoaConfig().set("file-edit-enabled", "true")
    ChronoaConfig().set("bulk-edit-enabled", "true")
    note = home / "note.txt"
    write_text_file._run({"path": str(note), "content": "v1"})
    write_text_file._run({"path": str(note), "content": "v2", "overwrite": True})
    write_text_file._run({"path": str(note), "content": "+", "append": True})
    assert note.read_text() == "v2+"
    listing = undo_last_change._run({"list": True, "path": str(note)})
    assert listing.startswith("2 undo point(s)") and "steps=2" in listing
    assert undo_last_change._run({"path": str(note)}).startswith("Restored") and note.read_text() == "v2"
    assert undo_last_change._run({"path": str(note)}).startswith("Restored") and note.read_text() == "v1"

    (home / "proj").mkdir()
    for n in ("a", "b"):
        (home / "proj" / f"{n}.txt").write_text(f"colour {n}")
    out = find_and_replace._run({"path": str(home / "proj"), "find": "colour", "replace": "color", "dry_run": False})
    assert "2 file(s) changed" in out, out
    every = undo_last_change._run({"list": True})
    assert "a.txt" in every and "b.txt" in every
    undo_last_change._run({"path": str(home / "proj" / "a.txt")})
    assert (home / "proj" / "a.txt").read_text() == "colour a"
    assert (home / "proj" / "b.txt").read_text() == "color b"


def test_listing_needs_the_same_consent():
    assert "Refusing" in undo_last_change._run({"list": True})


def test_edit_preview_shows_a_diff_and_writes_nothing():
    from shani_chronoa.skills import edit_file
    home = Path(os.environ["HOME"])
    ChronoaConfig().set("file-edit-enabled", "true")
    cfg = home / "app.conf"
    cfg.write_text("name=x\nport=8080\nmode=a\n")
    out = edit_file._run({"path": str(cfg), "old_string": "port=8080", "new_string": "port=9090", "preview": True})
    assert out.startswith("Preview only") and "-port=8080" in out and "+port=9090" in out
    assert cfg.read_text() == "name=x\nport=8080\nmode=a\n"
    assert "No undo points" in undo_last_change._run({"list": True, "path": str(cfg)})
