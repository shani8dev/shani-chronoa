"""Full multi-pane view: panes, focus ring, palette and resize in a real pty."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(REPO, "usr", "bin", "chronoa")


@pytest.fixture(scope="module")
def bus():
    from tests.tui_bus import FakeBus

    instance = FakeBus()
    try:
        yield instance
    finally:
        instance.close()


class Terminal:
    def __init__(self, bus, width=100, height=30):
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
        self.resize(width, height, slave)
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

    def resize(self, width, height, fd=None):
        fcntl.ioctl(
            fd if fd is not None else self.master,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", height, width, 0, 0),
        )

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
        assert needle in self.output, (needle, self.output[-2500:])
        return self.output

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                try:
                    self.output += os.read(self.master, 65536).decode(
                        "utf-8", "replace"
                    )
                except OSError:
                    break

    def send(self, data):
        os.write(self.master, data)

    def close(self):
        try:
            os.close(self.master)
        except OSError:
            pass


def test_panes_focus_palette_and_resize(bus):
    term = Terminal(bus)
    try:
        term.until("\x1b[?1049h")
        term.until("chronoa>")
        term.until("CHAT")  # pane borders
        term.until("Holiday plans")  # sidebar: conversations
        term.until("read_file")  # sidebar: skills
        term.until("conversion")  # sidebar: models
        term.until("focus: input")

        term.send(b"\t")  # input -> sidebar
        term.until("focus: sidebar")
        term.send(b"\t")  # sidebar -> detail
        term.until("focus: detail")
        term.send(b"\x1b[Z")  # Shift+Tab back to sidebar
        term.until("focus: sidebar")

        term.send(b"\x10")  # Ctrl+P: palette
        term.until("/help")
        term.send(b"mod")
        term.until("/models")
        term.send(b"\n")  # prefill, not execute
        term.until("chronoa> /models ")
        term.send(b"\n")  # now run it
        term.until("local")

        term.send(b"\x1b[5~")  # PgUp
        term.send(b"\x1b[6~")  # PgDn
        term.until("chronoa>")

        term.resize(70, 24)
        os.kill(term.proc.pid, signal.SIGWINCH)
        term.send(b"\x12")  # Ctrl+R: repaint at the new size
        term.until("chronoa>")
        assert "Unknown command" not in term.output
        assert "did not work" not in term.output

        term.send(b"/quit\n")
        term.until("\x1b[?1049l", timeout=5)
        assert term.proc.wait(timeout=8) == 0
    finally:
        term.close()


def test_compact_terminal_still_has_prompt(bus):
    term = Terminal(bus, width=45, height=14)
    try:
        term.until("chronoa>")
        term.until("/help commands")
        term.send(b"/status\n")
        term.until("llm model")
        term.send(b"/quit\n")
        term.until("\x1b[?1049l", timeout=5)
        assert term.proc.wait(timeout=8) == 0
    finally:
        term.close()
