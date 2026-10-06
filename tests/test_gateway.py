"""The inbound channel, which had no test file at all.

Measured before writing this: there is **no `tests/test_gateway*.py`** in the
tree, for the module AGENTS.md's "Channels and the jail" section cites as its
security argument - "one method, and it reaches no tool", `MAX_TEXT`,
`RATE_PER_MINUTE`, `may_execute`, `Registry.submit`. None of it had ever been
executed.

**Two faults sat in the one function that could have shown that.**

**One: `register_object` was given an object, not a callable.** PyGObject invokes
the third argument as
`f(connection, sender, path, interface, method, params, invocation)`; a plain
object with methods is not that, and the type check rejects it. Measured:

    could not export the gateway interface: Must be callable, not _Service

Every activation attempt threw, and the `except Exception` around it turned a
line that had never once worked into a log line reading "could not" - which looks
transient. `test_export_actually_exports` is the assertion that would have caught
it, and `test_the_object_can_be_registered` below is the shape of it in
isolation.

**Two: the bus name was the application's own.** `BUS_NAME` was
`dev.shani.chronoa`, which is `ChronoaApplication`'s `application_id`, so
`export()` asked the bus for a name the `GApplication` already owned with
`REPLACE | ALLOW_REPLACEMENT`. Two owners on one well-known name: the bus
arbitrates, ownership ping-pongs, and a client calling `Submit` gets
`ServiceUnknown`. `test_the_gateway_does_not_claim_the_applications_own_name` pins
it.

The second fault could not have been found by the first, because the first threw
before the name was ever requested - which is why "fix the first error" and
"the feature now works" are different claims, and only running it settles which
one you have.

**Why the round trip uses `Gio.TestDBus` and not a private `dbus-daemon`.**
`Gio.bus_get_sync` caches **one** session-bus connection per process, resolved
from `DBUS_SESSION_BUS_ADDRESS` at first call. A test that spawns its own
`dbus-daemon` and sets that variable *after* importing `gi` silently keeps using
the real bus - measured: the service owned `:1.7503` on the user's bus while the
client sat on `:1.0` of the private one, and every call returned `ServiceUnknown`
while the log said `gateway bus name acquired`. This file's own first attempt
spent a long time chasing that. `Gio.TestDBus` gives a private bus with no
environment variable at all, and `export(..., connection=)` exists so both ends
can be handed the same one.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from shani_chronoa import gateway as gw  # noqa: E402


# ── the setting's grammar ────────────────────────────────────────────────

def test_an_empty_setting_means_no_channel_is_listening():
    """The default state has to be "nothing is on the bus", not "an empty channel"."""
    assert gw.parse_config("") == ([], [])
    assert gw.parse_config("   ") == ([], [])
    assert gw.describe([]) == "none - nothing is listening on the session bus"


@pytest.mark.parametrize("text,expected", [
    ("whatsapp", [("whatsapp", "ask")]),
    ("whatsapp, telegram:execute", [("whatsapp", "ask"), ("telegram", "execute")]),
    ("a,,b", [("a", "ask"), ("b", "ask")]),
    ("  spaced  ", [("spaced", "ask")]),
    ("upper:EXECUTE", [("upper", "execute")]),
    ("a:execute,b:ask", [("a", "execute"), ("b", "ask")]),
])
def test_the_grammar_is_what_the_schema_documents(text, expected):
    entries, errors = gw.parse_config(text)
    assert entries == expected
    assert errors == [], f"these are documented as valid: {errors}"


@pytest.mark.parametrize("text,bad_bit", [
    ("my.channel", "cannot be empty or contain"),
    ("a/b", "cannot be empty or contain"),
    (":execute", "cannot be empty or contain"),
    ("a:delete", "must be 'ask' or 'execute'"),
    ("a:EXEC", "must be 'ask' or 'execute'"),
])
def test_a_bad_entry_is_reported_rather_than_ignored(text, bad_bit):
    """The whole point: a setting that silently drops what it cannot parse.

    A channel name becomes part of a D-Bus method name, so `my.channel` is not a
    near-miss - it cannot work. Ignoring it would leave a switch that appears set
    and does nothing, which is the defect this area keeps producing.
    """
    entries, errors = gw.parse_config(text)
    assert entries == [], f"{text!r} was accepted as {entries}"
    assert errors, f"{text!r} was dropped with no explanation"
    assert any(bad_bit in e for e in errors), errors


def test_one_bad_entry_does_not_take_the_good_ones_with_it():
    entries, errors = gw.parse_config("good, my.channel, other:delete, also-good")
    assert entries == [("good", "ask"), ("also-good", "ask")]
    assert len(errors) == 2


def test_describe_says_the_grant_not_just_the_name():
    text = gw.describe(gw.parse_config("a, b:execute")[0])
    assert "a (ask)" in text and "b (execute)" in text


# ── the registry's own limits ────────────────────────────────────────────

def _registry(seen=None):
    seen = seen if seen is not None else []
    return gw.Registry(lambda text: (seen.append(text), "ack")[1]), seen


def test_registering_and_naming():
    registry, _ = _registry()
    registry.register("whatsapp")
    registry.register("telegram", "execute")
    assert registry.names() == ["telegram", "whatsapp"]      # sorted
    registry.unregister("whatsapp")
    assert registry.names() == ["telegram"]


def test_a_name_that_cannot_be_a_method_is_refused_at_registration():
    """The reason `parse_config` checks it, asserted at the layer that enforces it."""
    registry, _ = _registry()
    for bad in ("my.channel", "a/b", ""):
        with pytest.raises(ValueError):
            registry.register(bad)


def test_an_unknown_grant_is_refused():
    registry, _ = _registry()
    with pytest.raises(ValueError):
        registry.register("a", "delete-everything")


def test_an_unknown_channel_is_refused_and_says_which_exist():
    registry, _ = _registry()
    registry.register("whatsapp")
    with pytest.raises(gw.Refused) as raised:
        registry.submit("signal", "hello")
    assert "no gateway called 'signal'" in str(raised.value)
    assert "whatsapp" in str(raised.value)
    assert registry.rejected == 1


def test_submitted_text_reaches_the_window_entry():
    registry, seen = _registry()
    registry.register("whatsapp")
    assert registry.submit("whatsapp", "what time is it") == "ack"
    assert seen == ["what time is it"], (
        "the text did not reach the window's own entry, which is the entire "
        "security argument: a channel that bypassed it would meet no gate")


def test_an_empty_message_is_refused():
    registry, seen = _registry()
    registry.register("a")
    for text in ("", "   "):
        with pytest.raises(gw.Refused) as raised:
            registry.submit("a", text)
        assert "empty" in str(raised.value)
    assert seen == []


def test_an_over_long_message_is_refused_with_the_number():
    """A refusal that does not say what was wrong is a channel that gets turned off."""
    registry, seen = _registry()
    registry.register("a")
    over = "x" * (gw.MAX_TEXT + 1)
    with pytest.raises(gw.Refused) as raised:
        registry.submit("a", over)
    message = str(raised.value)
    assert str(len(over)) in message and str(gw.MAX_TEXT) in message, message
    assert seen == []
    # And exactly at the limit is allowed.
    assert registry.submit("a", "x" * gw.MAX_TEXT) == "ack"


def test_the_rate_limit_is_real_and_counts_only_the_last_minute():
    registry, seen = _registry()
    registry.register("a")
    for i in range(gw.RATE_PER_MINUTE):
        registry.submit("a", f"message {i}")
    with pytest.raises(gw.Refused) as raised:
        registry.submit("a", "one too many")
    assert str(gw.RATE_PER_MINUTE) in str(raised.value), (
        "the refusal does not say the limit, so the caller cannot tell a rate "
        "limit from a size limit")
    assert len(seen) == gw.RATE_PER_MINUTE, (
        "a refused message still reached the window entry")
    # Aging out: pretend every earlier one was a minute ago.
    for gateway in registry._gateways.values():
        gateway._times[:] = [t - 61.0 for t in gateway._times]
    assert registry.submit("a", "later") == "ack"


def test_execute_is_a_grant_not_a_promotion():
    """`may_execute` True does not mean the channel can act on its own.

    It is the *outer* limit only. The turn it submits still meets the same consent
    keys as anything typed into the window, so this is asserted as a statement
    about the grant rather than a promise about the turn.
    """
    registry, _ = _registry()
    asking = registry.register("a")
    acting = registry.register("b", "execute")
    assert asking.may_execute() is False
    assert acting.may_execute() is True
    assert asking.grant == gw.ASK_ONLY
    # And an unknown grant cannot be smuggled through the constructor.
    with pytest.raises(ValueError):
        gw.Gateway("c", lambda text: "", "execute-and-delete")


# ── the export, which had never worked ───────────────────────────────────

def test_the_gateway_does_not_claim_the_applications_own_name():
    """**The second fault.** Two owners on one well-known name is a coin toss."""
    from shani_chronoa.app.application import ChronoaApplication
    assert gw.BUS_NAME != ChronoaApplication.__mro__[0].__init__ and True
    application_id = "dev.shani.chronoa"
    # Read it off the class rather than hard-coding, so a rename of either one
    # fails here instead of silently re-creating the collision.
    import inspect
    source = inspect.getsource(ChronoaApplication.__init__)
    assert application_id in source, (
        "the application's id moved; re-check that BUS_NAME still differs from it")
    assert gw.BUS_NAME != application_id, (
        f"the gateway claims {gw.BUS_NAME!r}, which is the application's own "
        "well-known name - export() then contends with GApplication for it")


def test_the_introspection_declares_exactly_one_method():
    """The claim AGENTS.md makes about this module, asserted on the XML itself."""
    assert '<method name="Submit">' in gw._INTROSPECTION
    assert gw._INTROSPECTION.count("<method") == 1
    assert gw._INTROSPECTION.count("<interface") == 1


def test_the_object_can_be_registered():
    """`register_object` needs a callable. Asserting this *shape* is the cheap guard.

    The full round trip is below; this exists because the failure mode was a
    `TypeError` from deep inside PyGObject, and the shape assertion says why.
    """
    registry, _ = _registry()
    info = Gio.DBusNodeInfo.new_for_xml(gw._INTROSPECTION)
    assert info.interfaces[0].name == gw.INTERFACE
    assert [m.name for m in info.interfaces[0].methods] == ["Submit"], (
        "the interface must declare exactly Submit, because GDBus refuses "
        "anything else before the closure runs - which is why the guard inside "
        "dispatch() cannot be reached over D-Bus")
    service = gw._Service(registry)
    assert callable(getattr(service, "Submit")), (
        "_Service must expose the method the closure will dispatch to")
    assert not callable(service), (
        "the object itself must NOT be handed to register_object - it needs a "
        "closure, which is exactly the bug this asserts against")


# ── the real thing, on a private bus ─────────────────────────────────────

@pytest.fixture
def bus():
    """A private bus and a connection to it, with no environment variable.

    **Function-scoped, not module-scoped**, and that is a real constraint rather
    than tidiness: `register_object` refuses a second export of the same
    interface at the same path on one connection (`An object is already exported
    for the interface dev.shani.chronoa.Gateways at /dev/shani/chronoa/Gateways`),
    and `bus_unown_name` releases the *name* without unregistering the *object*.
    So a shared connection would let the first export in this file decide the
    outcome of every one after it - which is the same class of accident as the
    cached session bus, one level down.

    `Gio.TestDBus` rather than a spawned `dbus-daemon` plus
    `DBUS_SESSION_BUS_ADDRESS`, because `Gio.bus_get_sync` caches one connection
    per process and resolves the address at first call - so redirecting it after
    `gi` is imported silently does nothing. Measured: the service owned
    `:1.7503` on the real bus and the client sat on `:1.0`, and every call failed
    with `ServiceUnknown` while the log cheerfully said the name was acquired.
    """
    testbus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    # **`get_bus_address()` returns None until `up()`** on this GLib. Measured, and
    # it fails as `TypeError: Argument 0 does not allow None as a value` from
    # three lines later, which reads like the connection call is wrong rather
    # than like the bus was never started.
    testbus.up()
    address = testbus.get_bus_address()
    assert address, "the private bus has no address even after up()"
    connection = Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    yield testbus, connection
    connection.close_sync(None)


def _pump(ms=400):
    """Let the connection's worker deliver a call and its reply."""
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < deadline:
        while context.pending():
            context.iteration(False)
        import time
        time.sleep(0.01)


def test_export_actually_exports_and_a_client_can_submit(bus):
    """**The assertion that would have caught it.**

    End to end on a real bus: export, wait for name acquisition, call `Submit`
    from the same connection, and check the text reached the registry's own
    submit callable. Every previous version of this module would have failed
    here - the first on `TypeError: Must be callable`, the second on
    `ServiceUnknown`.
    """
    _testbus, connection = bus
    seen = []
    registry = gw.Registry(lambda text: (seen.append(text), "ack")[1])
    registry.register("whatsapp")
    registry.register("telegram", "execute")

    owner = gw.export(registry, connection=connection)
    try:
        assert owner is not None, "export returned no owner id"
        _pump(600)                       # bus_own_name_on_connection is async

        def call(name, text, done):
            def finished(_c, result, _conn=connection):
                try:
                    done.append(("ok", _c.call_finish(result).unpack()[0]))
                except GLib.Error as exc:
                    done.append(("error", exc.message))
            connection.call(
                gw.BUS_NAME, gw.OBJECT_PATH, gw.INTERFACE, "Submit",
                GLib.Variant("(ss)", (name, text)),
                GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE,
                5000, None, finished)

        known = []
        call("whatsapp", "what time is it", known)
        _pump()
        assert known and known[0][0] == "ok", (
            f"a registered channel could not submit: {known}")
        assert seen == ["what time is it"], (
            "the submission returned ok but the text never reached the window "
            f"entry, which saw {seen}")

        unknown = []
        call("signal", "hello", unknown)
        _pump()
        assert unknown and unknown[0][0] == "error"
        assert "Refused" in unknown[0][1], unknown[0][1]

        toolong = []
        call("whatsapp", "x" * (gw.MAX_TEXT + 1), toolong)
        _pump()
        assert toolong and toolong[0][0] == "error"
        assert str(gw.MAX_TEXT) in toolong[0][1], toolong[0][1]
    finally:
        Gio.bus_unown_name(owner)


def test_an_unregistered_method_is_refused_rather_than_crashing(bus):
    """One method means one method - and note *who* guarantees it.

    **GDBus does, not the guard in `dispatch()`.** A method absent from the
    interface info passed to `register_object` is refused by GDBus before the
    closure is ever entered, with `UnknownMethod`. Measured, and it shows in the
    mutations: deleting the `if handler is None` branch outright leaves this file
    **fully green**, because nothing can reach that branch over D-Bus.

    Worth recording rather than quietly dropping, for two reasons. The guard is
    not dead in principle - it is the right thing to have if the interface ever
    grows a second method whose handler is missing - and an equivalent mutant is
    information: it says the property being asserted comes from the framework, so
    a future edit that widens the XML without adding a handler is the change that
    would make the guard load-bearing, and this test is what would notice.
    """
    _testbus, connection = bus
    registry, _ = _registry()
    registry.register("a")
    owner = gw.export(registry, connection=connection)
    try:
        _pump(500)
        out = []
        def finished(_c, result):
            try:
                out.append(("ok", _c.call_finish(result)))
            except GLib.Error as exc:
                out.append(("error", exc.message))
        connection.call(
            gw.BUS_NAME, gw.OBJECT_PATH, gw.INTERFACE, "RunAnything",
            GLib.Variant("(s)", ("rm -rf /",)), None,
            Gio.DBusCallFlags.NONE, 5000, None, finished)
        _pump()
        assert out and out[0][0] == "error", (
            f"an unregistered method did not error: {out}")
        assert "UnknownMethod" in out[0][1], out[0][1]
        # GDBus's own wording is `No such method "RunAnything"` - accurate, and it
        # does *not* point at the one method the interface has. Recorded because
        # it is the reason the guard in `dispatch()` is written the way it is,
        # even though D-Bus cannot currently reach it.
        assert "RunAnything" in out[0][1], out[0][1]
    finally:
        Gio.bus_unown_name(owner)


def test_nothing_is_exported_when_no_channel_is_configured():
    """The default state has to be no bus name at all.

    `_export_gateways()` returns before calling `export()` when the registry is
    empty, so a machine that has not asked for an inbound interface does not have
    one - which is the property that makes "off unless asked for" true.
    """
    from shani_chronoa.app import ChronoaApplication
    app = ChronoaApplication()
    registry, _ = _registry()
    app._gateways = registry
    app._gateway_owner = None
    app.config.set("gateways", "")
    app._export_gateways()
    assert registry.names() == []
    assert app._gateway_owner is None, (
        "an empty configuration still claimed a bus name, so a machine that "
        "never asked for a channel has an inbound interface")
    app._gateway_owner = None


def test_reloading_keeps_the_channel_reachable(bus, monkeypatch):
    """**The bug that only appeared once the feature worked.**

    Editing a channel name in Settings reloads rather than restarting, so
    `export()` runs repeatedly on one connection. And **`register_object` refuses
    a second export of the same interface at the same path** -
    `g-io-error-quark: An object is already exported for the interface
    dev.shani.chronoa.Gateways at /dev/shani/chronoa/Gateways` - while
    `bus_unown_name` frees the *name* and leaves the *object* registered.

    Measured before the fix, on the app's own reload path: reload 1 owned the
    name, reloads 2, 3 and 4 all reported `owner=False, on_bus=False` while the
    registry still listed the channel. The Settings row would have said "on the
    bus as whatsapp" while nothing was listening.

    The fix needed the **registration id**, because `unregister_object` is
    introspected as `unregister_object(registration_id: int)` even though the C
    function takes an object *path* - passing the path raises `TypeError: Must be
    number, not str`. So this test asserts the reload works, not that a
    particular unregister call is spelled correctly.
    """
    _testbus, connection = bus
    # Point the module's own `bus_get_sync` at the private bus, so the *real*
    # `export()` runs - including `register_object`, which is the whole point.
    monkeypatch.setattr(gw.Gio, "bus_get_sync",
                        lambda *a, **k: connection, raising=False)
    from shani_chronoa.app import ChronoaApplication
    app = ChronoaApplication()
    registry, _ = _registry()
    app._gateways = registry
    app._gateway_owner = None
    app.config.set("gateways", "whatsapp")

    def on_bus():
        names = connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "ListNames", None,
            GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, 5000, None)
        return gw.BUS_NAME in names.unpack()[0]

    for attempt in range(1, 4):
        app._reload_gateways()
        _pump(500)
        assert registry.names() == ["whatsapp"]
        assert app._gateway_owner is not None, (
            f"reload {attempt} produced no owner id, so the channel is not "
            f"reachable after editing it (owners so far were fine on reload 1 "
            f"only - see the docstring)")
        assert on_bus(), (
            f"reload {attempt} left the bus name unowned, so the channel "
            "stopped working the moment its name was edited")

    # And removing the last channel must take the interface back down.
    app.config.set("gateways", "")
    app._reload_gateways()
    _pump(400)
    assert not on_bus(), (
        "clearing the setting left the bus name owned, so a machine that "
        "removed every channel still has an inbound interface")
    app._gateway_owner = None


# ── the wiring that makes any of this reachable ─────────────────────────

def test_the_setting_reaches_the_registry(monkeypatch):
    """Driven, not grepped: set the key, call the loader, read the registry.

    The gap this closes is the original one: `_export_gateways()` ran at startup,
    built the registry, found it empty and returned - because **nothing anywhere
    ever called `Registry.register()`** (measured with an AST search). A module
    with no test file is how that survived.
    """
    from shani_chronoa.app import ChronoaApplication
    app = ChronoaApplication()
    registry, _ = _registry()
    app._gateways = registry
    app._gateway_owner = None
    monkeypatch.setattr(gw, "export", lambda *a, **k: "OWNER-ID")

    app.config.set("gateways", "whatsapp, telegram:execute")
    app._export_gateways()
    assert registry.names() == ["telegram", "whatsapp"], (
        "the setting was read but no channel was registered, which is exactly "
        "the state this module has been in on every install")
    assert app._gateway_entries == [("whatsapp", "ask"), ("telegram", "execute")]
    assert app._gateway_errors == []


def test_a_reload_replaces_the_channels_rather_than_adding_to_them(monkeypatch):
    """Removing a name must actually remove it, or a channel outlives its removal."""
    from shani_chronoa.app import ChronoaApplication
    app = ChronoaApplication()
    registry, _ = _registry()
    app._gateways = registry
    app._gateway_owner = None
    monkeypatch.setattr(gw, "export", lambda *a, **k: "OWNER-ID")

    app.config.set("gateways", "whatsapp, telegram")
    app._export_gateways()
    app.config.set("gateways", "whatsapp")
    app._export_gateways()
    assert registry.names() == ["whatsapp"], (
        f"a removed channel is still registered: {registry.names()}")


def test_a_bad_entry_is_collected_rather_than_swallowed(monkeypatch):
    from shani_chronoa.app import ChronoaApplication
    app = ChronoaApplication()
    registry, _ = _registry()
    app._gateways = registry
    app._gateway_owner = None
    monkeypatch.setattr(gw, "export", lambda *a, **k: "OWNER-ID")

    app.config.set("gateways", "good, my.channel")
    app._export_gateways()
    assert registry.names() == ["good"]
    assert len(app._gateway_errors) == 1, (
        "the unparseable entry was dropped without a word, so the setting looks "
        "like it worked while half of it did not")