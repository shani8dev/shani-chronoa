#!/usr/bin/env python3
"""Run the real `dev.shani.chronoa.Agent` against a scripted assistant.

The shell's `bin/mock-chronoa.py` owns the same bus name but emits events by
hand; this owns it with the *product* `AgentService`, so the shell can be driven
through the same sink, approval registration and context binding a real turn
uses. A scripted assistant stands in for the model and the tool loop: it emits
a tool pill, asks a permission-shaped question from an executor thread exactly
where a gated skill does, and streams a reply from the answer.

    dbus-run-session -- python3 tools/run_agent_host.py          # one turn, exit
    dbus-run-session -- python3 tools/run_agent_host.py --serve  # stay up

In a shell session the second is the useful one: start it after the bridge,
then answer through the panel.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from shani_chronoa import ask_bridge  # noqa: E402
from shani_chronoa.agent_service import AgentService  # noqa: E402
from shani_chronoa.asyncbridge import AsyncBridge  # noqa: E402

OPTIONS = ["Allow this once", "Allow for this session", "No"]


class ScriptedAssistant:
    """What a real turn does, without a model: one tool, one question, a reply."""

    def __init__(self, text: str) -> None:
        self.text = text

    async def handle(self, text, sink=None):
        sink.fire("on_tool_start", "scan_desktop", {"folder": "~/Desktop"})
        context = contextvars.copy_context()
        answer = await asyncio.get_running_loop().run_in_executor(
            None, context.run,
            lambda: ask_bridge.ask(
                "Chronoa wants to move 14 files", OPTIONS,
                preview="Desktop → Documents/Inbox", timeout=120))
        approved = answer == OPTIONS[0] or answer == OPTIONS[1]
        sink.fire("on_tool_finish", "move_files", {"count": 14},
                  "moved 14 files" if approved else "left the Desktop alone",
                  approved, ())
        for piece in (("Moved 14 files into Documents/Inbox." if approved else
                       "No answer, so I left the Desktop alone.").split(" ")):
            sink.fire("on_text", piece + " ")
        return answer


class _App:
    def __init__(self, text: str) -> None:
        self.assistant = ScriptedAssistant(text)
        self._async = AsyncBridge()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true",
                        help="stay up; turns are driven by the shell")
    args = parser.parse_args()

    app = _App("tidy")
    service = AgentService(app)
    service.export()
    loop = GLib.MainLoop()

    if not args.serve:
        # A self-driving turn, for a bus test with no shell attached.
        def start():
            import threading
            import time

            from gi.repository import Gio

            from shani_chronoa.agent_contract import BUS_NAME, IFACE, PATH

            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            def run():
                time.sleep(0.2)
                connection.call_sync(BUS_NAME, PATH, IFACE, "Turn",
                                     GLib.Variant("(s)", ("tidy my desktop",)),
                                     None, Gio.DBusCallFlags.NONE, 4000, None)

            threading.Thread(target=run, daemon=True).start()
            return False

        GLib.timeout_add(50, start)
        GLib.timeout_add_seconds(15, loop.quit)

    try:
        loop.run()
    finally:
        service.unexport()
        app._async.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
