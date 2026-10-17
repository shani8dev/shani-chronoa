"""The last-resort paste: copy, press paste, put the person's clipboard back.

Handy inserts every dictation this way; Chronoa keeps it for text no key can
type when its IBus input is not running. Driven through the real `paste_text`
against stand-in `wl-copy`/`wl-paste` that keep the clipboard in a file, so the
order (copy, then paste key, then restore) and the restore itself are checked.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import input_control  # noqa: E402


def _fake_clipboard(tmp_path, monkeypatch, start: str):
    board = tmp_path / "clipboard"
    board.write_text(start)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "wl-copy").write_text(f"#!/bin/sh\n[ \"$1\" = --clear ] && : > {board} && exit 0\ncat > {board}\n")
    (bin_dir / "wl-paste").write_text(f"#!/bin/sh\ncat {board}\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wl-test")
    monkeypatch.setattr(input_control, "PASTE_RESTORE_SECONDS", 0.05)
    return board


def test_the_text_is_on_the_clipboard_when_paste_is_pressed_and_yours_comes_back(tmp_path, monkeypatch):
    board = _fake_clipboard(tmp_path, monkeypatch, "kept by the person")
    seen = []
    assert input_control.paste_text("नमस्ते", lambda: seen.append(board.read_text()))
    assert seen == ["नमस्ते"], "paste was pressed before the text was on the clipboard"
    assert board.read_text() == "kept by the person", "the person's clipboard was not put back"


def test_no_clipboard_tool_is_a_plain_no(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert input_control.paste_text("x", lambda: None) is False
