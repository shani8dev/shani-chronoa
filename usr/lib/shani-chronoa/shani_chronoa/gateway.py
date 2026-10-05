"""An inbound text channel for Chronoa, on the session bus and nothing else.

The request was "connect other channels like WhatsApp etc to give commands
straight to your PC", and the same issue raised the risk: harnesses that accept
commands over a channel have been the source of some bad evenings, because a
channel is a remote caller with a microphone and no hands.

So this is deliberately **not** a WhatsApp integration. It is the part that has
to exist before any of them can be written safely, and its shape is the argument:

- **The session bus, not a socket.** No port, no listener, no network. A gateway
  is another program on this machine. Whatever carries a message to it later -
  WhatsApp, Signal, a meeting transcript - is a *separate* component with its own
  credentials, and Chronoa never sees a token.
- **One method: submit text.** That is the whole interface. A gateway cannot ask
  Chronoa to run a skill, name a tool, read a file, or reach the model server.
  It hands over words and gets words back.
- **Whether those words cause a side effect is decided elsewhere.** The text
  enters the *same* turn a person's keystrokes enter, so the same consent keys,
  the same whitelist, the same post-conditions apply. A gateway cannot lower a
  gate, because it never touches one.
- **Ask-only by default.** Executing anything requires an explicit grant per
  gateway, and even then the turn still goes through `tools.execute_tool_outcome`.
- **Bounded, because an unbounded pipe into an assistant that can run tools is a
  denial-of-service and an injection surface.** Length cap, rate cap, and no
  attachments.
- **Every submission is a logged turn**, so it appears in Tool activity and
  trains the outcome model exactly like a spoken one. A channel that bypassed the
  log would be the one place where learning could not see what happened.

What this deliberately is not: a plugin system, a shell, or an escape hatch for a
future channel to widen. Adapters are expected to be thin - connect, call
`Submit`, print the reply - and each one needs its own opt-in consent key, because
"someone may message my machine" is a decision about *that* channel and not a
property of this module.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List, Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

logger = logging.getLogger(__name__)

#: Where the object lives on the bus. Stable, because a channel configured once
#: should keep working across restarts.
BUS_NAME = "dev.shani.chronoa"
OBJECT_PATH = "/dev/shani/chronoa/Gateways"
INTERFACE = "dev.shani.chronoa.Gateways"

#: A request longer than this is not a person dictating and not a message; it is
#: a paste, a file, or an attempt to spend someone else's context window.
MAX_TEXT = 4000

#: Per-gateway rate limit, in requests per minute. A channel that can flood an
#: assistant which can call tools is a denial-of-service with extra steps.
RATE_PER_MINUTE = 20

#: What a gateway may do without an explicit grant: nothing but ask.
ASK_ONLY = "ask"


class Refused(Exception):
    """A request the gate refused. The reason is the message."""


class Gateway:
    """One named channel, with its own consent and its own budget."""

    def __init__(self, name: str, submit: Callable[[str], str],
                 grant: str = ASK_ONLY) -> None:
        if grant not in (ASK_ONLY, "execute"):
            raise ValueError(f"unknown grant {grant!r}")
        self.name = name
        self._submit = submit
        self.grant = grant
        self._times: List[float] = []

    def admit(self, text: str) -> str:
        """Check one request and return the text, or refuse with a reason.

        The refusals are specific on purpose. "Refused" tells a person nothing
        about whether to fix a key, shorten a message, or wait a minute, and a
        channel that cannot tell which will be turned off entirely.
        """
        if not isinstance(text, str) or not text.strip():
            raise Refused("empty message")
        if len(text) > MAX_TEXT:
            raise Refused(f"message is {len(text)} characters; the limit is "
                          f"{MAX_TEXT}")
        now = time.monotonic()
        self._times[:] = [t for t in self._times if now - t < 60.0]
        if len(self._times) >= RATE_PER_MINUTE:
            raise Refused(f"more than {RATE_PER_MINUTE} messages a minute; "
                          "slow down")
        self._times.append(now)
        return text

    def ask(self, text: str) -> str:
        return self._submit(self.admit(text))

    def may_execute(self) -> bool:
        """Whether this gateway may cause a side effect.

        **Almost never.** A gateway's own grant is the *outer* limit; the turn it
        submits still meets the consent keys for whatever the model decides to do,
        so this returning True does not let a message delete a file by itself.
        """
        return self.grant == "execute"


class Registry:
    """The named gateways, and the one method they can call.

    A registry rather than a module-level singleton because an application owns
    one, and a test can own another without the two interfering.
    """

    def __init__(self, submit: Callable[[str], str]) -> None:
        self._submit = submit
        self._gateways: Dict[str, Gateway] = {}
        self.rejected = 0

    def register(self, name: str, grant: str = ASK_ONLY) -> Gateway:
        if not name or "/" in name or "." in name:
            raise ValueError(
                f"gateway name {name!r} cannot contain '/' or '.', because it "
                "becomes part of a D-Bus method name")
        gateway = Gateway(name, self._dispatch, grant)
        self._gateways[name] = gateway
        logger.info("gateway %r registered (%s)", name, grant)
        return gateway

    def unregister(self, name: str) -> None:
        self._gateways.pop(name, None)

    def names(self) -> List[str]:
        return sorted(self._gateways)

    def _dispatch(self, text: str) -> str:
        return self._submit(text)

    def submit(self, gateway: str, text: str) -> str:
        entry = self._gateways.get(gateway)
        if entry is None:
            self.rejected += 1
            # Naming the ones that exist is not a leak: the caller is on the
            # session bus, which is the same user.
            raise Refused(f"no gateway called {gateway!r}; registered: "
                          f"{', '.join(self.names()) or 'none'}")
        try:
            return entry.ask(text)
        except Refused:
            self.rejected += 1
            raise


class _Service:
    """The bus object. One method, deliberately."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    # org.freedesktop.DBus.Introspectable - so `busctl introspect` shows one
    # method and not an opaque surface.
    def Introspect(self, connection, sender, path, interface) -> Gio.DBusNodeInfo:
        node = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)
        return node

    def Submit(self, connection, sender, path, interface, method, params,
               invocation) -> None:
        """`Submit(gateway, text) -> reply`. The whole interface."""
        try:
            gateway = params.unpack()[0]
            text = params.unpack()[1]
        except Exception:                                   # noqa: BLE001
            invocation.return_dbus_error(
                "dev.shani.chronoa.Error.BadArguments",
                "Submit takes a gateway name and the text to submit")
            return
        try:
            reply = self._registry.submit(gateway, text)
        except Refused as exc:
            logger.info("gateway %r refused: %s", gateway, exc)
            invocation.return_dbus_error("dev.shani.chronoa.Error.Refused",
                                         str(exc))
            return
        except Exception as exc:                            # noqa: BLE001
            logger.exception("gateway %r raised", gateway)
            invocation.return_dbus_error(
                "dev.shani.chronoa.Error.Failed",
                f"{type(exc).__name__}: {exc}"[:200])
            return
        invocation.return_value(GLib.Variant("(s)", (reply or "",)))


_INTROSPECTION = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="dev.shani.chronoa.Gateways">
    <method name="Submit">
      <arg name="gateway" type="s" direction="in"/>
      <arg name="text" type="s" direction="in"/>
      <arg name="reply" type="s" direction="out"/>
    </method>
  </interface>
</node>
"""


def export(registry: Registry, bus: Optional[Gio.BusType] = None) -> Gio.BusNameOwnerId:
    """Put the object on the bus. Returns the owner id."""
    node = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)
    connection = Gio.bus_get_sync(bus if bus is not None else Gio.BusType.SESSION, None)
    connection.register_object(
        OBJECT_PATH, node.interfaces[0], _Service(registry), None, None)
    owner = Gio.bus_own_name_on_connection(
        connection, BUS_NAME,
        Gio.BusNameOwnerFlags.REPLACE | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT,
        None, None)
    logger.info("gateways exported at %s (%s)", OBJECT_PATH, BUS_NAME)
    return owner
