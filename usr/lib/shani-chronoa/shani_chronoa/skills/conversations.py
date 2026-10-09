"""Skill: the saved conversations - list, search, open, start, rename, copy, export, delete.

Chronoa keeps every conversation (see `conversation_store.py`), so "what did we say
about the router last week?", "go back to the trip conversation", "start
fresh" and "save this chat to my notes" are all answerable. Harvested from
assistd (`/fork`, `/switch`, `reminisce` over earlier sessions), codex
(`/resume`, `/fork`, `/rename`, `/export`) and goose's chatrecall.

Opening or starting a conversation changes the index; the app notices after
the turn and switches the window to it, so this reply lands in the
conversation it was asked in. `search` never looks in the open conversation -
that is already in context. Deleting is permanent and uses the same
`file-delete-enabled` consent as deleting a file, because it is one.
"""

from __future__ import annotations

import time

from shani_chronoa import files, conversation_store
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "file-delete-enabled"
_ACTIONS = ("list", "search", "open", "new", "rename", "copy", "export", "delete")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "conversations",
        "description": (
            "The user's saved Chronoa conversations. list: recent ones; search: find what was said in "
            "earlier conversations (query); open: continue one (which = title or id); new: start a fresh "
            "one; rename (which, title); copy: duplicate one to try another way, optionally keeping only "
            "its first keep_turns user messages; export: save one as Markdown (path); delete: remove one "
            "permanently (needs 'file-delete-enabled')."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "which": {"type": "string", "description": "A conversation's title (or part of it) or id. "
                                                       "Defaults to the open one for rename/copy/export."},
            "query": {"type": "string", "description": "search: the words to look for in what was said, "
                                                       "e.g. 'router'."},
            "title": {"type": "string"},
            "path": {"type": "string", "description": "export: where to save, e.g. '~/Documents/chat.md'."},
            "keep_turns": {"type": "integer"},
        }, "required": ["action"]},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, f"deleting is turned off (enable '{_CONSENT_KEY}' in Settings)"
    return True, ""


def _when(ts: float) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ts or 0))


def _which(root, arguments: dict) -> str:
    ref = (arguments.get("which") or "").strip()
    return ref or conversation_store.index(root)["active"]


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    root = conversation_store.session_dir()
    if action == "list":
        items = [s for s in conversation_store.list_sessions(root) if s["messages"] or s["active"]][:15]
        if not items:
            return "There are no saved conversations yet."
        return "Conversations, most recent first:\n" + "\n".join(
            f"- {s['title']} ({s['messages']} messages, {_when(s['updated'])}){' - open now' if s['active'] else ''}"
            for s in items)
    if action == "search":
        # Qwen3-1.7B put the words in `which` (eval case past-chat, 2026-10-09):
        # for a search there is nothing else it could mean, so take it.
        query = (arguments.get("query") or arguments.get("which") or "").strip()
        if not query:
            return "Say what to look for with query."
        hits = conversation_store.search(root, query, exclude=conversation_store.index(root)["active"])
        if not hits:
            return f"Nothing in earlier conversations mentions all of: {query}."
        return f"Found in earlier conversations ({len(hits)}):\n" + "\n".join(
            f"- [{h['title']}] {'you' if h['role'] == 'user' else 'Chronoa'}: {h['snippet']}" for h in hits)
    if action == "new":
        conversation_store.new_session(root)
        return "Started a new conversation; the previous one is saved under Conversations."
    if action == "open":
        sid = conversation_store.switch(root, arguments.get("which") or "")
        if not sid:
            return f"No single conversation matches {arguments.get('which')!r}; list them to pick one."
        title = conversation_store.index(root)["sessions"][sid].get("title") or sid
        return f"Opened the conversation {title!r}; it continues from where it stopped."
    if action == "rename":
        sid = conversation_store.rename(root, _which(root, arguments), arguments.get("title") or "")
        return f"Renamed it to {arguments.get('title')!r}." if sid else "Could not rename: no such conversation, or no title given."
    if action == "copy":
        keep = arguments.get("keep_turns")
        sid = conversation_store.fork(root, _which(root, arguments), int(keep) if keep not in (None, "") else None)
        return "Made a copy and opened it; the original is unchanged." if sid else "No such conversation to copy."
    if action == "export":
        title, text = conversation_store.export_markdown(root, _which(root, arguments))
        if not text:
            return "No such conversation to export."
        try:
            target = files.resolve(arguments.get("path") or f"~/Documents/{title[:40].strip() or 'conversation'}.md")
        except files.PathProblem as exc:
            return str(exc)
        if target.exists():
            return f"{target} already exists; give another path."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return f"Saved {title!r} to {target}."
    if action == "delete":
        allowed, reason = _consent(ChronoaConfig())
        if not allowed:
            return f"Refusing to delete a conversation: {reason}."
        ref = (arguments.get("which") or "").strip()
        if not ref:
            return "Say which conversation to delete."
        sid = conversation_store.delete(root, ref)
        return "Deleted that conversation permanently." if sid else f"No single conversation matches {ref!r}."
    return f"action must be one of {', '.join(_ACTIONS)}."


def _post_condition(arguments: dict) -> "tuple[bool, str] | None":
    """Is the conversation this call deleted actually gone?

    Only the `delete` action is checked. Everything else this skill
    does is a read or a non-destructive write, and `None` - the
    contract's "nothing this call changed can be checked" - is the
    honest verdict for those, never `True`.

    The check re-resolves `which` through the *same* resolver `delete`
    used, rather than a second one written here: two resolvers can
    disagree about an ambiguous or partial title, and a check that
    disagrees with the action it verifies is worse than no check.

    A delete with no `which` is refused by the skill itself before
    anything is deleted, so there is nothing to verify - and the
    active conversation cannot be re-resolved by name, because
    deleting it moved the active pointer.
    """
    action = (arguments.get("action") or "").strip().lower()
    if action != "delete":
        return None
    ref = (arguments.get("which") or "").strip()
    if not ref:
        return None
    try:
        data = conversation_store.index(conversation_store.session_dir())
        still = conversation_store._resolve(data, ref)  # noqa: SLF001 - the same resolver delete used
    except Exception as exc:  # noqa: BLE001 - a store that raises is not a deletion
        return False, f"the conversation store could not be read: {exc.__class__.__name__}"
    if still:
        return False, f"a conversation still matches {ref!r}"
    return True, f"no conversation matches {ref!r} any more"


POST_CONDITION = _post_condition


SKILLS = [Skill(name="conversations", schema=SCHEMA, run=_run)]
