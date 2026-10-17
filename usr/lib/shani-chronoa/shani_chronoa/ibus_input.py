"""Text into any app, in any script, through IBus - the way dictation tools do it.

Typing by keys reaches only what the keyboard layout can produce: through the
GNOME extension's virtual keyboard, "hello, World!" arrives exactly, but a
Devanagari word has no key to press. An input method does not press keys: it
*commits* text, and every GTK, Qt and Electron app accepts a commit in any
script. Ubuntu's Myna inserts its dictation through IBus for the same reason,
and community speech engines (IBus-Speech-To-Text) commit their results the
same way.

So Chronoa hosts a small IBus engine, `chronoa`, in its own process:

- it passes every key straight through, so having it active changes nothing
  about typing;
- `Commit(text)` on `dev.shani.chronoa.Input` switches IBus to it just long
  enough to commit, then puts the person's own input method back.

Measured 2026-10-15 in a private headless GNOME session: committed through it,
"नमस्ते café" arrived in a GTK entry exactly. Without IBus running, `start()`
does nothing and typing falls back to keys.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

BUS_NAME = "dev.shani.chronoa.Input"
OBJECT_PATH = "/dev/shani/chronoa/Input"
INTERFACE = "dev.shani.chronoa.Input"
ENGINE = "chronoa"

_XML = f"""<node><interface name="{INTERFACE}">
<method name="Commit"><arg type="s" name="text"/><arg type="b" direction="out" name="done"/></method>
</interface></node>"""

_STATE: dict = {"engine": None, "bus": None, "owner": 0}


def _engine_type():
    import gi
    gi.require_version("IBus", "1.0")
    from gi.repository import IBus

    class ChronoaEngine(IBus.Engine):
        __gtype_name__ = "ChronoaEngine"

        def __init__(self, **kwargs):
            # IBus makes the engine for the field that already has focus when
            # the switch happens, and sends no focus-in for it - so it is the
            # one to commit into from the moment it exists.
            super().__init__(**kwargs)
            _STATE["engine"] = self

        def do_focus_in(self):
            _STATE["engine"] = self

        def do_enable(self):
            _STATE["engine"] = self

        def do_focus_out(self):
            if _STATE["engine"] is self:
                _STATE["engine"] = None

        def do_process_key_event(self, keyval, keycode, state):
            return False  # every key goes through untouched

    return ChronoaEngine


def start() -> bool:
    """Register the engine and the Commit method; False when IBus is not here."""
    if _STATE["bus"] is not None:
        return True
    try:
        import gi
        gi.require_version("IBus", "1.0")
        from gi.repository import Gio, GObject, IBus
        IBus.init()
        bus = IBus.Bus()
        if not bus.is_connected():
            return False
        engine_type = _engine_type()
        component = IBus.Component(
            name="dev.shani.chronoa.IBus", description="Chronoa text input",
            version="1", license="GPL-3.0", author="Shani OS", homepage="",
            command_line="", textdomain="")
        component.add_engine(IBus.EngineDesc(
            name=ENGINE, longname="Chronoa", description="Text Chronoa types for you",
            language="other", license="GPL-3.0", author="Shani OS", icon="", layout="default"))
        factory = IBus.Factory.new(bus.get_connection())
        factory.add_engine(ENGINE, GObject.type_from_name(engine_type.__gtype_name__))
        bus.register_component(component)
        session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(_XML)
        session.register_object(OBJECT_PATH, node.interfaces[0], _on_call)
        _STATE["owner"] = Gio.bus_own_name_on_connection(
            session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        _STATE.update(bus=bus, factory=factory, component=component)
        return True
    except Exception:  # noqa: BLE001 - no IBus is "type by keys", not a failure
        logger.info("IBus text input is not available", exc_info=True)
        return False


def _on_call(_conn, _sender, _path, _iface, method, params, invocation) -> None:
    from gi.repository import GLib
    if method != "Commit":
        invocation.return_value(GLib.Variant("(b)", (False,)))
        return
    commit(params.unpack()[0], lambda ok: invocation.return_value(GLib.Variant("(b)", (ok,))))


def commit(text: str, done) -> None:
    """Switch to the engine, commit `text` where focus is, switch back; `done(ok)`."""
    from gi.repository import GLib
    bus = _STATE["bus"]
    if bus is None:
        done(False)
        return
    try:
        current = bus.get_global_engine()
        previous: Optional[str] = current.get_name() if current is not None else None
    except Exception:  # noqa: BLE001
        previous = None
    if previous != ENGINE:
        # **Asynchronously.** IBus answers a switch by asking the engine's
        # process - this one - to create the engine; a synchronous call here
        # blocks the very loop that has to answer, and the switch timed out
        # with nothing created (measured: Commit returned False every time).
        bus.set_global_engine_async(ENGINE, -1, None, None, None)
    waited = {"ms": 0}

    def try_commit() -> bool:
        engine = _STATE["engine"]
        if engine is None and waited["ms"] < 1500:
            waited["ms"] += 25
            return True  # the engine has not been focused yet
        ok = engine is not None
        if ok:
            from gi.repository import IBus
            engine.commit_text(IBus.Text.new_from_string(text))
        if previous and previous != ENGINE:
            # The person's own input method back, after the commit lands.
            GLib.timeout_add(150, lambda: (bus.set_global_engine_async(
                previous, -1, None, None, None), False)[1])
        done(ok)
        return False
    GLib.timeout_add(25, try_commit)


def available() -> bool:
    """Whether a running Chronoa offers Commit on the session bus (for a skill)."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                              "org.freedesktop.DBus", "NameHasOwner",
                              GLib.Variant("(s)", (BUS_NAME,)), None,
                              Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        return bool(owner)
    except Exception:  # noqa: BLE001
        return False


def type_via_ibus(text: str) -> bool:
    """For a skill: hand `text` to the running Chronoa to commit; True if it landed."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, "Commit",
                              GLib.Variant("(s)", (text,)), GLib.VariantType.new("(b)"),
                              Gio.DBusCallFlags.NONE, 5000, None)
        return bool(reply.unpack()[0])
    except Exception:  # noqa: BLE001
        return False
