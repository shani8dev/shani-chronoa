"""Skill: keep your own notes - add, list, search, change and remove them.

This is *your* notebook, deliberately not the same thing as `remember_fact`:

- `remember_fact` is what the assistant believes and puts into its own prompts
  ("the meeting is at 4pm"). It is consent-gated behind `memory-sense-enabled`,
  it expires, and it is withheld the moment that switch goes off.
- This store is a plain list of notes you wrote. It never reaches a model, and
  nothing withholds it, because it is a file you can open and read.

Keeping those two apart is the whole point. A notebook that quietly fed itself
into prompts would be a privacy switch nobody asked for, and calling this
"memory" would invite exactly that confusion - so the name is `notes`, and the
description says where it lives.

Stored as one JSON file under the state directory, resolved per call through
`files.state_home()` so a redirected test run cannot write into the real
`~/.local/state` (the trap `tests/conftest.py` guards for triggers).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_NAME = "notes"


def _path() -> Path:
    return files.state_home() / "shani-chronoa" / "notes.json"


def _load() -> list:
    """Every note, oldest first. A corrupt or absent file reads as none."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [n for n in data if isinstance(n, dict) and n.get("text")] \
        if isinstance(data, list) else []


def _save(notes: list) -> None:
    path = _path()
    files.ensure_private_dir(path.parent)
    path.write_text(json.dumps(notes, indent=1), encoding="utf-8")


def _text(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) else ""


def _find(notes: list, query: str) -> "list[int]":
    """Indexes matching `query`: by id prefix, else by substring of the text."""
    q = query.lower()
    exact = [i for i, n in enumerate(notes) if str(n.get("id", "")).startswith(q)]
    if exact:
        return exact
    return [i for i, n in enumerate(notes) if q in str(n.get("text", "")).lower()]


def _render(notes: list) -> str:
    return "\n".join(f"  {n['id']}  {n['text']}" for n in notes)


def _run(arguments: dict) -> str:
    action = _text(arguments, "action") or "list"
    notes = _load()

    if action == "add":
        text = _text(arguments, "text")
        if not text:
            return "Nothing to add. Give the note's text as 'text'."
        note = {"id": f"n{len(notes) + 1}_{int(time.time()) % 100000}", "text": text}
        notes.append(note)
        _save(notes)
        return f"Saved as {note['id']}."

    if action in ("list", "search"):
        if action == "search":
            query = _text(arguments, "query")
            if not query:
                return "Search for what? Give 'query'."
            hits = [notes[i] for i in _find(notes, query)]
            if not hits:
                return f"No note matches {query!r}. There are {len(notes)} note(s)."
            return f"{len(hits)} of {len(notes)} note(s) match {query!r}:\n{_render(hits)}"
        if not notes:
            return "You have no notes. Add one with action='add' and 'text'."
        return f"{len(notes)} note(s):\n{_render(notes)}"

    if action in ("remove", "delete"):
        target = _text(arguments, "id")
        if not target:
            return "Which note? Give its 'id'."
        hits = _find(notes, target)
        if not hits:
            return f"No note matches {target!r}."
        removed = notes.pop(hits[0])
        _save(notes)
        return f"Removed {removed['id']}. {len(notes)} note(s) left."

    return f"Unknown action {action!r}. Use add, list, search or remove."


SCHEMA = {
    "type": "function",
    "function": {
        "name": _NAME,
        "description": (
            "Keep your own notes in a plain list stored on this machine: 'add' one "
            "from 'text', 'list' them, 'search' by text or id, 'remove' one by 'id'. "
            "Never sent to a model. For what the assistant itself believes and uses "
            "in its replies, use remember_fact instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "search", "remove"],
                    "description": "add: store 'text'. list: every note. search: "
                                   "'query' against text or id. remove: 'id'.",
                },
                "text": {"type": "string", "description": "The note's words, for action='add'."},
                "query": {"type": "string", "description": "What to look for, for action='search'."},
                "id": {"type": "string", "description": "A note's id, for action='remove'."},
            },
        },
    },
}

SKILLS = [Skill(name=_NAME, schema=SCHEMA, run=_run)]