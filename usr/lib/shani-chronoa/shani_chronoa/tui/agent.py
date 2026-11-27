"""Transport only: the terminal client's calls to dev.shani.chronoa.Agent.

Nothing here chooses a model, runs a skill, or keeps a conversation. It calls
the methods in `agent_contract.py`, listens for the one `Event` signal, and
starts the engine when the bus name is missing. Session state and rendering
live in `app.py`; this module is the wire.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Callable, Optional

from gi.repository import Gio, GLib

from shani_chronoa.agent_contract import BUS_NAME, IFACE, PATH

#: The unit being added by the headless-engine job. A missing unit is reported
#: as a clear instruction, never as a crash or a silent retry loop.
ENGINE_UNIT = "shani-chronoa-engine.service"
START_HINT = (
    "Chronoa's engine is not on the session bus. Start it with:\n"
    f"  systemctl --user start {ENGINE_UNIT}\n"
    "or launch the Chronoa app. Then run this command again."
)


def _has_owner(connection) -> bool:
    """GetNameOwner, not a DBusConnection method: PyGObject has no name_has_owner."""
    try:
        result = connection.call_sync(
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


class AgentError(Exception):
    """A call to the Agent failed at the transport layer."""


class EngineUnavailable(AgentError):
    """No Agent on the bus, and nothing could start one."""


class AgentClient:
    """A thin synchronous wrapper over the Agent's D-Bus methods and signal."""

    def __init__(self, connection=None) -> None:
        self._connection = connection
        self._subscription = None
        self._events: list[dict] = []
        self._listener: Optional[Callable[[dict], None]] = None

    @property
    def connection(self):
        return self._connection

    def connect(self):
        if self._connection is None:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if self._subscription is None:
            self._subscription = self._connection.signal_subscribe(
                BUS_NAME,
                IFACE,
                "Event",
                PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_signal,
            )
        return self._connection

    def set_listener(self, listener: Optional[Callable[[dict], None]]) -> None:
        """Called for each event as it arrives, before `drain()` sees it."""
        self._listener = listener

    def _on_signal(self, connection, sender, path, interface, signal, params) -> None:
        try:
            event = json.loads(params.unpack()[0])
        except (TypeError, ValueError, IndexError):
            return
        if not isinstance(event, dict):
            return
        self._events.append(event)
        if self._listener is not None:
            self._listener(event)

    def available(self) -> bool:
        """True when an Agent already owns the bus name."""
        try:
            return _has_owner(self.connect())
        except GLib.Error as exc:
            raise AgentError(f"cannot reach the session bus: {exc}") from exc

    def _wait_for_owner(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if _has_owner(self.connect()):
                return True
            self.tick()
        return False

    def ensure_started(self, timeout: float = 8.0) -> None:
        """Try D-Bus activation, then the user unit; otherwise explain how to start.

        A machine without the unit (the engine split is still landing) gets a
        clean exit with the command to run, not a stack trace or a fake client.
        """
        if self.available():
            return
        try:
            self.connect().call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "StartServiceByName",
                GLib.Variant("(su)", (BUS_NAME, 0)),
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                int(timeout * 1000),
                None,
            )
        except GLib.Error:
            pass  # no .service file yet; the unit is the next attempt
        if self._wait_for_owner(min(2.0, timeout)):
            return
        try:
            subprocess.run(
                ["systemctl", "--user", "start", ENGINE_UNIT],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(2.0, timeout / 2),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if self._wait_for_owner(min(2.0, timeout)):
            return
        raise EngineUnavailable(START_HINT)

    def call(
        self, method: str, signature: str, *args, reply: str, timeout_ms: int = 30000
    ):
        """One method call; GLib.Error becomes AgentError with a stable message."""
        self.connect()
        try:
            result = self._connection.call_sync(
                BUS_NAME,
                PATH,
                IFACE,
                method,
                GLib.Variant(signature, tuple(args)),
                GLib.VariantType(reply),
                Gio.DBusCallFlags.NONE,
                timeout_ms,
                None,
            )
        except GLib.Error as exc:
            raise AgentError(f"{method} failed: {exc.message}") from exc
        return result.unpack()

    def turn(self, text: str) -> str:
        return str(self.call("Turn", "(s)", text, reply="(s)")[0])

    def turn_with(self, text: str, options: dict) -> str:
        return str(
            self.call("TurnWith", "(ss)", text, json.dumps(options), reply="(s)")[0]
        )

    def approve(self, turn_id: str, label: str) -> bool:
        return bool(
            self.call("Approve", "(ss)", str(turn_id), str(label), reply="(b)")[0]
        )

    def cancel(self, turn_id: str) -> bool:
        return bool(self.call("Cancel", "(s)", str(turn_id), reply="(b)")[0])

    def inventory(self, section: str, *, timeout_ms: int = 30000) -> dict:
        raw = self.call(
            "Inventory", "(s)", section, reply="(s)", timeout_ms=timeout_ms
        )[0]
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise AgentError(f"Inventory({section}) returned bad JSON: {exc}") from exc

    def capability(self, method: str, *args) -> dict:
        """Call one additive capability method and decode its single JSON row."""
        from shani_chronoa.agent_capabilities import METHODS

        spec = METHODS[method]
        values = [
            bool(a) if kind == "b" else str(a) for a, (_, kind) in zip(args, spec.args)
        ]
        raw = self.call(method, spec.signature_in, *values, reply="(s)")[0]
        try:
            result = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise AgentError(f"{method} returned bad JSON: {exc}") from exc
        return result if isinstance(result, dict) else {"items": result}

    def close(self) -> None:
        if self._subscription is not None and self._connection is not None:
            try:
                self._connection.signal_unsubscribe(self._subscription)
            except Exception:  # noqa: BLE001 - closing is best-effort
                pass
            self._subscription = None

    # -- signal pumping -------------------------------------------------

    def tick(self, pause: float = 0.01) -> bool:
        """Dispatch queued D-Bus signals; sleep only when the queue was empty."""
        dispatched = GLib.MainContext.default().iteration(False)
        if not dispatched:
            time.sleep(pause)
        return bool(dispatched)

    def drain(self) -> list[dict]:
        """Take the events received since the last drain."""
        events, self._events = self._events, []
        return events
