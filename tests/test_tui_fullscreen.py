"""Full-screen Chronoa: layout, editor, and a real pty against the stand-in."""

from __future__ import annotations

import io
import os
import pty
import select
import struct
import subprocess
import sys
import time

import pytest

from shani_chronoa.tui import commands, fullscreen

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(REPO, "usr", "bin", "chronoa")


def test_editor_insert_move_backspace_and_history():
    editor = fullscreen.LineEditor(["first", "second"])
    editor.insert("hello")
    editor.insert(" world")
    assert editor.value == "hello world"
    assert editor.cursor == 11
    editor.move("left")
    editor.insert("X")
    assert editor.value == "hello worlXd"
    editor.backspace()
    assert editor.value == "hello world"
    editor.recall("up")
    assert editor.value == "second"
    editor.recall("up")
    assert editor.value == "first"
    editor.recall("down")
    assert editor.value == "second"
    assert editor.take() == "second"
    assert editor.value == ""


def test_editor_completes_slash_and_path_tokens():
    editor = fullscreen.LineEditor()
    editor.insert("/sett")
    assert editor.complete() == ["/settings"]
    assert editor.value == "/settings "
    editor.insert("@~/non")
    matches = editor.complete()
    assert isinstance(matches, list)


def test_screen_draw_repaints_header_body_and_input():
    stream = io.StringIO()
    screen = fullscreen.Screen(stream=stream, fd=0)
    screen._in_alternate = True
    screen.size = lambda: (40, 10)
    screen.draw([" Chronoa · qwen3:8b"], ["hello", "world"], "/models", 8, 0)
    written = stream.getvalue()
    assert "\x1b[H\x1b[J" in written
    assert " Chronoa · qwen3:8b" in written
    assert "hello" in written and "world" in written
    assert "chronoa> /models" in written
    assert "\x1b[10;18H" in written  # cursor on the input line


def test_transcript_turns_writes_into_lines():
    class App:
        dirty = False

    app = App()
    transcript = fullscreen.Transcript(app)
    transcript.write("one\ntwo\n")
    transcript.write("partial")
    transcript.flush()
    assert transcript.lines == ["one", "two", "partial"]
    assert app.dirty is True


@pytest.fixture(scope="module")
def bus():
    from tests.tui_bus import FakeBus

    instance = FakeBus()
    try:
        yield instance
    finally:
        instance.close()


class Terminal:
    def __init__(self, bus):
        env = os.environ.copy()
        env.update(
            DBUS_SESSION_BUS_ADDRESS=bus.address,
            NO_COLOR="1",
            PYTHONDONTWRITEBYTECODE="1",
            TERM="xterm",
        )
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("DISPLAY", None)
        self.master, slave = pty.openpty()
        fcntl_mod = __import__("fcntl")
        termios_mod = __import__("termios")
        fcntl_mod.ioctl(
            slave, termios_mod.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0)
        )
        self.proc = subprocess.Popen(
            [sys.executable, LAUNCHER],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave)
        self.output = ""

    def until(self, needle, timeout=20):
        deadline = time.monotonic() + timeout
        while needle not in self.output and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    self.output += os.read(self.master, 65536).decode(
                        "utf-8", "replace"
                    )
                except OSError:
                    break
        assert needle in self.output, (needle, self.output[-2000:])
        return self.output

    def close(self):
        try:
            os.close(self.master)
        except OSError:
            pass


def test_full_screen_turn_approval_and_quit(bus):
    term = Terminal(bus)
    try:
        term.until("\x1b[?1049h")  # alternate screen
        term.until("chronoa>")
        term.until("@file attach")  # header hint, not a scrolled log
        term.until("/help commands")
        os.write(term.master, b"/help\n")
        term.until("/settings get")
        os.write(term.master, b"hello\n")
        term.until("Run the command?")
        term.until("choice [1]:")
        os.write(term.master, b"2\n")
        term.until("Approved with Deny.")
        os.write(term.master, b"/quit\n")
        term.proc.wait(timeout=8)
        term.until("\x1b[?1049l", timeout=2)
        assert term.proc.returncode == 0
    finally:
        term.close()


def test_page_keys_and_ctrl_c_do_not_break_the_view(bus):
    term = Terminal(bus)
    try:
        term.until("chronoa>")
        os.write(term.master, b"/memory\n")
        term.until("Remembered facts")
        os.write(term.master, b"\x1b[5~")  # PgUp
        os.write(term.master, b"\x1b[6~")  # PgDn
        os.write(term.master, b"\x03")  # Ctrl+C with no turn running
        os.write(term.master, b"/quit\n")
        term.proc.wait(timeout=8)
        assert term.proc.returncode == 0
        assert "Unknown command" not in term.output
    finally:
        term.close()
