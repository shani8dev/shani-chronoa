"""Drive the real `chronoa` command in a pseudo-terminal.

Unit tests prove the parts; this proves the assembled client on a terminal:
a streamed turn, a tool line, an approval answered by number, /models, one-shot
-p, JSON lines, and Ctrl+C. The engine is the stand-in Agent on a private bus,
so no model, GUI or person's bus is involved. NO_COLOR keeps the captured
screen plain; the real renderer's colour path is covered by its own tests.
"""

from __future__ import annotations

import json
import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.tui_bus import FakeBus

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "usr" / "bin" / "chronoa"


@pytest.fixture(scope="module")
def bus():
    instance = FakeBus()
    try:
        yield instance
    finally:
        instance.close()


class Terminal:
    def __init__(self, bus, args, *, stdin_pipe=False):
        env = os.environ.copy()
        env.update(
            DBUS_SESSION_BUS_ADDRESS=bus.address,
            NO_COLOR="1",
            PYTHONDONTWRITEBYTECODE="1",
            TERM="dumb",
        )
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("DISPLAY", None)
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            [sys.executable, str(LAUNCHER), *args],
            stdin=subprocess.PIPE if stdin_pipe else slave,
            stdout=slave,
            stderr=slave,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave)
        self.output = ""

    def write(self, data: str) -> None:
        os.write(self.master, data.encode())

    def pump(self, timeout: float = 0.1) -> str:
        ready, _, _ = select.select([self.master], [], [], timeout)
        if ready:
            try:
                chunk = os.read(self.master, 65536).decode("utf-8", "replace")
            except OSError:  # EIO once the child closes its end
                return self.output
            self.output += chunk
        return self.output

    def until(self, needle: str, timeout: float = 12.0) -> str:
        deadline = time.monotonic() + timeout
        while needle not in self.output and time.monotonic() < deadline:
            self.pump(min(0.1, max(0.01, deadline - time.monotonic())))
        assert needle in self.output, f"missing {needle!r} in:\n{self.output}"
        return self.output

    def wait(self, timeout: float = 10.0) -> int:
        deadline = time.monotonic() + timeout
        while self.proc.poll() is None and time.monotonic() < deadline:
            self.pump(0.05)
        if self.proc.poll() is None:
            self.proc.terminate()
        return self.proc.wait(timeout=5)

    def close(self):
        try:
            os.close(self.master)
        except OSError:
            pass


def test_interactive_turn_tool_approval_and_slash_command(bus):
    term = Terminal(bus, [])
    try:
        term.until("Chronoa")
        term.write("hello\n")
        term.until("read_file")
        term.until("Run the command?")
        term.until("1) Allow this once")
        term.until("2) Deny")
        term.until("from the stand-in agent")
        term.until("choice [1]")  # raw mode is active; only now can a digit reach it
        term.write("2\n")  # numbered choice -> the label "Deny" goes to Approve
        term.until("Approved with Deny.")
        term.write("/models\n")
        term.until("Models (1):")
        term.until("conversion")
        term.write("/quit\n")
        assert term.wait() == 0
    finally:
        term.close()


def test_interactive_arrow_keys_pick_the_second_label(bus):
    term = Terminal(bus, [])
    try:
        term.until("Chronoa")
        term.write("hello\n")
        term.until("choice [1]")
        term.write("\x1b[B\n")  # Down moves to "Deny", Enter confirms it
        term.until("Approved with Deny.")
        term.write("/quit\n")
        term.wait()
    finally:
        term.close()


def test_interactive_ctrl_c_cancels_the_turn(bus):
    term = Terminal(bus, [])
    try:
        term.until("Chronoa")
        term.write("slow\n")
        term.until("thinking")
        # The pty here has no controlling terminal, so a literal ^C would be
        # buffered as a byte instead of raising SIGINT the way a real terminal
        # does. Send the signal the terminal driver would send.
        os.kill(term.proc.pid, signal.SIGINT)
        out = term.until("Turn cancelled.")
        assert "NOT MINE" not in out
        term.write("/quit\n")
        term.wait()
    finally:
        term.close()


def test_print_mode_prints_only_the_reply(bus):
    term = Terminal(bus, ["-p", "no-approval"])
    try:
        code = term.wait()
        assert code == 0
        out = term.output
        assert "from the stand-in agent" in out
        assert "Done." in out
        assert "Run the command?" not in out
        assert '"event"' not in out
    finally:
        term.close()


def test_print_mode_refuses_approvals_and_says_so(bus):
    term = Terminal(bus, ["-p", "hello"])
    try:
        assert term.wait() == 0
        assert "Approval refused in -p mode" in term.output
        assert "Approved with Deny." in term.output
    finally:
        term.close()


def test_print_mode_allow_sends_the_exact_label(bus):
    term = Terminal(bus, ["-p", "hello", "--allow", "Allow this once"])
    try:
        assert term.wait() == 0
        assert "Approved with Allow this once." in term.output
        assert "Approval refused" not in term.output
    finally:
        term.close()


def test_json_mode_is_json_lines(bus):
    term = Terminal(bus, ["-p", "no-approval", "--json"])
    try:
        assert term.wait() == 0
        lines = [line for line in term.output.splitlines() if line.strip()]
        assert lines
        events = [json.loads(line) for line in lines]
        assert all("event" in event for event in events)
        assert any(event["event"] == "tool" for event in events)
        assert any(event["event"] == "reply" for event in events)
        assert events[-1]["event"] == "done"
    finally:
        term.close()


def test_launcher_script_runs_directly(bus):
    """Invoke usr/bin/chronoa itself, not `python -c` with a hand-set sys.path."""
    env = os.environ.copy()
    env.update(
        DBUS_SESSION_BUS_ADDRESS=bus.address, NO_COLOR="1", PYTHONDONTWRITEBYTECODE="1"
    )
    result = subprocess.run(
        [str(LAUNCHER), "-p", "no-approval"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "from the stand-in agent" in result.stdout


def test_print_mode_reads_the_question_from_stdin(bus):
    term = Terminal(bus, ["-p", "-"], stdin_pipe=True)
    try:
        term.proc.stdin.write(b"no-approval\n")
        term.proc.stdin.close()
        assert term.wait() == 0
        assert "from the stand-in agent" in term.output
    finally:
        term.close()
