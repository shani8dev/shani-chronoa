"""Storage for `Percept` objects, split by lifetime.

Two tiers, because a screen grab and a remembered preference have genuinely
different lifetimes and conflating them is how ambient perception becomes a
liability:

- **Transient** percepts (those with a `ttl_seconds`) are held in a bounded
  in-memory deque only. They are never written to disk: a percept of what
  the screen looked like three sessions ago is not context, it is a
  surveillance record, and keeping one has no upside.
- **Durable** percepts (`ttl_seconds is None` - the memory sense) are
  appended to a JSON-lines file so they survive a restart. They were
  unbounded, which made the tier the one part of this store that could grow
  forever; they are now capped by *count* with least-recently-recalled
  eviction, because a durable percept has no age that means anything on its
  own. A fact the user states once and never asks about again is not
  something an assistant should keep for them.

Expiry is applied on read, never on a timer, so a store that is not being
polled still returns correct results and there is no background thread to
leak. `active()` is the only read path the context builder uses.

Three different reasons a durable fact stops being *available* here, kept
deliberately apart because merging any two of them is a privacy or honesty
regression:

- **Expiry** (`ttl_seconds`, `valid_until`) is about relevance. The fact
  stays on disk and comes back if its window is reopened.
- **Consent** (`ContextBuilder._permitted`) is about permission. The fact
  stays in the store and is withheld from the prompt.
- **Capacity** (this module's cap) is about space. The fact leaves the file,
  and that is a real deletion, so it is logged at `warning` rather than done
  quietly.

State lives under the per-user XDG data location
(`~/.local/share/shani-chronoa/`), matching the sandbox executor's own
state directory and `tool_tracking.py`'s log directory. Chronoa runs as a
normal desktop user and never as root, so nothing here may assume a
writable system path - an earlier `/var/log/shani-chronoa` default raised
`PermissionError` in real use precisely because it did.
"""

import dataclasses
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

from shani_chronoa import files
from shani_chronoa.senses import CONFIDENCE_UNSTATED, Percept

logger = logging.getLogger(__name__)

PERCEPT_DIR = Path(
    files.data_home() / "shani-chronoa" / "percepts"
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

# The durable tier is bounded by count, not by age, because a durable fact has
# no TTL: `ttl_seconds is None` is what routes it to disk in the first place.
# A thousand is chosen to be well past what a person actually accumulates -
# the failure this guards against is a loop re-storing the same fact, and that
# shows up as a store at its cap full of near-duplicates, which is a loud
# enough signal. A smaller cap would start evicting real facts for a user who
# legitimately remembers a lot; a larger one would not bound anything.
_DURABLE_CAPACITY = 1000

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
        "valid_until": percept.valid_until,
        "confidence": percept.confidence,
        "last_accessed_at": percept.last_accessed_at,
    }


def _optional_number(raw: object) -> Optional[float]:
    """A float out of a decoded record, or None if it is not a number.

    `bool` is excluded explicitly: it is an `int` subclass, so `True` would
    otherwise become `valid_until: 1.0` - 1970, and therefore expired by any
    `is_expired()` that consults it.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _from_dict(raw: object) -> Optional[Percept]:
    """Rebuild a Percept from a decoded record, or None if it is unusable.

    A corrupt or hand-edited line is skipped rather than raised: one bad
    record must not make every previously-stored memory unreadable.

    The three newer fields are each degraded rather than trusted, because this
    file is under the user's control and survives across versions:

    - `valid_until` unparseable becomes None (permanent), never a guess. The
      two plausible guesses are both wrong in the dangerous direction: treating
      it as permanent quotes a stale fact as current, and treating it as "now"
      discards a fact the user never asked to lose.
    - `confidence` is clamped into [0, 1] rather than rejected or trusted, so
      a hand-edited 4.2 still orders sensibly instead of dominating every
      tie-break, and a record written before the field existed ranks as
      unstated.
    - `last_accessed_at` unparseable becomes None, which reads as "never
      recalled" and therefore makes the fact a *first* eviction candidate.
      That is the safe direction for a bound: the alternative is a corrupt
      timestamp pinning a fact's LRU key to the top of the heap forever.
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

    confidence = _optional_number(raw.get("confidence"))
    if confidence is None:
        confidence = CONFIDENCE_UNSTATED
    confidence = min(1.0, max(0.0, confidence))

    metadata = raw.get("metadata")
    return Percept(
        sense=sense,
        kind=kind,
        content=content,
        created_at=created_at,
        ttl_seconds=_optional_number(raw.get("ttl_seconds")),
        source=raw.get("source") or "",
        sensitivity=raw.get("sensitivity") or "public",
        metadata=metadata if isinstance(metadata, dict) else None,
        valid_until=_optional_number(raw.get("valid_until")),
        confidence=confidence,
        last_accessed_at=_optional_number(raw.get("last_accessed_at")),
    )


class PerceptStore:
    """Holds transient percepts in memory and durable ones on disk."""

    def __init__(
        self,
        durable_path: Optional[Path] = None,
        live_path: Optional[Path] = None,
        transient_capacity: int = _TRANSIENT_CAPACITY,
        durable_capacity: Optional[int] = None,
    ) -> None:
        self._durable_path = Path(durable_path) if durable_path else DURABLE_FILE
        # Per instance, not read from the module at write time: a global lookup
        # lets a store with a custom durable_path publish into a shared location.
        # Being an attribute is not the same as being *derived* from the durable
        # path - an earlier fix did the first and left this fallback as the
        # module global, so `PerceptStore(durable_path=tmp)` still published on
        # every `add()` into the real `~/.local/share/shani-chronoa/percepts/
        # live.json` (observed: a test run rewrote that file with a fabricated
        # vision snapshot). A caller who names a scratch file asked to be told
        # about that file, so both halves of the store follow it.
        if live_path:
            self._live_path = Path(live_path)
        elif durable_path:
            self._live_path = self._durable_path.with_name(LIVE_FILE.name)
        else:
            self._live_path = LIVE_FILE
        self._transient: "deque[Percept]" = deque(maxlen=transient_capacity)
        self._durable: list[Percept] = []
        self._durable_capacity = (
            _DURABLE_CAPACITY if durable_capacity is None else max(1, int(durable_capacity))
        )
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
                files.ensure_private_dir(self._live_path.parent)
                unique = f"{os.getpid()}.{threading.get_ident()}"
                tmp = self._live_path.with_name(f"{self._live_path.name}.{unique}.tmp")
                tmp.write_text(json.dumps(snapshot), encoding="utf-8")
                # On the temp only: `os.replace` carries the temp's mode across,
                # so restricting it here is what makes the destination private.
                # Restricting the destination before the replace would fail on a
                # first publish, where it does not exist yet.
                files.restrict_file(tmp)
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
            if len(self._durable) > self._durable_capacity:
                # The rewrite serialises `self._durable`, which already
                # contains `percept`, so the append below would write this
                # record a second time. The durable file is append-only
                # otherwise, which makes a duplicate permanent: the same
                # fact then occupies two slots forever and is recalled twice.
                # Caught by counting lines in the file, not by looking at
                # `durable()`, which cannot see the difference.
                self._evict_to_capacity()
                return
            try:
                files.ensure_private_dir(self._durable_path.parent)
                existed = self._durable_path.exists()
                with self._durable_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(_as_dict(percept)) + "\n")
                if not existed:
                    files.restrict_file(self._durable_path)
            except OSError as e:
                logger.error("Failed to persist durable percept to %s: %s", self._durable_path, e)

    def mark_accessed(self, percepts: Iterable[Percept], now: Optional[float] = None) -> int:
        """Record that these durable facts were actually used; return how many.

        Called by `memory.recall_report()` on the facts it is about to return,
        and by nothing else. That restriction is the design: `active()` hands
        back the *whole* durable tier on every single turn, so stamping there
        would set every fact's key to the same instant and reduce the eviction
        order to plain insertion order - a bound with no policy in it. The one
        read that says something true about which facts matter is the read the
        user asked a question of.

        In memory only. The timestamps reach disk on the next full-file
        rewrite, which is the one place the file is ever rewritten at all -
        see `_rewrite_durable`. Writing on every read would mean rewriting the
        whole file per lookup, and the fact that a process which never forgets
        anything and never fills the cap has nothing to evict means the
        unflushed stamps cost nothing.
        """
        current = time.time() if now is None else now
        stamped = 0
        with _LOCK:
            for percept in percepts:
                # Identity, not equality: two durable facts with identical
                # fields are still two records, and `list.index` would keep
                # restamping the first one forever.
                for position, held in enumerate(self._durable):
                    if held is percept:
                        if held.last_accessed_at != current:
                            self._durable[position] = dataclasses.replace(
                                held, last_accessed_at=current
                            )
                            stamped += 1
                        break
        return stamped

    def _rewrite_durable(self) -> None:
        """Write the whole durable tier from memory. The only full-file write.

        `forget()` and capacity eviction both need the file to match
        `self._durable` exactly, so they share this rather than each carrying a
        copy of the same open/truncate/loop. It is also the only place the
        in-memory `last_accessed_at` stamps reach the disk, which is the
        piggyback that keeps them from needing a write path of their own.
        """
        try:
            files.ensure_private_dir(self._durable_path.parent)
            with self._durable_path.open("w", encoding="utf-8") as handle:
                for percept in self._durable:
                    handle.write(json.dumps(_as_dict(percept)) + "\n")
            files.restrict_file(self._durable_path)
        except OSError as e:
            logger.error("Failed to rewrite durable percepts to %s: %s", self._durable_path, e)

    def _evict_to_capacity(self) -> int:
        """Drop least-recently-recalled durable facts until the tier fits.

        A real deletion, and logged as one. An unbounded durable tier is a
        file that only ever grows, and "the assistant kept everything you ever
        said, forever, and you cannot see which parts it stopped using" is the
        failure this prevents - so the eviction is named rather than silent.

        Sorting is stable, which makes the tie-break insertion order: facts
        nobody has ever recalled all carry `created_at` as their access key, so
        the oldest of those goes first. Nothing here consults consent or
        expiry. A withheld fact still occupies its slot (withholding is about
        permission and costs the user nothing) and an expired fact is still
        stored (its window may be reopened); only space evicts.
        """
        overflow = len(self._durable) - self._durable_capacity
        if overflow <= 0:
            return 0
        victims = sorted(self._durable, key=Percept.access_key)[:overflow]
        doomed = {id(victim) for victim in victims}
        self._durable = [p for p in self._durable if id(p) not in doomed]
        logger.warning(
            "Durable percept store is at its cap of %d; evicted %d "
            "least-recently-recalled fact(s) from %s, oldest first among "
            "those never recalled",
            self._durable_capacity,
            len(victims),
            self._durable_path,
        )
        self._rewrite_durable()
        return len(victims)

    def active(self, now: Optional[float] = None) -> list[Percept]:
        """Every percept still within its lifetime, freshest last.

        Expired entries are dropped from the transient window as they are
        found, so a store left idle for a long time does not accumulate
        garbage indefinitely. A durable percept past its `valid_until` is
        filtered here for the same reason a transient one past its TTL is:
        this is the read path the context builder uses, so this is where
        "stale" has to become "absent" for anything to change.
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

        This is also when any `last_accessed_at` stamps accumulated since the
        last rewrite are persisted, for the reasons in `_rewrite_durable`.
        A predicate that matches nothing returns early without rewriting, so
        those stamps stay in memory until something actually needs the file
        changed; that is deliberate, because rewriting on a no-op forget would
        turn "ask what do you remember about nothing" into a full-file write.
        """
        with _LOCK:
            self._ensure_loaded()
            kept = [p for p in self._durable if not predicate(p)]
            removed = len(self._durable) - len(kept)
            if not removed:
                return 0
            self._durable = kept
            self._rewrite_durable()
            return removed
