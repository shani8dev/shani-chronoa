"""Guarantee: every Agent method has a slash command and a CLI subcommand.

Also: the stand-in Agent serves the *same shapes as the real engine functions*.
The stand-in once answered `/status` with a shape the real Inventory did not
have, and no test ran it, so the terminal client crashed against a real engine.
Both halves here use the real XML and the real functions, not a copied list.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from shani_chronoa import agent_capabilities
from shani_chronoa.agent_contract import XML

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "usr" / "bin" / "chronoa"


def _xml_methods() -> dict[str, tuple]:
    root = ET.fromstring(XML)
    iface = next(n for n in root if n.tag == "interface")
    out = {}
    for node in iface.findall("method"):
        args = tuple(
            (a.attrib["name"], a.attrib["type"])
            for a in node.findall("arg")
            if a.attrib.get("direction", "out") == "in"
        )
        reply = tuple(
            a.attrib["type"]
            for a in node.findall("arg")
            if a.attrib.get("direction") == "out"
        )
        out[node.attrib["name"]] = (args, reply)
    return out


def test_contract_and_client_table_name_the_same_methods():
    wire = _xml_methods()
    table = agent_capabilities.METHODS
    for name, method in table.items():
        assert name in wire, f"{name} is in the client table but not on the bus"
        args, reply = wire[name]
        assert args == method.args, f"{name} has different arguments on the bus"
        assert reply == (method.signature_out.strip("()"),)
    for name in wire:
        if name not in agent_capabilities.LEGACY_METHODS:
            assert name in table, f"{name} is on the bus but no client can call it"


def test_every_method_has_a_slash_path_and_a_cli_path():
    for name, method in agent_capabilities.METHODS.items():
        assert method.slash and method.cli, f"{name} has no client path"
        assert method.slash == method.cli, f"{name} parses differently in TUI and CLI"
        assert method.signature_out == "(s)", f"{name} must return one JSON string"
        assert agent_capabilities.by_slash(method.slash).name == name
        assert agent_capabilities.by_cli(method.cli).name == name


def test_parser_builds_every_cli_subcommand():
    from shani_chronoa.tui.cli import build_parser

    parser = build_parser()
    for name, method in agent_capabilities.METHODS.items():
        count = len(method.arg_names) - int(method.confirm)
        argv = [*method.cli, *(f"arg{i}" for i in range(count))]
        parsed = parser.parse_args(argv)
        assert parsed.method == name, f"{argv} did not select {name}"
        for argname, value in zip(
            (a for a in method.arg_names if a != "confirm"),
            (f"arg{i}" for i in range(count)),
        ):
            assert getattr(parsed, argname) == value


def test_slash_resolution_matches_the_cli_table():
    from shani_chronoa.tui import commands

    for name, method in agent_capabilities.METHODS.items():
        count = len(method.arg_names) - int(method.confirm)
        args = [f"arg{i}" for i in range(count)]
        resolved, got = commands.resolve(
            method.slash[0], " ".join(method.slash[1:] + tuple(args))
        )
        assert resolved == name, (name, resolved)
        assert got == args, (name, got)


class Terminal:
    """One short-lived `chronoa` process on the private fake-agent bus."""

    def __init__(self, bus, argv):
        env = os.environ.copy()
        env.update(
            DBUS_SESSION_BUS_ADDRESS=bus.address,
            NO_COLOR="1",
            TERM="dumb",
            PYTHONDONTWRITEBYTECODE="1",
        )
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("DISPLAY", None)
        self.proc = subprocess.Popen(
            [sys.executable, str(LAUNCHER), *argv],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def done(self):
        out, err = self.proc.communicate(timeout=30)
        return self.proc.returncode, out, err


@pytest.fixture(scope="module")
def bus():
    from tests.tui_bus import FakeBus

    instance = FakeBus()
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize(
    "method",
    sorted(agent_capabilities.METHODS.values(), key=lambda m: m.name),
    ids=lambda m: m.name,
)
def test_every_cli_subcommand_runs_against_the_standin(bus, method):
    count = len(method.arg_names) - int(method.confirm)
    argv = [*method.cli, *(f"arg{i}" for i in range(count)), "--json"]
    if method.confirm:
        argv.append("--yes")
    code, out, err = Terminal(bus, argv).done()
    assert code == 0, (argv, code, out, err)
    payload = json.loads(out.strip().splitlines()[-1])
    assert isinstance(payload, dict) and payload, (argv, payload)


@pytest.mark.parametrize(
    "method",
    sorted(agent_capabilities.METHODS.values(), key=lambda m: m.name),
    ids=lambda m: m.name,
)
def test_every_slash_command_runs_in_a_pty(bus, method):
    import pty
    import select

    master, slave = pty.openpty()
    env = os.environ.copy()
    env.update(
        DBUS_SESSION_BUS_ADDRESS=bus.address,
        NO_COLOR="1",
        TERM="dumb",
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("DISPLAY", None)
    proc = subprocess.Popen(
        [sys.executable, str(LAUNCHER)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave)
    output = ""

    def read_for(seconds=20):
        nonlocal output
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    output += os.read(master, 65536).decode("utf-8", "replace")
                except OSError:
                    return

    def until(text, seconds=20):
        deadline = time.monotonic() + seconds
        while text not in output and time.monotonic() < deadline:
            read_for(0.1)
        assert text in output, f"missing {text!r} in {output!r}"

    try:
        until("chronoa>", 20)
        before = output.count("chronoa>")
        line = "/" + " ".join(method.slash)
        count = len(method.arg_names) - int(method.confirm)
        args = [f"arg{i}" for i in range(count)]
        os.write(
            master, (line + (" " + " ".join(args) if args else "") + "\n").encode()
        )
        if method.confirm:
            until("Continue?", 20)
            os.write(master, b"y\n")
        deadline = time.monotonic() + 20
        while output.count("chronoa>") <= before and time.monotonic() < deadline:
            read_for(0.1)
        assert output.count("chronoa>") > before, (
            f"{line} produced no answer: {output!r}"
        )
        os.write(master, b"/quit\n")
        read_for(1.0)
        assert proc.wait(timeout=5) == 0, output
        assert "did not work" not in output, output
        assert "Unknown command" not in output, output
    finally:
        try:
            os.close(master)
        except OSError:
            pass
