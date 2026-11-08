"""Things Chronoa noticed, in one place, instead of one popup each.

Reflexes notify; an armed rule asks; a meeting app offers. Each of those is a
separate `notify-send`, and a desktop that interrupts from three sources has
no budget and no memory: the person cannot see what Chronoa has been saying,
cannot dismiss a whole class of it, and the assistant cannot say why it
spoke. This is the queue the design notes asked for - one bounded place where
proactive notices accumulate, each with *why now*, and a dismiss that lasts.

**A queue, not a popup.** `record()` never displays anything and never
blocks. The notification still goes out as before (a reflex that stopped
notifying would be a regression, not a policy); the record is what makes the
saying checkable afterwards, and what the Notices surface reads.

**Deduplicated and bounded, because a queue can nag too.** A condition that
stays true (a battery that stays flat) produces one open notice, not one per
tick - the reflex cooldown already spaces the notifications, and the store
refuses a second open copy of the same kind+title. The whole store is capped;
the oldest open notices are dropped first, and the drop is counted rather
than silent.

**Records carry no payloads beyond the notice itself.** Title, body and the
reason are what the person saw; nothing more is kept about the machine that
produced them. Notices expire: an open notice older than `MAX_AGE_SECONDS`
stops being listed, because a "battery low" from last month is not news.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional

from shani_chronoa import files

logger = logging.getLogger(__name__)

#: How many notices are kept at all. Open ones go first when the cap bites;
#: dismissed ones are kept a little longer so the surface can say what it
#: already dismissed, then they are pruned.
MAX_NOTICES = 100

#: An open notice older than this is no longer listed. "Battery low" from
#: last month is not news, and a queue that shows stale urgency teaches the
#: person to ignore it.
MAX_AGE_SECONDS = 7 * 24 * 3600


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa" / "notices.json"


def _load() -> list:
    try:
        parsed = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        logger.warning("notices store unreadable", exc_info=True)
        return []
    return [e for e in parsed if isinstance(e, dict)] if isinstance(parsed, list) else []


def _save(entries: list) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        path.write_text(json.dumps(entries), encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        logger.debug("could not write notices", exc_info=True)


def _is_open(entry: dict, now: float) -> bool:
    if entry.get("dismissed"):
        return False
    at = entry.get("at") or 0
    return now - at <= MAX_AGE_SECONDS


def record(kind: str, title: str, body: str, why: str) -> Optional[str]:
    """Queue one notice. Returns its id, or None when it was not queued.

    A notice identical to one already open (same kind and title) is not
    queued twice: the person has already been told, and the surface already
    shows it.
    """
    now = time.time()
    entries = _load()
    for entry in entries:
        if (_is_open(entry, now) and entry.get("kind") == kind
                and entry.get("title") == title):
            return None
    notice_id = uuid.uuid4().hex[:12]
    entries.append({
        "id": notice_id, "kind": str(kind or "notice"),
        "title": str(title or "")[:200], "body": str(body or "")[:500],
        "why": str(why or "")[:200], "at": now, "dismissed": False,
    })
    if len(entries) > MAX_NOTICES:
        dismissed = [e for e in entries if e.get("dismissed")]
        overflow = len(entries) - MAX_NOTICES
        drop = {id(e) for e in dismissed[:overflow]}
        if len(drop) < overflow:
            drop.update(id(e) for e in entries[: overflow - len(drop)])
        entries = [e for e in entries if id(e) not in drop]
    _save(entries)
    return notice_id


def list_open() -> "List[dict]":
    """Open notices, newest first. Expired and dismissed are not listed."""
    now = time.time()
    return sorted((e for e in _load() if _is_open(e, now)),
                  key=lambda e: e.get("at") or 0, reverse=True)


def list_all() -> "List[dict]":
    """Every notice kept, newest first - the honest reading of the store."""
    return sorted(_load(), key=lambda e: e.get("at") or 0, reverse=True)


def set_outcome(notice_id: Optional[str], outcome: str) -> bool:
    """Write the answer to one notice (an approval's allow/deny/timeout)."""
    if not notice_id:
        return False
    entries = _load()
    for entry in entries:
        if entry.get("id") == notice_id:
            entry["outcome"] = str(outcome or "")[:40]
            _save(entries)
            return True
    return False


def dismiss(notice_id: str) -> bool:
    """Mark one notice dismissed. True if it was found and open."""
    entries = _load()
    for entry in entries:
        if entry.get("id") == notice_id and not entry.get("dismissed"):
            entry["dismissed"] = True
            _save(entries)
            return True
    return False
