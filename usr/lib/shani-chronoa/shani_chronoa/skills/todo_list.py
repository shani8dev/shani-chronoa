"""Skill: keep a list of things to do across turns and sessions.

`add_reminder` writes a dated line and is never read back by anything. This is
the other half: a mutable list with statuses, so "what's left" is answerable
and a multi-step job can be tracked without the user holding it in their head
or re-typing it after every turn. The common failure of an assistant asked to
"keep track of this" is to say it will, and then have no list - so the list is
on disk, in a plain JSON file a person can open without Chronoa running.

**The store is resolved per call, never at import time**, and
`$XDG_STATE_HOME` is honoured with the `~/.local/state` fallback. Both halves
are this repo's own recorded lesson: a module-level constant captured from
`$HOME` at import has already twice contaminated a real user's directories
(`AGENTS.md` names `PerceptStore.DURABLE_FILE`, and a full suite run without
`XDG_STATE_HOME` set wrote fixture data into a real
`~/.local/state/shani-chronoa/timers.json`). Resolving late is also what lets
a test point the variable at a temp dir.

**A corrupt store is refused, not reset.** Silently replacing an unreadable
list with an empty one turns a user's saved work into nothing and reports
success, which is the single worst outcome this skill could produce. So a
`ValueError` on load is reported with the path, and nothing is written.

`clear` is destructive-sounding and is gated with everything else, rather than
being carved out as "just a list" - the list is the user's data, and a model
that empties it has destroyed something the user cannot reconstruct from the
transcript.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "todo-list-enabled"
_ACTIONS = ("add", "update", "remove", "list", "clear")
_STATUSES = ("pending", "in_progress", "blocked", "completed")
_MAX_ITEMS = 500
_MAX_CHARS = 500

SCHEMA = {
    "type": "function",
    "function": {
        "name": "todo_list",
        "description": (
            "Keep a list of tasks that survives between turns and restarts: "
            "add an item, change its status, mark what it is blocked by, or "
            "list what is outstanding. Use this to track a multi-step job "
            "rather than holding it in the conversation. Requires the "
            "'todo-list-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": f"What to do: {', '.join(_ACTIONS)}. Defaults to list.",
                },
                "id": {
                    "type": "integer",
                    "description": "Which item, as shown by the list action. Required for update and remove.",
                },
                "content": {
                    "type": "string",
                    "description": "The task text. Required for add; replaces the text on update.",
                },
                "status": {
                    "type": "string",
                    "description": f"One of {', '.join(_STATUSES)}. Only meaningful for add and update.",
                },
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Ids of items this one is waiting on, e.g. [2]. Empty "
                        "clears it."
                    ),
                },
            },
        },
    },
}


def _store_path() -> Path:
    """Where the list lives, resolved per call - see the module docstring."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa" / "todos.json"


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"keeping a task list is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Reading files and listing folders needs no such "
            f"permission - this one writes something that persists."
        )
    return True, ""


def _load() -> "tuple[list | None, str]":
    """(items, problem). `None` items means the store is unreadable.

    Distinct from an empty list on purpose: "you have no tasks" and "your task
    list could not be read" are different facts, and conflating them destroys
    the user's work without telling them.
    """
    path = _store_path()
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
            f"Refusing to use the task list at {path}: it is not valid JSON "
            f"({exc}). Nothing has been changed - fix or move that file, then "
            f"try again."
        )
    if not isinstance(parsed, list) or not all(isinstance(i, dict) for i in parsed):
        return None, (
            f"Refusing to use the task list at {path}: it does not contain a "
            f"list of tasks. Nothing has been changed."
        )
    return parsed, ""


def _save(items: list) -> str:
    path = _store_path()
    try:
        # mkdir then chmod, not mkdir(mode=...): masked by the umask, so it lands
        # permissive. Incidental before - the parent was only ever private because
        # `triggers.py` chmod'd the same tree.
        files.ensure_private_dir(path.parent)
        path.write_text(json.dumps(items, indent=1), encoding="utf-8")
        files.restrict_file(path)
    except OSError as exc:
        return files.describe(exc, path, "write the task list to")
    return ""


def _render(items: list) -> str:
    if not items:
        return "The task list is empty."
    mark = {"pending": "[ ]", "in_progress": "[~]", "blocked": "[!]",
            "completed": "[x]"}
    outstanding = [i for i in items if i.get("status") != "completed"]
    done = len(items) - len(outstanding)
    lines = [f"{len(items)} task(s), {len(outstanding)} outstanding, {done} completed:"]
    for item in items:
        blocked = item.get("blocked_by") or []
        note = f"  (blocked by {', '.join(str(b) for b in blocked)})" if blocked else ""
        lines.append(
            f"  {item.get('id')}. {mark.get(item.get('status'), '[?]')} "
            f"{item.get('content', '')}{note}"
        )
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "list").strip().lower()
    if action not in _ACTIONS:
        return f"Action must be one of {', '.join(_ACTIONS)}, not {action!r}."

    items, problem = _load()
    if items is None:
        return problem

    if action == "list":
        if arguments.get("content"):
            return (
                "content was given with action=list, which does not use it. "
                f"Use add to write a task. {_render(items)}"
            )
        return _render(items)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the task list: {reason}"

    if action == "clear":
        if not items:
            return "The task list is already empty, so nothing was removed."
        count = len(items)
        failure = _save([])
        if failure:
            return failure
        return f"Removed all {count} task(s) from the list. That is permanent."

    if action == "add":
        content = (arguments.get("content") or "").strip()
        if not content:
            return (
                "No content was given, so nothing was added. Pass the task "
                "text as content."
            )
        if len(content) > _MAX_CHARS:
            return (
                f"That task is {len(content)} characters, over the {_MAX_CHARS} "
                f"limit. Shorten it."
            )
        if len(items) >= _MAX_ITEMS:
            return (
                f"The list already holds {len(items)} tasks, the maximum. "
                f"Complete or remove some before adding more."
            )
        status = (arguments.get("status") or "pending").strip().lower()
        if status not in _STATUSES:
            return f"Status must be one of {', '.join(_STATUSES)}, not {status!r}."
        blocked = arguments.get("blocked_by")
        if blocked is not None and not isinstance(blocked, list):
            return "blocked_by must be a list of task ids, e.g. [2]."
        item = {
            "id": (max((i.get("id", 0) for i in items), default=0) or 0) + 1,
            "content": content,
            "status": status,
            "blocked_by": [b for b in (blocked or [])],
            "created_at": time.time(),
        }
        items.append(item)
        failure = _save(items)
        if failure:
            return failure
        return f"Added task {item['id']}: {content!r} [{status}].\n{_render(items)}"

    raw_id = arguments.get("id")
    if raw_id is None:
        return f"Which task? Pass id, as shown by the list action. Nothing was changed."
    try:
        wanted = int(raw_id)
    except (TypeError, ValueError):
        return f"id must be a whole number, not {raw_id!r}. Nothing was changed."
    found = next((i for i in items if i.get("id") == wanted), None)
    if found is None:
        known = ", ".join(str(i.get("id")) for i in items) or "none"
        return f"There is no task with id {wanted} (existing ids: {known}). Nothing was changed."

    if action == "remove":
        items = [i for i in items if i.get("id") != wanted]
        failure = _save(items)
        if failure:
            return failure
        return f"Removed task {wanted}. That is permanent.\n{_render(items)}"

    if "status" in arguments and arguments.get("status") is not None:
        status = str(arguments.get("status")).strip().lower()
        if status not in _STATUSES:
            return f"Status must be one of {', '.join(_STATUSES)}, not {status!r}. Nothing was changed."
        found["status"] = status
    if arguments.get("content") is not None:
        content = str(arguments.get("content")).strip()
        if not content:
            return "content was empty, so the task text was not changed."
        if len(content) > _MAX_CHARS:
            return f"That task is {len(content)} characters, over the {_MAX_CHARS} limit. Nothing was changed."
        found["content"] = content
    if "blocked_by" in arguments and arguments.get("blocked_by") is not None:
        blocked = arguments.get("blocked_by")
        if not isinstance(blocked, list):
            return "blocked_by must be a list of task ids, e.g. [2]. Nothing was changed."
        found["blocked_by"] = list(blocked)
    found["updated_at"] = time.time()
    failure = _save(items)
    if failure:
        return failure
    return f"Updated task {wanted}.\n{_render(items)}"


SKILLS = [Skill(name="todo_list", schema=SCHEMA, run=_run)]
