"""Shared helper: run tests/tui_fake_agent.py on a private session bus.

Both the stand-in's contract test and the pseudo-terminal test need the same
thing, so the bus is started in one place. Nothing here touches the person's
own DBUS_SESSION_BUS_ADDRESS except to restore it afterwards.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from shani_chronoa.agent_contract import BUS_NAME, IFACE, PATH  # noqa: E402

FAKE = Path(__file__).resolve().parent / "tui_fake_agent.py"


class FakeBus:
    """A child `dbus-run-session` serving the real Agent interface."""

    def __init__(self, timeout: float = 15.0) -> None:
        self.tmp = tempfile.mkdtemp(prefix="chronoa-tui-bus-")
        address_file = Path(self.tmp) / "address"
        script = 'printf %s "$DBUS_SESSION_BUS_ADDRESS" > "$1"; exec python3 "$2"'
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.proc = subprocess.Popen(
            [
                "dbus-run-session",
                "--",
                "bash",
                "-c",
                script,
                "_",
                str(address_file),
                str(FAKE),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + timeout
        address = ""
        while time.monotonic() < deadline:
            if address_file.exists():
                address = address_file.read_text().strip()
                if address:
                    break
            if self.proc.poll() is not None:
                raise RuntimeError(f"fake agent exited: {self.proc.stderr.read()!r}")
            time.sleep(0.05)
        if not address:
            raise RuntimeError("fake agent never published a bus address")
        self.address = address
        self.previous_address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = address
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        while time.monotonic() < deadline:
            if self.has_owner():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("fake agent never owned " + BUS_NAME)
        self.events: list[dict] = []
        self.connection.signal_subscribe(
            BUS_NAME,
            IFACE,
            "Event",
            PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_event,
        )

    def has_owner(self) -> bool:
        try:
            result = self.connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetNameOwner",
                GLib.Variant("(s)", (BUS_NAME,)),
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            return bool(result.unpack()[0])
        except GLib.Error:
            return False

    def _on_event(self, connection, sender, path, interface, signal, params):
        try:
            self.events.append(json.loads(params.unpack()[0]))
        except (TypeError, ValueError):
            pass

    def call(self, method: str, signature: str, args, reply: str):
        result = self.connection.call_sync(
            BUS_NAME,
            PATH,
            IFACE,
            method,
            GLib.Variant(signature, tuple(args)),
            GLib.VariantType(reply),
            Gio.DBusCallFlags.NONE,
            10000,
            None,
        )
        return result.unpack()

    def wait_for(self, predicate, timeout: float = 10.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.tick()
            found = [event for event in self.events if predicate(event)]
            if found:
                return found
            time.sleep(0.01)
        raise AssertionError(
            "the expected Agent event never arrived: "
            + repr([e.get("event") for e in self.events])
        )

    def tick(self) -> bool:
        return bool(GLib.MainContext.default().iteration(False))

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if self.previous_address is None:
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        else:
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = self.previous_address
