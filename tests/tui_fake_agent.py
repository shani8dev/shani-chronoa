#!/usr/bin/env python3
"""A stand-in dev.shani.chronoa.Agent on a private session bus.

It serves the real contract from shani_chronoa.agent_contract and emits the
real event shapes from agent_service.py. It exists so a terminal client can be
driven end to end without a model, a GUI, or the person's own bus. A stand-in
that accepted any method would hide the mismatch this test is meant to catch:
the methods here are exactly those in the contract's XML.
"""

from __future__ import annotations

import json
import signal
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from shani_chronoa.agent_contract import BUS_NAME, IFACE, PATH, XML  # noqa: E402

OPTIONS = ["Allow this once", "Deny"]
ANSWER = ["Here is ", "the answer ", "from the stand-in agent."]

SECTIONS = {
    "models": [{"task": "conversion", "model": "qwen3:4b", "why": "local first"}],
    "conversations": [
        {
            "id": "conv-1",
            "title": "Holiday plans",
            "updated": 1760000000,
            "message_count": 4,
        }
    ],
    "skills": [{"name": "read_file", "description": "read a text file", "gate": ""}],
    "capabilities": [
        {
            "tool": "read_file",
            "group": "files",
            "label": "Read a file",
            "description": "reads text",
            "consent_key": "",
            "example": "read a file",
        }
    ],
    "routines": [{"name": "morning", "phrases": ["good morning"]}],
    "triggers": [{"name": "battery low", "enabled": True}],
    "privacy": {
        "privacy_mode": True,
        "granted_consent_key": None,
        "senses": [],
        "recent_egress": 0,
    },
    "senses": [{"name": "battery", "key": "battery-sense-enabled", "enabled": True}],
    "memory": [
        {"content": "Likes tea", "source": "chat", "created_at": 1, "metadata": {}}
    ],
    "status": {
        "llm_model": "qwen3:4b",
        "llm_installed": True,
        "stt_available": False,
        "tts_engine": "espeak-ng",
        "privacy_mode": True,
    },
}


CAP_DATA = {
    "settings_rows": [
        {
            "name": "privacy-mode",
            "label": "Privacy mode",
            "value": "true",
            "kind": "b",
            "secret": False,
            "consent_key": "",
        }
    ],
    "models": {
        "items": [
            {
                "engine": "local",
                "name": "qwen3:4b",
                "detail": "llama.cpp server",
                "installed": True,
                "current": True,
            }
        ],
        "how": "local first",
    },
    "setup": {
        "state": {"brain": "ready"},
        "needs_setup": False,
        "stt_catalogue": [("en", "whisper-small")],
    },
    "memory": {
        "facts": [
            {"text": "Likes tea", "created": 1, "source": "chat", "confidence": 0.9}
        ],
        "proposals": [],
    },
    "notices": [
        {
            "id": "notice-1",
            "title": "Something happened",
            "body": "detail",
            "why": "reason",
        }
    ],
    "voice": {
        "engines": [{"name": "espeak-ng", "installed": True, "detail": "speech"}],
        "devices": {
            "input": [
                {
                    "name": "default",
                    "label": "Default",
                    "description": "",
                    "kind": "input",
                }
            ],
            "output": [
                {
                    "name": "default",
                    "label": "Default",
                    "description": "",
                    "kind": "output",
                }
            ],
        },
        "catalogue": [
            {"key": "en", "label": "English", "detail": "", "engine": "espeak-ng"}
        ],
    },
    "activity": {
        "calls": [
            {
                "timestamp": "2026-11-26T00:00:00",
                "tool_name": "read_file",
                "duration_ms": 1,
                "origin": "user",
                "verdict": "ok",
                "evidence": "",
                "undone": False,
            }
        ],
        "truncated": False,
    },
    "diagnostics": {
        "checks": [{"title": "Local model", "status": "working", "detail": "ok"}]
    },
}


def event(turn_id: str, event_kind: str, **fields):
    return {"turn": turn_id, "event": event_kind, **fields}


class FakeAgent:
    def __init__(self) -> None:
        self.turns: dict[str, dict] = {}
        self.connection = None

    # -- D-Bus plumbing -------------------------------------------------

    def dispatch(self, connection, sender, path, interface, method, params, invocation):
        handler = getattr(self, method, None)
        if handler is None:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"the stand-in has no method {method}",
            )
            return
        try:
            handler(params.unpack(), invocation)
        except Exception as exc:  # noqa: BLE001 - the caller is told, in words
            invocation.return_dbus_error(
                "dev.shani.chronoa.Error.FakeFailure", f"{type(exc).__name__}: {exc}"
            )

    def emit(self, payload: dict) -> None:
        if self.connection is None:
            return
        self.connection.emit_signal(
            None,
            PATH,
            IFACE,
            "Event",
            GLib.Variant("(s)", (json.dumps(payload, ensure_ascii=False),)),
        )

    # -- methods --------------------------------------------------------

    def Turn(self, params, invocation):
        (text,) = params
        turn_id = self._start(str(text or ""))
        invocation.return_value(GLib.Variant("(s)", (turn_id,)))

    def TurnWith(self, params, invocation):
        text, options_json = params
        turn_id = self._start(str(text or ""), str(options_json or ""))
        invocation.return_value(GLib.Variant("(s)", (turn_id,)))

    def _start(self, text: str, options_json: str = "") -> str:
        turn_id = "turn-" + uuid.uuid4().hex[:12]
        self._last_id = turn_id
        delay = 0.2 if text == "slow" else 0.06
        queue = [
            event(turn_id, "state", state="thinking"),
            event(
                turn_id,
                "tool",
                tool="read_file",
                text="read_file · path=/tmp/demo.txt",
                args={"path": "/tmp/demo.txt"},
            ),
            event(turn_id, "tool_done", tool="read_file", ok=True, text="two lines"),
        ]
        if options_json:
            queue.append(
                event(turn_id, "reply", text=f"options: {options_json[:120]} ")
            )
        if text == "error":
            queue.append(event(turn_id, "error", text="the stand-in model failed"))
            queue.append(event(turn_id, "done"))
        elif text == "no-approval":
            queue.extend(event(turn_id, "reply", text=chunk) for chunk in ANSWER)
            queue.append(event(turn_id, "reply", text="Done."))
            queue.append(event(turn_id, "done"))
        else:
            queue.extend(event(turn_id, "reply", text=chunk) for chunk in ANSWER)
            queue.append(
                event(
                    turn_id,
                    "approval",
                    id=turn_id,
                    text="Run the command?",
                    options=list(OPTIONS),
                    detail="line one\nline two",
                    kind="confirm",
                )
            )
        self.turns[turn_id] = {
            "queue": queue,
            "waiting": False,
            "delay": delay,
            "approved": None,
            "cancelled": False,
        }
        GLib.timeout_add(int(delay * 1000), self._tick, turn_id)
        return turn_id

    def _tick(self, turn_id: str):
        state = self.turns.get(turn_id)
        if state is None:
            return False
        if state["waiting"]:
            return True
        if not state["queue"]:
            self.turns.pop(turn_id, None)
            return False
        item = state["queue"].pop(0)
        self.emit(item)
        if item["event"] == "approval":
            state["waiting"] = True
        return True

    def Approve(self, params, invocation):
        turn_id, label = params
        state = self.turns.get(str(turn_id))
        ok = state is not None and state["waiting"]
        if ok:
            state["approved"] = str(label)
            state["waiting"] = False
            state["queue"].append(
                event(turn_id, "reply", text=f"Approved with {label}.")
            )
            state["queue"].append(event(turn_id, "done"))
        invocation.return_value(GLib.Variant("(b)", (bool(ok),)))

    def Cancel(self, params, invocation):
        (turn_id,) = params
        state = self.turns.get(str(turn_id))
        ok = state is not None and not state["cancelled"]
        if ok:
            state["cancelled"] = True
            state["queue"].clear()
            state["waiting"] = False
            self.emit(event(turn_id, "notice", text="Turn cancelled"))
            self.emit(event(turn_id, "done"))
        invocation.return_value(GLib.Variant("(b)", (bool(ok),)))

    def Inventory(self, params, invocation):
        (section,) = params
        section = str(section or "")
        if section in SECTIONS:
            payload = {"section": section, "items": SECTIONS[section]}
        else:
            payload = {
                "section": section,
                "error": "unknown section",
                "allowed": sorted(SECTIONS),
            }
        invocation.return_value(
            GLib.Variant("(s)", (json.dumps(payload, ensure_ascii=False),))
        )

    # -- additive capability methods -------------------------------------
    # These shapes are the real engine_ops shapes, asserted by
    # tests/test_agent_capability_parity.py against the real functions.

    def _capability(self, payload, invocation):
        invocation.return_value(
            GLib.Variant("(s)", (json.dumps(payload, ensure_ascii=False, default=str),))
        )

    def SettingsList(self, params, invocation):
        self._capability({"items": CAP_DATA["settings_rows"]}, invocation)

    def SettingsGet(self, params, invocation):
        name = str(params[0] or "")
        row = dict(CAP_DATA["settings_rows"][0])
        row["name"] = name or row["name"]
        self._capability(row, invocation)

    def SettingsSet(self, params, invocation):
        name, value, confirm = params
        if not confirm:
            self._capability({"error": "confirmation required"}, invocation)
        else:
            self._capability(
                {
                    "name": str(name),
                    "label": str(name).replace("-", " "),
                    "value": "<set>",
                    "changed": True,
                },
                invocation,
            )

    def SettingsPreset(self, params, invocation):
        name, confirm = params
        self._capability(
            {"error": "confirmation required"}
            if not confirm
            else {"preset": str(name), "current": str(name)},
            invocation,
        )

    def SettingsEmail(self, params, invocation):
        values_json, confirm = params
        self._capability(
            {"error": "confirmation required"}
            if not confirm
            else {"status": "saved", "message": "", "path": "/tmp/email.json"},
            invocation,
        )

    def ModelsList(self, params, invocation):
        self._capability(CAP_DATA["models"], invocation)

    def ModelsUse(self, params, invocation):
        name, confirm = params
        self._capability(
            {"error": "confirmation required"}
            if not confirm
            else {
                "name": str(name),
                "engine": str(name),
                "how": "chosen",
                "changed": True,
            },
            invocation,
        )

    def SetupState(self, params, invocation):
        self._capability(CAP_DATA["setup"], invocation)

    def SetupStep(self, params, invocation):
        step, value, confirm = params
        self._capability(
            {"error": "confirmation required"}
            if not confirm
            else {"step": str(step), "result": "done", "message": ""},
            invocation,
        )

    def Conversations(self, params, invocation):
        self._capability({"items": SECTIONS["conversations"]}, invocation)

    def ConversationsResume(self, params, invocation):
        self._capability(
            {"session": "/tmp/conv-1.jsonl", "ref": str(params[0])}, invocation
        )

    def ConversationsRename(self, params, invocation):
        self._capability({"ref": str(params[0]), "title": str(params[1])}, invocation)

    def ConversationsDelete(self, params, invocation):
        ref, confirm = params
        self._capability(
            {"error": "confirmation required"}
            if not confirm
            else {"deleted": str(ref)},
            invocation,
        )

    def MemoryList(self, params, invocation):
        self._capability(CAP_DATA["memory"], invocation)

    def MemoryDecide(self, params, invocation):
        item, decision, confirm = params
        self._capability(
            {"error": "confirmation required"}
            if not confirm
            else {"id": str(item), "decision": str(decision)},
            invocation,
        )

    def Routines(self, params, invocation):
        self._capability({"items": SECTIONS["routines"]}, invocation)

    def RoutinesRun(self, params, invocation):
        self._capability(
            {"name": str(params[0]), "request": "good morning"}, invocation
        )

    def Triggers(self, params, invocation):
        self._capability({"items": SECTIONS["triggers"]}, invocation)

    def Notices(self, params, invocation):
        self._capability({"items": CAP_DATA["notices"]}, invocation)

    def NoticesDismiss(self, params, invocation):
        self._capability({"dismissed": str(params[0])}, invocation)

    def VoiceEngines(self, params, invocation):
        self._capability(CAP_DATA["voice"], invocation)

    def Activity(self, params, invocation):
        self._capability(CAP_DATA["activity"], invocation)

    def DiagnosticsProbe(self, params, invocation):
        self._capability(CAP_DATA["diagnostics"], invocation)

    def Write(self, params, invocation):
        instruction, text = params
        invocation.return_value(GLib.Variant("(s)", (f"[{instruction}] {text}",)))

    def FindFiles(self, params, invocation):
        query, limit = params
        invocation.return_value(
            GLib.Variant(
                "(s)",
                (
                    json.dumps(
                        {
                            "how": "keyword",
                            "hits": [{"path": "/tmp/demo.txt"}],
                            "indexing": False,
                        }
                    ),
                ),
            )
        )


def main() -> int:
    agent = FakeAgent()
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    agent.connection = connection
    connection.register_object(PATH, node.interfaces[0], agent.dispatch, None, None)
    Gio.bus_own_name_on_connection(
        connection,
        BUS_NAME,
        Gio.BusNameOwnerFlags.REPLACE | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT,
        None,
        None,
    )
    loop = GLib.MainLoop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, loop.quit, None)
    print("fake agent ready", file=sys.stderr, flush=True)
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
