#!/usr/bin/env python3
"""End-to-end check of dev.shani.chronoa.Agent on a real session bus.

Run with: dbus-run-session -- python3 tools/check_agent_bus.py
"""

import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr/lib/shani-chronoa"))

import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from shani_chronoa import ask_bridge
from shani_chronoa.agent_contract import BUS_NAME, IFACE, PATH
from shani_chronoa.agent_service import AgentService
from shani_chronoa.asyncbridge import AsyncBridge


class Assistant:
    async def handle(self, text, sink=None):
        sink.fire("on_tool_start", "demo_tool", {"path": "/tmp/x"})
        # What a gated skill does: ask from the tool executor thread.
        import asyncio, contextvars
        ctx = contextvars.copy_context()
        answer = await asyncio.get_running_loop().run_in_executor(
            None, ctx.run,
            lambda: ask_bridge.ask("Move 14 files?", ["Allow this once", "Deny"],
                                   timeout=10))
        sink.fire("on_tool_finish", "demo_tool", {}, answer or "(no answer)",
                  bool(answer), ())
        sink.fire("on_text", f"answer={answer}")
        return answer


class App:
    def __init__(self):
        self.assistant = Assistant()
        self._async = AsyncBridge()


def main() -> int:
    loop = GLib.MainLoop()
    app = App()
    service = AgentService(app)
    service.export()

    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    events = []
    failures = []
    state = {"turn": None, "approved": False}

    def done():
        loop.quit()

    def on_event(connection, sender, path, iface, signal, params):
        ev = json.loads(params.unpack()[0])
        events.append(ev)
        print("event:", json.dumps(ev, ensure_ascii=False)[:180], flush=True)
        if ev.get("event") == "approval" and not state["approved"]:
            state["approved"] = True

            def approve():
                # Off the main loop: a blocking call from the thread that
                # dispatches this object's methods cannot be answered.
                r = conn.call_sync(BUS_NAME, PATH, IFACE, "Approve",
                                   GLib.Variant("(ss)", (ev["id"], ev["options"][0])),
                                   None, Gio.DBusCallFlags.NONE, 4000, None)
                print("Approve ->", r.unpack(), flush=True)
                if r.unpack()[0] is not True:
                    failures.append("Approve said no pending question")

            threading.Thread(target=approve, daemon=True).start()
        elif ev.get("event") == "done":
            GLib.idle_add(done)

    conn.signal_subscribe(BUS_NAME, IFACE, "Event", PATH, None,
                          Gio.DBusSignalFlags.NONE, on_event)

    def start():
        def turn():
            try:
                r = conn.call_sync(BUS_NAME, PATH, IFACE, "Turn",
                                   GLib.Variant("(s)", ("tidy the desktop",)),
                                   None, Gio.DBusCallFlags.NONE, 4000, None)
                state["turn"] = r.unpack()[0]
                print("Turn ->", state["turn"], flush=True)
            except Exception as exc:
                failures.append(f"Turn failed: {exc}")
                GLib.idle_add(done)

        threading.Thread(target=turn, daemon=True).start()
        return False

    GLib.timeout_add(100, start)
    GLib.timeout_add(15000, done)
    loop.run()
    service.unexport()
    app._async.shutdown()

    names = [e.get("event") for e in events]
    if not state["turn"]:
        failures.append("no turn id")
    if "approval" not in names:
        failures.append("no approval event")
    if "reply" not in names:
        failures.append("no reply event")
    if names.count("done") != 1:
        failures.append(f"expected one done, got {names}")
    reply = next((e.get("text") for e in events if e.get("event") == "reply"), "")
    if reply != "answer=Allow this once":
        failures.append(f"reply was {reply!r}, not the approved option")
    if state["approved"] and "approval_closed" in names:
        failures.append("an answered approval was also closed")

    print("\nFAIL" if failures else "\nPASS", failures or names, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
