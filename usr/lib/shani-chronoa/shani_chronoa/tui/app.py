"""The terminal session: turn the engine's events into scrollback.

This is orchestration, not assistant logic. It sends turns and approvals over
the Agent client, hands events to the renderer, and reads the person's next
line. Every decision about what a reply says, which tools run, or which
conversation is open stays in the engine.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional, TextIO

from shani_chronoa.tui import approve, commands, display
from shani_chronoa.tui.agent import AgentClient
from shani_chronoa.tui.input import Input

TURN_TIMEOUT = 600.0
BANNER = "Chronoa — type a question, /help for commands, Ctrl+C to interrupt."


def history_path() -> Optional[str]:
    root = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return str(Path(root) / "shani-chronoa" / "tui-history")


def extract_paths(text: str) -> "tuple[str, list[str]]":
    """Pull `@file` tokens out of a message and leave everything else alone."""
    kept, paths = [], []
    for word in text.split():
        if word.startswith("@") and len(word) > 1:
            path = Path(word[1:]).expanduser()
            if path.is_file():
                paths.append(str(path.resolve()))
                continue
        kept.append(word)
    return " ".join(kept), paths


class Session:
    """One turn or many: pump the signal stream until the engine says done."""

    def __init__(
        self,
        client: AgentClient,
        on_event: Callable[[dict], None],
        responder: Optional[Callable[[dict], str]] = None,
    ) -> None:
        self.client = client
        self.on_event = on_event
        self.responder = responder
        self.current_turn = ""

    def run_turn(self, text: str, options: Optional[dict] = None) -> dict:
        turn = (
            self.client.turn_with(text, options) if options else self.client.turn(text)
        )
        self.current_turn = turn
        chunks: list[str] = []
        error: Optional[str] = None
        events: list[dict] = []
        deadline = time.monotonic() + TURN_TIMEOUT
        while True:
            batch = self.client.drain()
            if not batch:
                if time.monotonic() > deadline:
                    error = error or "timed out waiting for the engine"
                    break
                self.client.tick()
                continue
            for event in batch:
                if event.get("turn") not in (None, turn):
                    continue  # another turn's events are not ours to draw
                events.append(event)
                self.on_event(event)
                kind = event.get("event")
                if kind == "reply":
                    chunks.append(str(event.get("text", "")))
                elif kind == "approval":
                    label = self.responder(event) if self.responder else ""
                    self.client.approve(event.get("id") or turn, label)
                elif kind == "error":
                    error = str(event.get("text", "")) or "the engine reported an error"
                elif kind == "done":
                    self.current_turn = ""
                    return {
                        "reply": "".join(chunks),
                        "error": error,
                        "turn": turn,
                        "events": events,
                    }
        self.current_turn = ""
        return {
            "reply": "".join(chunks),
            "error": error,
            "turn": turn,
            "events": events,
        }


def show_inventory(client: AgentClient, name: str, sections, stream: TextIO = None):
    """Plain listing of read-only Inventory sections; no engine writes."""
    out = stream if stream is not None else sys.stdout
    for section in sections:
        data = client.inventory(section)
        items = data.get("items", []) if isinstance(data, dict) else []
        if isinstance(items, dict):
            # a section that is one record (status, privacy) - key: value lines
            print(f"{section}:", file=out)
            for key, value in items.items():
                if isinstance(value, (list, tuple)):
                    value = ", ".join(str(v) for v in value) or "none"
                elif isinstance(value, bool):
                    value = "yes" if value else "no"
                print(
                    f"  {key.replace('_', ' ')}: {value if value != '' else '-'}",
                    file=out,
                )
            if data.get("error"):
                print(f"  error: {data['error']}", file=out)
            continue
        if not isinstance(items, list):
            items = [items]
        print(f"{section} ({len(items)}):", file=out)
        for item in items[:30]:
            if isinstance(item, dict):
                title_keys = ("title", "name", "task", "tool", "model")
                title_key = next((key for key in title_keys if item.get(key)), None)
                title = str(item[title_key]) if title_key else "item"
                rest = ", ".join(
                    f"{key}={value}"
                    for key, value in item.items()
                    if key not in title_keys and not isinstance(value, (dict, list))
                )
                print(f"  - {title}{': ' + rest if rest else ''}", file=out)
            else:
                print(f"  - {item}", file=out)
        if isinstance(data, dict) and data.get("error"):
            print(f"  error: {data['error']}", file=out)


class Interactive:
    """The prompt loop. It renders; the engine answers."""

    def __init__(
        self,
        client: AgentClient,
        renderer,
        history: Optional[str] = None,
        stream: TextIO = None,
    ) -> None:
        self.client = client
        self.renderer = renderer
        self.input = Input(history if history is not None else history_path())
        self.out = stream if stream is not None else sys.stdout
        self.pending_options: dict = {}
        self.stop = False

    def run(self) -> int:
        self.input.install()
        print(BANNER, file=self.out, flush=True)
        try:
            while not self.stop:
                try:
                    line = self.input.read()
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print(file=self.out)
                    continue
                self.handle(line)
        finally:
            self.input.close()
        return 0

    def handle(self, line: str) -> None:
        name, arg = commands.parse(line)
        if name:
            # one command going wrong must not end the session
            try:
                self.command(name, arg)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"/{name} did not work: {type(exc).__name__}: {exc}", file=self.out
                )
            return
        text, paths = extract_paths(line)
        options = dict(self.pending_options)
        self.pending_options = {}
        if paths:
            options["paths"] = paths
        if not text.strip():
            return
        self.ask(text.strip(), options or None)

    def command(self, name: str, arg: str) -> None:
        if name == "help":
            for line in commands.help_lines():
                print(line, file=self.out)
            return
        if name == "quit":
            self.stop = True
            return
        if name == "new":
            self.pending_options["conversation"] = "new"
            print("Next message starts a new conversation.", file=self.out)
            return
        if name == "resume":
            if not arg.strip():
                print("Usage: /resume <conversation id or title>", file=self.out)
            else:
                self.pending_options["conversation"] = arg.strip()
                print(f"Next message continues {arg.strip()}.", file=self.out)
            return
        if name == "skills":
            show_inventory(self.client, name, ("skills", "capabilities"), self.out)
            return
        method, args = commands.resolve(name, arg)
        if method is not None:
            self.capability(method, args)
        elif name in commands.SECTION_COMMANDS:
            show_inventory(
                self.client, name, (commands.SECTION_COMMANDS[name],), self.out
            )
        else:
            print(f"Unknown command /{name}. Try /help.", file=self.out)

    def capability(self, method: str, args: list) -> None:
        """One additive Agent call: ask first when a person must say yes."""
        from shani_chronoa.agent_capabilities import METHODS

        spec = METHODS[method]
        values = list(args)
        if method == "SettingsSet" and values and str(values[0]).endswith("-api-key"):
            import getpass

            values[1] = getpass.getpass("Secret (not shown): ")
        if spec.confirm:
            if "confirm" not in spec.arg_names:
                values.append(True)
            else:
                answer = input(
                    "This changes consent or sends data off this machine. Continue? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Cancelled.", file=self.out)
                    return
                values.append(True)
        try:
            data = self.client.capability(method, *values)
        except Exception as exc:  # noqa: BLE001 - one command must not end the session
            print(f"/{method} did not work: {type(exc).__name__}: {exc}", file=self.out)
            return
        display.show(display.with_tasks(self.client, method, data), self.out, method)

    def _respond(self, event: dict) -> str:
        # The renderer already drew the number choices and the detail.
        return approve.choose(
            event.get("options") or [], stream=self.out, show_options=False
        )

    def ask(self, text: str, options: Optional[dict] = None) -> dict:
        session = Session(self.client, self.renderer.render_event, self._respond)
        try:
            result = session.run_turn(text, options)
        except KeyboardInterrupt:
            turn = session.current_turn
            if turn and self.client.cancel(turn):
                print("\nTurn cancelled.", file=self.out)
            else:
                print(
                    "\nInterrupted. This engine has no way to stop that turn.",
                    file=self.out,
                )
            return {"reply": "", "error": "interrupted", "turn": turn, "events": []}
        print(file=self.out, flush=True)
        if result["error"]:
            print(f"error: {result['error']}", file=self.out)
        return result
