"""Storage for `Percept` objects, split by lifetime.

Two tiers, because a screen grab and a remembered preference have genuinely
different lifetimes and conflating them is how ambient perception becomes a
liability:

- **Transient** percepts (those with a `ttl_seconds`) are held in a bounded
  in-memory deque only. They are never written to disk: a percept of what
  the screen looked like three sessions ago is not context, it is a
  surveillance record, and keeping one has no upside.
- **Durable** percepts (`ttl_seconds is None` - the memory sense) are
  appended to a JSON-lines file so they survive a restart.

Expiry is applied on read, never on a timer, so a store that is not being
polled still returns correct results and there is no background thread to
leak. `active()` is the only read path the context builder uses.

State lives under the per-user XDG data location
(`~/.local/share/shani-chronoa/`), matching the sandbox executor's own
state directory and `tool_tracking.py`'s log directory. Chronoa runs as a
normal desktop user and never as root, so nothing here may assume a
writable system path - an earlier `/var/log/shani-chronoa` default raised
`PermissionError` in real use precisely because it did.
"""

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

from shani_chronoa.senses import Percept

logger = logging.getLogger(__name__)

PERCEPT_DIR = Path(
    os.path.expanduser("~/.local/share/shani-chronoa/percepts")
)
DURABLE_FILE = PERCEPT_DIR / "memory.jsonl"

#: What the app is holding *right now*, published so a separate process can
#: see it. The durable file only ever holds the memory sense's facts, so on its
#: own it understates what is in context by every polled percept - which is most
#: of them. A skill runs in a subprocess and cannot see the app's memory, so
#: without this it would answer "what are you perceiving?" with the smaller and
#: less interesting half, and say so confidently.
LIVE_FILE = PERCEPT_DIR / "live.json"

# Bounded so a long-running ambient session cannot grow without limit. A
# transient percept is only useful while fresh, so a small window is not a
# real loss - anything older should have been persisted as a durable
# percept by the memory sense instead.
_TRANSIENT_CAPACITY = 256

# Guards both tiers. The ambient scheduler writes from its own thread while
# the turn path reads on the GTK main thread.
_LOCK = threading.RLock()


def _as_dict(percept: Percept) -> dict:
    return {
        "sense": percept.sense,
        "kind": percept.kind,
        "content": percept.content,
        "created_at": percept.created_at,
        "ttl_seconds": percept.ttl_seconds,
        "source": percept.source,
        "sensitivity": percept.sensitivity,
        "metadata": percept.metadata or {},
    }


def _from_dict(raw: object) -> Optional[Percept]:
    """Rebuild a Percept from a decoded record, or None if it is unusable.

    A corrupt or hand-edited line is skipped rather than raised: one bad
    record must not make every previously-stored memory unreadable.
    """
    if not isinstance(raw, dict):
        return None
    try:
        sense = raw["sense"]
        kind = raw["kind"]
        content = raw["content"]
        created_at = float(raw["created_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(sense, str) or not isinstance(kind, str):
        return None
    if not isinstance(content, str):
        return None

    ttl = raw.get("ttl_seconds")
    if ttl is not None:
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
            ttl = None
        else:
            ttl = float(ttl)

    metadata = raw.get("metadata")
    return Percept(
        sense=sense,
        kind=kind,
        content=content,
        created_at=created_at,
        ttl_seconds=ttl,
        source=raw.get("source") or "",
        sensitivity=raw.get("sensitivity") or "public",
        metadata=metadata if isinstance(metadata, dict) else None,
    )


class PerceptStore:
    """Holds transient percepts in memory and durable ones on disk."""

    def __init__(
        self,
        durable_path: Optional[Path] = None,
        live_path: Optional[Path] = None,
        transient_capacity: int = _TRANSIENT_CAPACITY,
    ) -> None:
        self._durable_path = Path(durable_path) if durable_path else DURABLE_FILE
        # Per instance, not read from the module at write time: a global lookup
        # lets a store with a custom durable_path publish into a shared location.
        self._live_path = Path(live_path) if live_path else LIVE_FILE
        self._transient: "deque[Percept]" = deque(maxlen=transient_capacity)
        self._durable: list[Percept] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Read the durable file once, lazily.

        A missing file is the normal first-run case, not an error.
        """
        if self._loaded:
            return
        self._loaded = True
        try:
            if not self._durable_path.is_file():
                return
            with self._durable_path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decoded = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Skipping malformed durable percept record in %s",
                            self._durable_path,
                        )
                        continue
                    percept = _from_dict(decoded)
                    if percept is not None:
                        self._durable.append(percept)
        except OSError as e:
            # A store that cannot read its own file degrades to "no durable
            # percepts" rather than breaking startup - the same reasoning
            # as the skills loader skipping a broken user module.
            logger.error("Failed to read durable percepts from %s: %s", self._durable_path, e)

    def add(self, percept: Percept) -> None:
        """Record a percept in the tier its lifetime dictates."""
        if percept.ttl_seconds is None:
            self._append_durable(percept)
        else:
            with _LOCK:
                self._transient.append(percept)
        self._publish_live()

    def _publish_live(self) -> None:
        """Write what is currently held, so another process can read it.

        Best effort by design. A failure here must not cost a percept: the
        store is the thing that matters, and this exists only so the answer to
        "what is being perceived" is available outside this process. Renamed
        into place rather than written in place, because a reader - a skill
        running concurrently with a poll - must never see half a file and
        report it as the truth.

        The whole write happens under the lock, and the temp name is unique per
        process *and* thread. Holding the lock only while building the snapshot
        was not enough: `os.getpid()` is the same for every thread in a
        process, so two threads publishing at once wrote the same temp file
        concurrently and the loser's bytes were renamed into place. That
        produced a `live.json` that was not valid JSON, and `list_percepts`
        reads that as "no running app is publishing" - a corruption that reports
        itself as an absence. Measured at 1 in 60 concurrent-publish runs before
        this fix; 0 in 400 after.
        """
        try:
            with _LOCK:
                snapshot = {
                    "written_at": time.time(),
                    "pid": os.getpid(),
                    "transient": [_as_dict(p) for p in self._transient],
                    "durable_count": len(self._durable),
                }
                self._live_path.parent.mkdir(parents=True, exist_ok=True)
                unique = f"{os.getpid()}.{threading.get_ident()}"
                tmp = self._live_path.with_name(f"{self._live_path.name}.{unique}.tmp")
                tmp.write_text(json.dumps(snapshot), encoding="utf-8")
                os.replace(tmp, self._live_path)
        except OSError as e:
            logger.debug("Could not publish the live percept view to %s: %s",
                         self._live_path, e)

    def extend(self, percepts: Iterable[Percept]) -> None:
        for percept in percepts:
            self.add(percept)

    def _append_durable(self, percept: Percept) -> None:
        with _LOCK:
            self._ensure_loaded()
            self._durable.append(percept)
            try:
                self._durable_path.parent.mkdir(parents=True, exist_ok=True)
                with self._durable_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(_as_dict(percept)) + "\n")
            except OSError as e:
                logger.error("Failed to persist durable percept to %s: %s", self._durable_path, e)

    def active(self, now: Optional[float] = None) -> list[Percept]:
        """Every percept still within its lifetime, freshest last.

        Expired entries are dropped from the transient window as they are
        found, so a store left idle for a long time does not accumulate
        garbage indefinitely.
        """
        current = time.time() if now is None else now
        with _LOCK:
            self._ensure_loaded()
            live = [p for p in self._durable if not p.is_expired(current)]
            kept: deque[Percept] = deque()
            for percept in self._transient:
                if percept.is_expired(current):
                    continue
                kept.append(percept)
            self._transient = deque(kept, maxlen=self._transient.maxlen)
            return live + list(self._transient)

    def durable(self) -> list[Percept]:
        """The persistent tier, oldest first."""
        with _LOCK:
            self._ensure_loaded()
            return list(self._durable)

    def clear_transient(self) -> None:
        """Drop every transient percept. Durable memories are untouched."""
        with _LOCK:
            self._transient.clear()
        self._publish_live()

    def forget(self, predicate) -> int:
        """Remove durable percepts matching `predicate`; return how many.

        Rewrites the durable file, because deletion is the whole point -
        a user who asks the assistant to forget something needs it gone from
        disk, not merely hidden from a read.
        """
        with _LOCK:
            self._ensure_loaded()
            kept = [p for p in self._durable if not predicate(p)]
            removed = len(self._durable) - len(kept)
            if not removed:
                return 0
            self._durable = kept
            try:
                self._durable_path.parent.mkdir(parents=True, exist_ok=True)
                with self._durable_path.open("w", encoding="utf-8") as handle:
                    for percept in kept:
                        handle.write(json.dumps(_as_dict(percept)) + "\n")
            except OSError as e:
                logger.error("Failed to rewrite durable percepts to %s: %s", self._durable_path, e)
            return removed
