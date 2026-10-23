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


# --- terminal-aware paste: Ctrl+Shift+V in a terminal, Ctrl+V elsewhere -------
#
# Harvested from AIYantra's "Extra" desktop engine. A terminal reads Ctrl+V as a
# literal control character, so a clipboard paste that works in every other app
# silently fails there - and the paste path is the only way a Devanagari or emoji
# word gets in. The combo is chosen from the focused window's app id.

class _Win:
    def __init__(self, app_id, focused=True):
        self.app_id = app_id
        self.focused = focused


def _fake_windows(monkeypatch, windows):
    from shani_chronoa import windows as win_mod

    class _Backend:
        def list(self):
            return windows
    monkeypatch.setattr(win_mod, "detect", lambda: (_Backend(), ""))


def test_a_terminal_gets_ctrl_shift_v(monkeypatch):
    from shani_chronoa.skills import input_control as ic
    for app_id in ("org.gnome.Terminal", "org.gnome.Console", "com.gnome.Ptyxis",
                   "Alacritty", "kitty", "foot", "org.wezfurlong.wezterm", "konsole"):
        _fake_windows(monkeypatch, [_Win(app_id)])
        assert ic._focused_is_terminal() is True, app_id
        assert ic._paste_combo() == "ctrl+shift+v", app_id


def test_a_normal_app_gets_plain_ctrl_v(monkeypatch):
    from shani_chronoa.skills import input_control as ic
    for app_id in ("org.gnome.TextEditor", "firefox", "code", "org.libreoffice.LibreOffice", ""):
        _fake_windows(monkeypatch, [_Win(app_id)])
        assert ic._focused_is_terminal() is False, app_id
        assert ic._paste_combo() == "ctrl+v", app_id


def test_an_unreadable_window_list_falls_back_to_ctrl_v(monkeypatch):
    """Not knowing the window is not a terminal: Ctrl+V is the safe default that
    works in every ordinary app, so an unreadable list must not send Shift."""
    from shani_chronoa import windows as win_mod
    from shani_chronoa.skills import input_control as ic
    monkeypatch.setattr(win_mod, "detect", lambda: (None, "no backend"))
    assert ic._paste_combo() == "ctrl+v"

    def _boom():
        raise RuntimeError("window list unavailable")
    monkeypatch.setattr(win_mod, "detect", _boom)
    assert ic._paste_combo() == "ctrl+v"
