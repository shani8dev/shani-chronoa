"""Cross-device sync primitives: a file you carry, never a server you join.

There is no account, no relay, no pairing service and no new port here. An
export writes one JSON file to a path the user names inside their home; an
import reads such a file and merges it into the local stores. The only transport
is the person carrying that file to another machine, which is why the file is
written 0600 and the path is confined to the home directory.

Three stores travel, and only these three:

- **conversations** - each session as its messages, imported as a *new*
  session so an import cannot overwrite a conversation written here since;
- **settings** - a short allowlist of non-secret preferences. API keys, tokens
  and every secret live in the keyring and are never in this file;
- **memory** - the durable percepts (the facts Chronoa keeps), merged by
  content so importing twice does not double a fact.

Speaker enrolments and their audio clips do **not** travel. They are biometric
records bound to a person's consent on this machine, not portable preferences;
putting them in a file somebody carries would defeat the point of `speaker_id`.

Nothing happens without an explicit confirmation, and an import is a *preview*
unless `apply=true` is also passed. Preview counts what would change and writes
nothing. The confirmation carries those counts and the path, not the messages,
so the question a person answers is the shape of the change rather than a wall
of text. A refusal, a dismissed question or no notification service is a no.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from shani_chronoa import approvals, conversation_store, files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import store as percept_store
from shani_chronoa.skills import Skill

FORMAT = "shani-chronoa-sync"
VERSION = 1
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_CONVERSATIONS = 200
MAX_MESSAGES = 5000
MAX_FACTS = 2000

#: A deliberately short allowlist. These are preferences a person would
#: reasonably want on a second machine; adding a key here is a decision about
#: what may be carried, never a reason to carry everything in GSettings.
SETTINGS_KEYS = (
    "language",
    "speech-rate",
    "end-of-speech-pause",
    "piper-voice",
    "kokoro-voice",
    "home-place",
)
_KINDS = ("conversations", "settings", "memory")
_ACTIONS = ("export", "preview", "import")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "device_sync",
        "description": (
            "Move conversations, preferences and remembered facts to another Chronoa by carrying a file - "
            "no server, account or network. export: write a private JSON file to a path in your home; "
            "preview: say what a file would change without writing; import: merge it (needs apply=true). "
            "Every action asks you first. Speaker enrolments and voice clips never travel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "kinds": {
                    "type": "string",
                    "description": "comma-separated: conversations, settings, memory (default all)",
                },
                "path": {
                    "type": "string",
                    "description": "the .json file to write or read, inside your home",
                },
                "apply": {
                    "type": "boolean",
                    "description": "import only: actually write after the preview",
                },
            },
            "required": ["action", "path"],
        },
    },
}


def _requested_kinds(raw: str) -> set[str]:
    text = str(raw or "").strip().lower()
    if not text or text == "all":
        return set(_KINDS)
    picked = {part.strip() for part in text.split(",") if part.strip()}
    unknown = picked - set(_KINDS)
    if unknown:
        raise ValueError(f"unknown thing to sync: {', '.join(sorted(unknown))}")
    return picked


def _settings(config: ChronoaConfig | None = None) -> dict:
    cfg = config or ChronoaConfig()
    out = {}
    for key in SETTINGS_KEYS:
        value = cfg.get(key, "")
        if isinstance(value, (str, int, float, bool)) and value != "":
            out[key] = value
    return out


def _conversations(root: Path | None = None) -> list[dict]:
    root = root or conversation_store.session_dir()
    sessions = conversation_store.list_sessions(root)[:MAX_CONVERSATIONS]
    out = []
    for item in sessions:
        sid = str(item.get("id") or "")
        if not conversation_store.valid_id(sid):
            continue
        messages = conversation_store.load(conversation_store.session_path(root, sid))
        out.append(
            {"title": str(item.get("title") or ""), "messages": messages[:MAX_MESSAGES]}
        )
    return out[:MAX_CONVERSATIONS]


def _memory(store: percept_store.PerceptStore | None = None) -> list[dict]:
    store = store or percept_store.PerceptStore()
    return [percept_store._as_dict(p) for p in store.durable()[:MAX_FACTS]]


def collect(kinds: set[str], config=None, root=None, store=None) -> dict:
    """Build the portable bundle, reading only the three named stores."""
    bundle = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": time.time(),
        "settings": {},
        "conversations": [],
        "memory": [],
    }
    if "settings" in kinds:
        bundle["settings"] = _settings(config)
    if "conversations" in kinds:
        bundle["conversations"] = _conversations(root)
    if "memory" in kinds:
        bundle["memory"] = _memory(store)
    return bundle


def write_bundle(path: Path, bundle: dict) -> None:
    """One 0600 file, written atomically; the directory stays 0700."""
    files.ensure_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    try:
        body = json.dumps(bundle, ensure_ascii=False, indent=1, sort_keys=True)
        with os.fdopen(
            os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(body + "\n")
        files.restrict_file(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def read_bundle(path: Path) -> dict:
    """Validate before acting on anything from a file somebody brought."""
    if not path.is_file():
        raise ValueError(f"no sync file at {path}")
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError(f"{path.name} is too large to be a Chronoa sync file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read {path.name} as a sync file: {exc}") from exc
    if (
        not isinstance(raw, dict)
        or raw.get("format") != FORMAT
        or raw.get("version") != VERSION
    ):
        raise ValueError("that is not a Chronoa sync file of this version")
    for key in ("settings", "conversations", "memory"):
        if not isinstance(raw.get(key, []), (dict, list)):
            raise ValueError(f"the {key} part of that file is not readable")
    if not isinstance(raw.get("settings", {}), dict):
        raise ValueError("the settings part of that file is not readable")
    if (
        len(raw.get("conversations", [])) > MAX_CONVERSATIONS
        or len(raw.get("memory", [])) > MAX_FACTS
    ):
        raise ValueError(
            "that sync file holds more than this import will take in one go"
        )
    return raw


def preview(bundle: dict) -> dict:
    """Counts only. No writes, no config reads that change anything."""
    return {
        "conversations": len(bundle.get("conversations") or []),
        "settings": len(bundle.get("settings") or {}),
        "memory": len(bundle.get("memory") or []),
    }


def apply_bundle(bundle: dict, config=None, root=None, store=None) -> dict:
    """Merge into the local stores. New conversations; no overwrite, no duplicates."""
    cfg = config or ChronoaConfig()
    root = root or conversation_store.session_dir()
    store = store or percept_store.PerceptStore()
    counts = {"conversations": 0, "settings": 0, "memory": 0}

    for key, value in (bundle.get("settings") or {}).items():
        if key not in SETTINGS_KEYS or not isinstance(value, (str, int, float, bool)):
            continue
        if str(cfg.get(key, "")) != str(value):
            cfg.set(key, str(value).lower() if isinstance(value, bool) else str(value))
            counts["settings"] += 1

    for item in bundle.get("conversations") or []:
        if not isinstance(item, dict) or not isinstance(item.get("messages"), list):
            continue
        sid = conversation_store.new_session(root)
        if conversation_store.rewrite(
            item["messages"], conversation_store.session_path(root, sid)
        ):
            if item.get("title"):
                conversation_store.rename(root, sid, str(item["title"]))
            counts["conversations"] += 1

    known = {(p.sense, p.kind, p.content, p.created_at) for p in store.durable()}
    for raw in bundle.get("memory") or []:
        percept = percept_store._from_dict(raw)
        if percept is None or percept.ttl_seconds is not None:
            continue
        key = (percept.sense, percept.kind, percept.content, percept.created_at)
        if key in known:
            continue
        known.add(key)
        store.add(percept)
        counts["memory"] += 1
    return counts


def _summarise(counts: dict) -> str:
    return (
        f"{counts['conversations']} conversation(s), {counts['settings']} setting(s) "
        f"and {counts['memory']} remembered fact(s)"
    )


def _run(arguments: dict) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    try:
        kinds = _requested_kinds(str(args.get("kinds") or ""))
    except ValueError as exc:
        return str(exc)
    try:
        path = files.resolve_in_home(str(args.get("path") or ""))
    except files.PathProblem as exc:
        return str(exc)
    if path.suffix.lower() != ".json":
        return "Give a .json file to carry the bundle."

    if action == "export":
        bundle = collect(kinds)
        counts = preview(bundle)
        ok, why = approvals.confirm(
            "Export Chronoa data to a file",
            f"Write {_summarise(counts)} to {path.name} so you can carry it to another machine? "
            "Speaker enrolments, voice clips and secrets are not included. Allow once?",
        )
        if not ok:
            return f"Nothing was exported: {why}."
        try:
            write_bundle(path, bundle)
        except OSError as exc:
            return f"Could not write the sync file: {exc}"
        return (
            f"Exported {_summarise(counts)} to {path}. It stays on this machine until "
            "you carry it; nothing was sent over a network."
        )

    try:
        bundle = read_bundle(path)
    except ValueError as exc:
        return str(exc)
    counts = preview(bundle)
    if action == "preview" or not args.get("apply"):
        return (
            f"Preview: {_summarise(counts)} would be imported from {path.name}. "
            "Nothing was written; pass apply=true after you have decided."
        )
    ok, why = approvals.confirm(
        "Import Chronoa data from a file",
        f"Merge {_summarise(counts)} from {path.name} into this machine? "
        "Conversations are added as new ones; nothing here is overwritten. Allow once?",
    )
    if not ok:
        return f"Nothing was imported: {why}."
    done = apply_bundle(bundle)
    return (
        f"Imported {_summarise(done)} from {path.name}. Nothing was overwritten, "
        "and no voice enrolments or secrets were carried."
    )


SKILLS = [Skill(name="device_sync", schema=SCHEMA, run=_run)]
