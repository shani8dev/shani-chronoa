"""The conversation transcript, on disk, so a restart is not a total loss.

Until this existed the assistant's history was a list in one process. Closing
the window, or a crash, or a reboot discarded the whole conversation with no
record - which for something people talk to several times a day is the
difference between a tool and a toy.

Append-only JSONL, one message per line, written after every turn rather than
at the end. The end is never reached in practice: the interesting failure is the
crash, and a transcript saved on clean exit is empty precisely when it was most
worth having. mini-swe-agent puts its `save()` in a `finally` for the same
reason.

Deliberately **not** a privacy-mode key. `privacy-mode` is documented as "all
processing happens locally, no data is sent to external servers" - it governs
egress. Writing a local file is not egress, and tying the two would mean a
setting labelled "local-only" silently also deleted transcripts, which is a
different promise than the one the user agreed to.

What this does do about the content:

- Mode 0600, in the app's own data directory, alongside the percepts and logs
  that are already there. Nothing new leaves the machine.
- **Percepts are never written.** They are deliberately not part of
  `_history` - they are re-read from the store on every request so one that
  expires mid-turn stops appearing - so a snapshot of the conversation cannot
  capture a machine reading the user has since revoked consent for.
- A corrupt or half-written line is skipped rather than failing the load. A
  crash mid-append is the expected case, not an exceptional one.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterator, List, Optional

from shani_chronoa import files

logger = logging.getLogger(__name__)

SESSION_DIR = files.data_home() / "shani-chronoa" / "sessions"
#: One file, holding the most recent conversation. A single assistant on one
#: machine is one conversation; a per-id scheme would be scaffolding for a
#: multi-session feature that does not exist yet.
#:
#: Named here, but **never used as a default**. Every function in this module
#: takes the path explicitly, because a default would mean that any
#: `Assistant()` constructed without one - a test, a script, an embedder -
#: silently reads and writes the real user's conversation file. That was the
#: first version, and it is exactly the kind of default nobody reviews.
TRANSCRIPT = SESSION_DIR / "current.jsonl"


def session_dir() -> Path:
    """The conversations directory, resolved now - never a constant captured at import."""
    return files.data_home() / "shani-chronoa" / "sessions"

#: How much history is worth reading back. The in-memory cap is 40 messages;
#: this is deliberately larger, because the transcript is a record rather than a
#: working set - a month-old turn is not going into a prompt, but deleting it
#: would mean the user cannot see what they were told.
MAX_LOADED = 500

#: Roles worth keeping. Anything else is a shape this module does not
#: understand, and writing it back out would just reproduce the surprise.
_KNOWN_ROLES = frozenset({"system", "user", "assistant", "tool"})


def _read_lines(path: Path) -> Iterator[str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                yield line
    except OSError as exc:
        logger.debug("No readable transcript at %s: %s", path, exc)
        return


def load(path: Path) -> List[dict]:
    """The saved messages, oldest first, skipping anything unreadable.

    A truncated final line is the signature of a crash during an append, and it
    is expected rather than exceptional - so it is dropped, not raised. The
    alternative is refusing to start because the last write was interrupted.
    """
    target = Path(path)
    messages: List[dict] = []
    for line in _read_lines(target):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            # A partial write, or a line something else put there. Either way
            # the rest of the conversation is still good.
            logger.debug("Skipping an unreadable transcript line")
            continue
        if not isinstance(record, dict) or record.get("role") not in _KNOWN_ROLES:
            continue
        messages.append(record)
    if len(messages) > MAX_LOADED:
        messages = messages[-MAX_LOADED:]
    return messages


def append(message: dict, path: Path) -> bool:
    """Append one message. Returns whether it was written.

    Never raises: a transcript that cannot be written is a lost convenience, and
    losing it must not take the conversation with it.
    """
    if not isinstance(message, dict) or message.get("role") not in _KNOWN_ROLES:
        return False
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(message, ensure_ascii=False)
        # Opened per append rather than held open, so the mode is applied on
        # every write and a file created by an older build still gets tightened.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        note_activity(target, message)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Could not append to the transcript at %s: %s", target, exc)
        return False


def rewrite(messages: List[dict], path: Path) -> bool:
    """Replace a transcript with `messages`. Used when a reply is taken back.

    Append-only is right for a conversation being written, and wrong the moment
    the user says "no, answer that again": the old answer has to leave the file,
    not sit under the new one for the next process to restore.

    Written through a temp file in the same directory and renamed into place,
    so a crash mid-write leaves the previous transcript intact rather than a
    half-written one. Same 0600 as `append`, and the temp file never gets wider
    than the transcript it replaces.

    Never raises, for the same reason `append` does not.
    """
    target = Path(path)
    if any(not isinstance(m, dict) or m.get("role") not in _KNOWN_ROLES for m in messages):
        return False
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        body = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, body.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Could not rewrite the transcript at %s: %s", target, exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def clear(path: Path) -> bool:
    """Delete the transcript. Used by `Assistant.reset()` and by the user."""
    target = Path(path)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.debug("Could not clear the transcript at %s: %s", target, exc)
        return False


# --- Many conversations ----------------------------------------------------
#
# One transcript was right while there was one conversation. People keep
# several going ("the tax one", "the trip one") and come back to them, so the
# directory now holds one `<id>.jsonl` per conversation and an `index.json`
# naming them and saying which one is open. The JSONL format and `load` /
# `append` above are unchanged - a conversation file is exactly what
# `current.jsonl` was - so the Assistant only ever sees a path.
#
# The shape is the one assistd, codex and gemini-cli converged on: list, new,
# switch, rename, delete, fork (copy up to a point to try another way), search
# across all of them, and export to Markdown. Titles come from the first thing
# the user said, not from a model call, so naming a conversation costs nothing
# and cannot send its text anywhere.
#
# The index is written atomically (temp file + rename) under an flock, because
# the app and a skill subprocess can both touch it.

import fcntl
import re
import secrets
import threading
import time
from contextlib import contextmanager

INDEX = "index.json"
_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
TITLE_CHARS = 60


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def valid_id(session_id: str) -> bool:
    return bool(_ID_RE.match(session_id or ""))


_held = threading.local()


@contextmanager
def _locked(root: Path):
    """An exclusive flock on the directory, re-entrant within one thread.

    Re-entrant because `fork` appends while it holds the lock and `append`
    updates the index under it: a second flock on a fresh descriptor blocks
    on the first, in the same thread, for ever.
    """
    key = str(Path(root).resolve())
    held = getattr(_held, "roots", None)
    if held is None:
        held = _held.roots = set()
    if key in held:
        yield
        return
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held.add(key)
        yield
    finally:
        held.discard(key)
        os.close(fd)


def _read_index(root: Path) -> dict:
    try:
        data = json.loads((root / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    known = data.get("sessions")
    data["sessions"] = {k: v for k, v in (known or {}).items()
                        if valid_id(k) and isinstance(v, dict)} if isinstance(known, dict) else {}
    if not valid_id(str(data.get("active") or "")):
        data["active"] = ""
    return data


def _write_index(root: Path, data: dict) -> None:
    tmp = root / (INDEX + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, root / INDEX)


def _title_from(text: str) -> str:
    line = " ".join(str(text or "").split())
    # an attachment block is not what the conversation is about
    line = line.split("📎", 1)[0].strip()
    return (line[: TITLE_CHARS - 1] + "…") if len(line) > TITLE_CHARS else line


def _migrate(root: Path, data: dict) -> bool:
    """Fold the single-file `current.jsonl` of earlier builds into the index, once."""
    legacy = root / "current.jsonl"
    if not legacy.is_file():
        return False
    when = legacy.stat().st_mtime
    sid = time.strftime("%Y%m%d-%H%M%S", time.localtime(when)) + "-" + secrets.token_hex(2)
    os.replace(legacy, root / f"{sid}.jsonl")
    first = next((m.get("content") for m in load(root / f"{sid}.jsonl") if m.get("role") == "user"), "")
    data["sessions"][sid] = {"title": _title_from(first) or "Earlier conversation",
                             "created": when, "updated": when}
    data["active"] = data["active"] or sid
    return True


def index(root: Path) -> dict:
    """The index, after folding in a legacy transcript if there is one."""
    root = Path(root)
    with _locked(root):
        data = _read_index(root)
        if _migrate(root, data):
            _write_index(root, data)
        return data


def session_path(root: Path, session_id: str) -> Path:
    if not valid_id(session_id):
        raise ValueError(f"{session_id!r} is not a conversation id")
    return Path(root) / f"{session_id}.jsonl"


def active_path(root: Path) -> Path:
    """The open conversation's file, starting a new conversation if none is open."""
    root = Path(root)
    with _locked(root):
        data = _read_index(root)
        changed = _migrate(root, data)
        if not data["active"] or data["active"] not in data["sessions"]:
            sid = _new_id()
            data["sessions"][sid] = {"title": "", "created": time.time(), "updated": time.time()}
            data["active"] = sid
            changed = True
        if changed:
            _write_index(root, data)
        return root / f"{data['active']}.jsonl"


def new_session(root: Path) -> str:
    """Open a fresh conversation; an empty open one is reused rather than piling up blanks."""
    root = Path(root)
    with _locked(root):
        data = _read_index(root)
        _migrate(root, data)
        current = data["active"]
        if current in data["sessions"] and not (root / f"{current}.jsonl").exists():
            return current
        sid = _new_id()
        data["sessions"][sid] = {"title": "", "created": time.time(), "updated": time.time()}
        data["active"] = sid
        _write_index(root, data)
        return sid


def _resolve(data: dict, ref: str) -> str:
    """An id, or a title (exact, then unique substring), to an id; '' if none or ambiguous."""
    ref = (ref or "").strip()
    if ref in data["sessions"]:
        return ref
    low = ref.lower()
    if not low:
        return ""
    exact = [k for k, v in data["sessions"].items() if (v.get("title") or "").lower() == low]
    if len(exact) == 1:
        return exact[0]
    partial = [k for k, v in data["sessions"].items() if low in (v.get("title") or "").lower()]
    return partial[0] if len(partial) == 1 else ""


def switch(root: Path, ref: str) -> str:
    """Make `ref` (id or title) the open conversation; returns its id, or '' if not found."""
    root = Path(root)
    with _locked(root):
        data = _read_index(root)
        sid = _resolve(data, ref)
        if sid:
            data["active"] = sid
            _write_index(root, data)
        return sid


def rename(root: Path, ref: str, title: str) -> str:
    root = Path(root)
    title = _title_from(title)
    with _locked(root):
        data = _read_index(root)
        sid = _resolve(data, ref)
        if sid and title:
            data["sessions"][sid]["title"] = title
            data["sessions"][sid]["named"] = True
            _write_index(root, data)
            return sid
        return ""


def set_generated_title(root: Path, sid: str, title: str) -> bool:
    """A model-written title, once per conversation and never over one the person chose (`rename`)."""
    root = Path(root)
    title = _title_from(" ".join(str(title or "").split()).strip(" .\"'"))
    if not title or not valid_id(sid):
        return False
    with _locked(root):
        data = _read_index(root)
        entry = data["sessions"].get(sid)
        if not entry or entry.get("named") or entry.get("titled"):
            return False
        entry["title"], entry["titled"] = title, True
        _write_index(root, data)
    return True


def delete(root: Path, ref: str) -> str:
    """Delete one conversation's file and entry; the open one moves to the newest left (or none)."""
    root = Path(root)
    with _locked(root):
        data = _read_index(root)
        sid = _resolve(data, ref)
        if not sid:
            return ""
        try:
            (root / f"{sid}.jsonl").unlink()
        except FileNotFoundError:
            pass
        _purge_from_search(root, sid)  # deleted means gone from the search index too, now
        del data["sessions"][sid]
        if data["active"] == sid:
            rest = sorted(data["sessions"], key=lambda k: data["sessions"][k].get("updated", 0))
            data["active"] = rest[-1] if rest else ""
        _write_index(root, data)
        return sid


def fork(root: Path, ref: str, keep_user_turns: Optional[int] = None) -> str:
    """Copy a conversation (optionally only up to its Nth user message) into a new open one."""
    root = Path(root)
    with _locked(root):
        data = _read_index(root)
        sid = _resolve(data, ref)
        if not sid:
            return ""
        messages = load(root / f"{sid}.jsonl")
        if keep_user_turns is not None:
            kept, seen = [], 0
            for m in messages:
                if m.get("role") == "user":
                    seen += 1
                    if seen > keep_user_turns:
                        break
                kept.append(m)
            messages = kept
        new = _new_id()
        for m in messages:
            append(m, root / f"{new}.jsonl")
        title = data["sessions"][sid].get("title") or "Conversation"
        data["sessions"][new] = {"title": _title_from("Copy of " + title), "created": time.time(),
                                 "updated": time.time(), "forked_from": sid}
        data["active"] = new
        _write_index(root, data)
        return new


def note_activity(path: Path, message: dict) -> None:
    """Keep the index's `updated` and auto-title current; a no-op outside an indexed directory."""
    path = Path(path)
    root = path.parent
    if not (root / INDEX).exists() or not valid_id(path.stem):
        return
    try:
        with _locked(root):
            data = _read_index(root)
            entry = data["sessions"].setdefault(path.stem, {"title": "", "created": time.time()})
            entry["updated"] = time.time()
            if not entry.get("title") and message.get("role") == "user":
                entry["title"] = _title_from(message.get("content") or "")
            _write_index(root, data)
    except OSError as exc:
        logger.debug("Could not update the conversation index: %s", exc)


def list_sessions(root: Path) -> List[dict]:
    """Every conversation, most recently used first, with its message count."""
    root = Path(root)
    data = index(root)
    out = []
    for sid, meta in data["sessions"].items():
        messages = load(root / f"{sid}.jsonl")
        out.append({"id": sid, "title": meta.get("title") or "New conversation",
                    "updated": meta.get("updated", 0), "created": meta.get("created", 0),
                    "messages": sum(1 for m in messages if m.get("role") in ("user", "assistant")),
                    "active": sid == data["active"], "forked_from": meta.get("forked_from", "")})
    return sorted(out, key=lambda s: s["updated"], reverse=True)


def _text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):  # multimodal parts
        content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


SEARCH_DB = "search.sqlite"


def _purge_from_search(root: Path, sid: str) -> None:
    import sqlite3
    path = Path(root) / SEARCH_DB
    if not path.exists():
        return
    try:
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM msgs WHERE sid = ?", (sid,))
            db.execute("DELETE FROM seen WHERE sid = ?", (sid,))
            try:
                db.execute("DELETE FROM vecs WHERE sid = ?", (sid,))
            except sqlite3.OperationalError:  # an index from before vectors existed
                pass
    except sqlite3.Error as exc:
        logger.warning("Could not remove a deleted conversation from the search index: %s", exc)


def _search_db(root: Path):
    """The full-text index beside the conversations, brought up to date with the JSONL files.

    The JSONL files stay the record; this is a cache that can be deleted at
    any time (assistd keeps the same FTS5 table over its conversation store).
    Each file is indexed from the byte offset reached last time, so a search
    reads only what was said since; a deleted conversation's rows go with it.
    Returns None when this SQLite has no FTS5.
    """
    import sqlite3
    path = Path(root) / SEARCH_DB
    try:
        db = sqlite3.connect(path)
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS msgs USING fts5(sid UNINDEXED, role UNINDEXED, text, "
                   "tokenize='unicode61 remove_diacritics 2')")
        db.execute("CREATE TABLE IF NOT EXISTS seen (sid TEXT PRIMARY KEY, offset INTEGER)")
        # one embedding per msgs row, filled in as the embedding server allows (local_embed)
        db.execute("CREATE TABLE IF NOT EXISTS vecs (id INTEGER PRIMARY KEY, sid TEXT, v BLOB)")
    except sqlite3.Error as exc:
        logger.debug("No full-text index (%s); searching by scan", exc)
        return None
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    live = {p.stem for p in Path(root).glob("*.jsonl") if valid_id(p.stem)}
    for (sid,) in db.execute("SELECT sid FROM seen").fetchall():
        if sid not in live:
            db.execute("DELETE FROM msgs WHERE sid = ?", (sid,))
            db.execute("DELETE FROM vecs WHERE sid = ?", (sid,))
            db.execute("DELETE FROM seen WHERE sid = ?", (sid,))
    for sid in live:
        file = Path(root) / f"{sid}.jsonl"
        row = db.execute("SELECT offset FROM seen WHERE sid = ?", (sid,)).fetchone()
        offset = row[0] if row else 0
        size = file.stat().st_size
        if size < offset:  # rewritten (cleared): index it again
            db.execute("DELETE FROM msgs WHERE sid = ?", (sid,))
            db.execute("DELETE FROM vecs WHERE sid = ?", (sid,))
            offset = 0
        if size == offset:
            continue
        with file.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
        complete = chunk[: chunk.rfind(b"\n") + 1]
        for line in complete.decode("utf-8", errors="replace").splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and _text_of(m).strip():
                db.execute("INSERT INTO msgs (sid, role, text) VALUES (?, ?, ?)", (sid, m["role"], _text_of(m)))
        db.execute("INSERT OR REPLACE INTO seen (sid, offset) VALUES (?, ?)", (sid, offset + len(complete)))
    db.commit()
    return db


#: messages embedded per search at most, so the first search after setup is not a long wait
EMBED_BATCH, EMBED_PER_SEARCH = 32, 256
#: below this cosine similarity a message is not "about" the query (nomic-embed-text v1.5)
MEANING_FLOOR = 0.55


def _fill_vectors(db, embed) -> None:
    """Embed messages that have no vector yet, oldest first, up to EMBED_PER_SEARCH of them."""
    from shani_chronoa import local_embed
    rows = db.execute("SELECT m.rowid, m.sid, m.text FROM msgs m LEFT JOIN vecs v ON v.id = m.rowid "
                      "WHERE v.id IS NULL ORDER BY m.rowid LIMIT ?", (EMBED_PER_SEARCH,)).fetchall()
    for at in range(0, len(rows), EMBED_BATCH):
        batch = rows[at:at + EMBED_BATCH]
        vectors = embed([text for _id, _sid, text in batch], local_embed.DOCUMENT)
        if vectors is None:
            break
        db.executemany("INSERT OR REPLACE INTO vecs (id, sid, v) VALUES (?, ?, ?)",
                       [(rid, sid, local_embed.to_blob(vec)) for (rid, sid, _t), vec in zip(batch, vectors)])
    db.commit()


def _by_meaning(db, query: str, exclude: str, limit: int, embed) -> "list[tuple[int, float]]":
    """(msgs rowid, similarity) of the messages closest in meaning to `query`, best first."""
    from shani_chronoa import local_embed
    _fill_vectors(db, embed)
    asked = embed([query], local_embed.QUERY)
    if not asked:
        return []
    q = asked[0]
    scored = []
    for rid, sid, blob in db.execute("SELECT id, sid, v FROM vecs WHERE sid != ?", (exclude,)):
        score = local_embed.similarity(q, local_embed.from_blob(blob))
        if score >= MEANING_FLOOR:
            scored.append((rid, score))
    scored.sort(key=lambda r: -r[1])
    return scored[:limit]


def search(root: Path, query: str, limit: int = 10, exclude: str = "", embed=None) -> List[dict]:
    """Messages (user and assistant) about `query`, best first.

    Words first (FTS5 bm25 over messages containing every word), and - when
    Chronoa's embedding model is set up - meaning too: the two rankings are
    merged by reciprocal rank, so a message that says "the wifi box keeps
    dropping" is found for "router problems". `embed` is `local_embed.embed`
    unless a test passes its own. Each hit says how it matched.
    """
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 1]
    if not words:
        return []
    titles = {s["id"]: s["title"] for s in list_sessions(root)}
    db = _search_db(root)
    if db is not None:
        import sqlite3
        if embed is None:
            from shani_chronoa import local_embed
            embed = local_embed.embed
        match = " ".join('"' + w.replace('"', "") + '"*' for w in words)
        try:
            by_words = db.execute(
                "SELECT rowid FROM msgs WHERE msgs MATCH ? AND sid != ? ORDER BY bm25(msgs) LIMIT ?",
                (match, exclude, limit * 2)).fetchall()
            by_meaning = _by_meaning(db, query, exclude, limit * 2, embed)
            fused: "dict[int, float]" = {}
            how: "dict[int, set]" = {}
            for rank, (rid,) in enumerate(by_words):
                fused[rid] = fused.get(rid, 0.0) + 1.0 / (60 + rank)
                how.setdefault(rid, set()).add("words")
            for rank, (rid, _score) in enumerate(by_meaning):
                fused[rid] = fused.get(rid, 0.0) + 1.0 / (60 + rank)
                how.setdefault(rid, set()).add("meaning")
            hits = []
            for rid in sorted(fused, key=lambda r: -fused[r])[:limit]:
                row = db.execute("SELECT sid, role, text FROM msgs WHERE rowid = ?", (rid,)).fetchone()
                if not row or row[0] not in titles:
                    continue
                sid, role, text = row
                if "words" in how[rid]:
                    snip = db.execute("SELECT snippet(msgs, 2, '', '', ' ... ', 24) FROM msgs "
                                      "WHERE msgs MATCH ? AND rowid = ?", (match, rid)).fetchone()
                    snippet = snip[0] if snip else text[:200]
                else:
                    snippet = text[:200]
                hits.append({"id": sid, "title": titles.get(sid, "Conversation"), "role": role,
                             "snippet": " ".join(snippet.split()),
                             "match": "words and meaning" if len(how[rid]) == 2 else next(iter(how[rid]))})
            return hits
        except sqlite3.Error as exc:
            logger.debug("Full-text search failed (%s); scanning instead", exc)
        finally:
            db.close()
    hits = []
    for sid, title in titles.items():
        if sid == exclude:
            continue
        for m in load(Path(root) / f"{sid}.jsonl"):
            if m.get("role") not in ("user", "assistant"):
                continue
            text = _text_of(m)
            low = text.lower()
            if all(w in low for w in words):
                at = low.find(words[0])
                snippet = " ".join(text[max(0, at - 80): at + 160].split())
                hits.append({"id": sid, "title": title, "role": m["role"], "snippet": snippet})
                if len(hits) >= limit:
                    return hits
    return hits


def export_markdown(root: Path, ref: str) -> "tuple[str, str]":
    """(title, markdown) of one conversation; ('', '') if not found. Tool traffic is left out."""
    data = index(root)
    sid = _resolve(data, ref)
    if not sid:
        return "", ""
    meta = data["sessions"][sid]
    title = meta.get("title") or "Conversation"
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.get("created", 0)))
    parts = [f"# {title}", "", f"_Chronoa conversation started {when}_", ""]
    for m in load(Path(root) / f"{sid}.jsonl"):
        text = _text_of(m).strip()
        if m.get("role") == "user" and text:
            parts += [f"**You:** {text}", ""]
        elif m.get("role") == "assistant" and text:
            parts += [f"**Chronoa:** {text}", ""]
    return title, "\n".join(parts).rstrip() + "\n"
