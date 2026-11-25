"""Skill: keep a shopping list across turns and sessions.

This is the same kind of store as `notes`: a plain JSON file the person can
open without Chronoa, resolved per call through `files.state_home()` so a
redirected test run cannot write into their real `~/.local/state`. It never
reaches a model's prompt and needs no consent switch - unlike `todo_list`,
which is a task tracker a model plans against. A shopping list is the user's
own prose, and withholding it would just make the assistant forget the milk.

A store that cannot be read is refused, not reset: silently replacing a
corrupt list with an empty one destroys the list and reports success.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_NAME = "shopping_list"
_ACTIONS = ("add", "list", "check", "uncheck", "remove", "clear")
_MAX_ITEMS = 500
_MAX_CHARS = 300


def _path() -> Path:
    return files.state_home() / "shani-chronoa" / "shopping_list.json"


def _load() -> "tuple[list | None, str]":
    """(items, problem). `None` means unreadable, which is not the same as empty."""
    path = _path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], ""
    except OSError as exc:
        return None, f"Could not read {path}: {files.describe(exc, path, 'read')}"
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        return None, (
            f"Refusing to use the shopping list at {path}: it is not valid JSON "
            f"({exc}). Nothing has been changed."
        )
    if not isinstance(parsed, list) or not all(isinstance(i, dict) for i in parsed):
        return None, (
            f"Refusing to use the shopping list at {path}: it does not contain a "
            f"list of items. Nothing has been changed."
        )
    return parsed, ""


def _save(items: list) -> str:
    path = _path()
    try:
        files.ensure_private_dir(path.parent)
        path.write_text(json.dumps(items, indent=1), encoding="utf-8")
        files.restrict_file(path)
    except OSError as exc:
        return files.describe(exc, path, "write the shopping list to")
    return ""


def _text(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) else ""


def _find(items: list, query: str) -> "list[int]":
    """Id prefix first, then a substring of the item text."""
    q = query.lower()
    exact = [i for i, item in enumerate(items) if str(item.get("id", "")).startswith(q)]
    return exact or [
        i for i, item in enumerate(items) if q in str(item.get("text", "")).lower()
    ]


def _next_id(items: list) -> str:
    numbers = []
    for item in items:
        head = str(item.get("id", "")).split("_", 1)[0]
        if head.startswith("s") and head[1:].isdigit():
            numbers.append(int(head[1:]))
    return f"s{max(numbers, default=0) + 1}_{int(time.time()) % 100000}"


def _render(items: list) -> str:
    if not items:
        return "The shopping list is empty."
    left = [i for i in items if not i.get("checked")]
    lines = [f"{len(items)} item(s), {len(left)} still to get:"]
    for item in items:
        mark = "[x]" if item.get("checked") else "[ ]"
        lines.append(f"  {item['id']}  {mark}  {item.get('text', '')}")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    action = _text(arguments, "action") or "list"
    if action not in _ACTIONS:
        return f"Unknown action {action!r}. Use {', '.join(_ACTIONS)}."

    items, problem = _load()
    if items is None:
        return problem

    if action == "add":
        text = _text(arguments, "text")
        if not text:
            return "Nothing to add. Give the item's text as 'text'."
        if len(text) > _MAX_CHARS:
            return f"That item is {len(text)} characters; the limit is {_MAX_CHARS}."
        if len(items) >= _MAX_ITEMS:
            return f"The list already holds {_MAX_ITEMS} items, the maximum."
        item = {
            "id": _next_id(items),
            "text": text,
            "checked": False,
            "created_at": time.time(),
        }
        items.append(item)
        failure = _save(items)
        return failure or f"Added {item['id']}: {text}.\n{_render(items)}"

    if action == "list":
        return _render(items)

    if action == "clear":
        if not items:
            return "The shopping list is already empty, so nothing was removed."
        failure = _save([])
        return failure or f"Removed all {len(items)} item(s). That is permanent."

    target = _text(arguments, "id")
    if not target:
        return "Which item? Give its 'id' (or part of its text). Nothing was changed."
    hits = _find(items, target)
    if not hits:
        return f"No item matches {target!r}. Nothing was changed."
    if action == "remove":
        removed = items.pop(hits[0])
        failure = _save(items)
        return failure or f"Removed {removed['id']}. {_render(items)}"

    found = items[hits[0]]
    found["checked"] = action == "check"
    failure = _save(items)
    word = "checked off" if found["checked"] else "unchecked"
    return failure or f"{word} {found['id']}.\n{_render(items)}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": _NAME,
        "description": (
            "Keep your own shopping list in a plain local file: 'add' an item "
            "from 'text', 'list' what is on it, 'check' or 'uncheck' one by "
            "'id', 'remove' one, or 'clear' the list. Checked items stay on the "
            "list so you can see what is already in the basket. This is the "
            "user's own list and is never sent to a model or a server."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "What to do. Defaults to list.",
                },
                "text": {
                    "type": "string",
                    "description": "The item to buy, for action='add'.",
                },
                "id": {
                    "type": "string",
                    "description": "An item's id (or part of its text), for check, uncheck and remove.",
                },
            },
        },
    },
}

SKILLS = [Skill(name=_NAME, schema=SCHEMA, run=_run)]
