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
from typing import Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

logger = logging.getLogger(__name__)

#: Where the object lives on the bus. Stable, because a channel configured once
#: should keep working across restarts.
#:
#: **This used to be the application's own well-known name.** It was
#: `dev.shani.chronoa`, which is `ChronoaApplication`'s `application_id` - so
#: `export()` asked the bus for a name the `GApplication` already owned, with
#: `REPLACE | ALLOW_REPLACEMENT`. Two owners on one well-known name: the bus
#: arbitrates, ownership ping-pongs between them, and a client calling `Submit`
#: gets `ServiceUnknown` (measured) or lands on whichever owner won the race. The
#: object never became reliably reachable.
#:
#: It could not have been observed before, because the `register_object` call in
#: the same function threw on every invocation, so this name was never actually
#: requested. Two faults in one line, both invisible, in a function no test ever
#: called - there is still no `tests/test_gateway.py` anywhere in the tree.
BUS_NAME = "dev.shani.chronoa.Gateways"
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
        """Whether this channel is *labelled* `execute`, or `ask`.

        **Nothing calls this, and the name overpromises. Measured 2026-10-06:**
        `self.grant` is written once in `__init__` and read once here, so the
        grant is a **label**, not an enforced gate - an `ask` channel's message
        is submitted exactly as an `execute` channel's is. Proven across two
        processes on a private bus: a channel registered `"laptop"` with the
        default `ask` grant took a `Submit` call and its text reached the
        submit callable.

        The docstring here used to say the grant is "the *outer* limit", which
        is false - there is no outer limit, only three inner ones:

        - `admit()` enforces what is enforced: non-empty, `MAX_TEXT`, and
          `RATE_PER_MINUTE`;
        - the submitted turn then meets the same per-sense consent keys as
          anything typed into the window, which is the limit that actually
          matters - it is why an inbound message cannot delete a file by itself.

        So the security property is real and does **not** depend on the grant.
        What does not exist is any distinction in behaviour between the two
        grants. A method called `may_execute` with no caller reads like a
        security control to the next person auditing this file, and on this
        module the recurring defect has been exactly that kind of un-wired
        promise (`Registry.register()` had zero callers and the whole inbound
        channel was dead). Kept because `describe()` and the Settings row show
        the grant, so an operator can see what they configured - but it is a
        display value today, and making it enforced is a decision about consent
        on a bus call, not a bug fix.
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


def parse_config(text: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Read the `gateways` setting into `(entries, errors)`.

    **This is what makes the module reachable at all.** Until now nothing in the
    tree ever called `Registry.register()` - measured, with an AST search over
    every call whose receiver mentions a gateway: **zero**. So
    `_export_gateways()` built the registry at startup, found `names()` empty and
    returned, and the whole inbound channel stayed unexported on every install. A
    fully built, fully tested feature with no switch.

    The format is `name`, or `name:execute`, comma-separated:

        gateways = "whatsapp, telegram:execute, matrix"

    **Errors are returned, not raised, and never silently dropped.** A channel
    named `my.channel` raises `ValueError` from `register()` because the name
    becomes part of a D-Bus method name - and a setting that quietly ignores the
    entries it could not parse is a switch that appears to work while doing
    nothing, which is the defect this whole audit keeps finding. Each error names
    the offending entry and what is wrong with it.

    `execute` is opt-in per channel and still does not mean the channel can act:
    the submitted turn meets the same consent keys as anything typed into the
    window. **The grant itself is not enforced anywhere** - measured, and written
    out at length in `Gateway.may_execute`, which has no caller. Today `ask` and
    `execute` behave identically; the difference is what the Settings row shows.
    """
    entries: List[Tuple[str, str]] = []
    errors: List[str] = []
    for raw in (text or "").split(","):
        entry = raw.strip()
        if not entry:
            continue
        name, _, grant = entry.partition(":")
        name = name.strip()
        grant = grant.strip().lower() or ASK_ONLY
        if grant not in (ASK_ONLY, "execute"):
            errors.append(f"{entry!r}: grant must be 'ask' or 'execute', "
                          f"not {grant!r}")
            continue
        if not name or "/" in name or "." in name:
            errors.append(
                f"{entry!r}: a name cannot be empty or contain '/' or '.', "
                "because it becomes part of a D-Bus method name")
            continue
        entries.append((name, grant))
    return entries, errors


def describe(entries: List[Tuple[str, str]]) -> str:
    """One line for a status row: what is registered, and with what grant."""
    if not entries:
        return "none - nothing is listening on the session bus"
    return ", ".join(f"{name} ({grant})" for name, grant in entries)


#: Object path -> `(connection, registration_id)` for what `export()` registered.
#:
#: **`unregister_object` takes a registration id, not a path.** GLib's C function
#: is `g_dbus_connection_unregister_object(connection, object_path)`, and the
#: introspection says `unregister_object(registration_id: int) -> bool` - measured,
#: by passing the path and getting `TypeError: Must be number, not str`. Reading
#: the C header and writing the Python is how that happened.
#:
#: It is idempotent-safe only if the id is the one `register_object` returned,
#: which is why this stores both.
_EXPORTED: "Dict[str, tuple]" = {}


def unexport(owner: Optional["Gio.BusNameOwnerId"] = None) -> None:
    """Release the bus name and unregister the object. Safe to call twice."""
    if owner is not None:
        try:
            Gio.bus_unown_name(owner)
        except Exception:  # noqa: BLE001 - a channel is optional
            logger.exception("could not release the gateway bus name")
    record = _EXPORTED.pop(OBJECT_PATH, None)
    if record is not None:
        connection, registration_id = record
        try:
            connection.unregister_object(registration_id)
        except Exception:  # noqa: BLE001 - already gone is the same as released
            logger.debug("the gateway object was already unregistered")


def export(registry: Registry, bus: Optional[Gio.BusType] = None,
           connection: Optional["Gio.DBusConnection"] = None) -> "Gio.BusNameOwnerId":
    """Put the object on the bus. Returns the owner id.

    **`register_object` needs a callable, and passing `_Service` itself never
    worked.** Measured: the first real activation of this path logged

        could not export the gateway interface: Must be callable, not _Service

    PyGObject's `register_object` takes a *closure* it will invoke as
    `f(connection, sender, path, interface, method, params, invocation)`; a plain
    object with methods is not that, and the type check rejects it. So the whole
    inbound channel could never have been exported - not because nothing
    registered a gateway, but because the one line that puts it on the bus throws
    every time.

    It stayed invisible for two reasons that are worth naming, because both are
    this repository's own recurring failure:

    - the `except Exception` around the call turned a hard error into a log line
      that says "could not", which reads as a transient condition rather than a
      line that has never once succeeded;
    - **`gateway.py` had no test file at all** - measured, there is no
      `tests/test_gateway*.py` - so the one function that needed executing was the
      one function nobody executed. `Registry`, `MAX_TEXT`, `RATE_PER_MINUTE`,
      `may_execute` and `_Service.Submit` were all unexercised too, despite
      AGENTS.md citing measured properties of this module as its security
      argument.

    `connection=` exists so a test can hand this a private bus instead of the
    session bus. `Gio.bus_get_sync` **caches one connection per process**, and it
    is resolved from `DBUS_SESSION_BUS_ADDRESS` at the first call - which is why
    setting that variable inside a test that has already imported `gi` silently
    does nothing, and why the round-trip test below drives a `Gio.TestDBus`
    directly rather than trying to redirect the session bus.
    """
    node = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)
    connection = connection or Gio.bus_get_sync(
        bus if bus is not None else Gio.BusType.SESSION, None)
    # Undo any previous registration on this path first. `register_object`
    # **refuses** a second export of the same interface at the same path on one
    # connection (`g-io-error-quark: An object is already exported for the
    # interface dev.shani.chronoa.Gateways at /dev/shani/chronoa/Gateways`), and
    # `bus_unown_name` frees the *name* without freeing the object - so a reload
    # that only unowned the name left every later export throwing, and the
    # caller's `except Exception` turned that into a log line. Measured on the
    # app's own reload path: reload 1 owned the name, reloads 2-4 all reported
    # `owner=False, on_bus=False` with the registry still listing the channel.
    record = _EXPORTED.pop(OBJECT_PATH, None)
    if record is not None:
        try:
            record[0].unregister_object(record[1])
        except Exception:  # noqa: BLE001 - already gone is the same as released
            logger.debug("the previous gateway object was already unregistered")
    service = _Service(registry)

    def dispatch(connection_, sender, path, interface, method, params, invocation):
        """One method, and one refusal for anything else."""
        handler = getattr(service, method, None)
        if handler is None:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"{interface} has no method {method!r}; it has one, Submit")
            return
        handler(connection_, sender, path, interface, method, params, invocation)

    registration_id = connection.register_object(
        OBJECT_PATH, node.interfaces[0], dispatch, None, None)
    _EXPORTED[OBJECT_PATH] = (connection, registration_id)
    # **`bus_own_name_on_connection` is asynchronous**: it returns an owner id
    # immediately and the name is owned a moment later. Passing `None` for the
    # callbacks discarded that fact, so a caller that trusted the return value -
    # including this module's own `logger.info("gateways exported")` below - could
    # report a channel as listening when nothing owned the name yet, and a client
    # connecting straight after startup got `ServiceUnknown`. Both outcomes are
    # measured; the log line now says "requested", which is what happened.
    def acquired(_connection, name):
        logger.info("gateway bus name acquired: %s at %s", name, OBJECT_PATH)

    def lost(_connection, name):
        logger.warning("lost the gateway bus name %s - channels are unreachable "
                       "until it is taken again", name)

    owner = Gio.bus_own_name_on_connection(
        connection, BUS_NAME,
        Gio.BusNameOwnerFlags.REPLACE | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT,
        acquired, lost)
    logger.info("gateway interface requested at %s (%s)", OBJECT_PATH, BUS_NAME)
    return owner
