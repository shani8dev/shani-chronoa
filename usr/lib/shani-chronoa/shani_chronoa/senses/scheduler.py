"""Ambient mode for the sensory layer: poll a sense on a timer, unprompted.

Reactive sensing - what this repo shipped first - is a sense that runs when the
model asks for it. Ambient is the *same* `Sense` contract run on a clock
instead: `poll()` here takes the identical arguments dict, gets back the
identical `str`-or-`Percept`, and the result lands in the identical
`PerceptStore` that `ContextBuilder` reads each turn. There is no second
perception architecture and no second percept type, which is the point of the
`Sense.is_ambient()` concept already living on the contract rather than being
invented here as a parallel flag.

The whole design is one sentence long: **ambient adds a clock, and a clock
does not get to bypass the consent gate.** Everything below follows from that.

Why the gate is checked before the work, not after
---------------------------------------------------
`poll()` consults `ChronoaConfig.sense_allowed(name)` and returns on the very
first line of real work, so a sense whose consent key is off is never
*invoked* - not "invoked and its result discarded". A post-hoc filter is
worthless here: the capture has already happened by the time a filter could
look at it, and OCR of a screenshot or a web fetch leaves the machine whether
or not anyone kept the percept. So the disabled-sense test in
`tests/test_sense_scheduler.py` asserts the sense's `run` callable was never
entered, not that its output was dropped.

The same rule covers the networked case without special-casing it: `web` is
in `config._NETWORKED_SENSES`, so `sense_allowed("web")` is false while
privacy mode is on and this scheduler never runs it. That is why `web.py`
declares `poll_interval=None`, and why nothing here has to know the
difference.

A consent key with no module
----------------------------
A name can have a consent key and no registered sense: a user drop-in can be
deleted while its gsetting stays, and a sense module can be withheld. That is
a legitimate state, and it is the reason `plan()` exists: it reports every name
it knows about - registered senses, consent names from
`config._SENSE_CONSENT_KEYS`, and any extra names a caller passes - and says
plainly which of them are not pollable and why. An unregistered name is
*information*, not an error: `poll()` returns a skipped result for it and logs
nothing at all, because a scheduler that logged once per interval for a sense
that does not exist would fill a log with a complaint about a state the
codebase calls normal.

Durability is a refusal, not a warning
-------------------------------------
`ttl_seconds is None` routes a percept to `PerceptStore`'s on-disk tier, and
only the memory sense may use it. An ambient sense declaring `None` would
write a fresh permanent record on *every* tick - a surveillance-shaped default
produced by arithmetic rather than by a decision. So the refusal is computed
once, at construction, from the registry: such a sense is excluded from the
poll set before it ever runs, and reported by `plan()`. The same rule is
re-checked on the *returned* percept, because a sense may hand back a Percept
whose lifetime differs from what it declared, and `store.add()` is the last
gate before disk.

Threading, and why the main thread is never blocked
--------------------------------------------------
One daemon thread, one `threading.Event` for stopping, and
`Event.wait(timeout)` for sleeping - never `time.sleep`, so `stop()` returns
immediately instead of after a full interval. The wait is computed as the
shortest time until any sense is next due, so senses with different
intervals share a thread and a wake-up.

The loop holds the GIL only while it is actually doing Python work, which is
the intent rather than an accident: a sense that shells out (`ocr` runs
tesseract, `web` does an HTTP request) releases the GIL for the whole of that
work, and `filesystem` already runs its bounded read on a worker thread
precisely because a kernel read is not interruptible from Python. A scheduler
that polled on the GTK main thread would stall the window for the length of
every capture; one that busy-waited between polls would burn a core doing
nothing. `tests/test_sense_scheduler.py` measures both halves of that claim
with real elapsed time.

Percepts do not touch conversation history
------------------------------------------
This module has no reference to `Assistant` and no way to reach
`_history`; the only sink is a `PerceptStore`, which is what `ContextBuilder`
reassembles from each turn (see `context.py` for why that separation is the
point rather than an implementation detail). A test asserts the absence, by
source, so a future convenience hook cannot quietly add one.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Iterable, Mapping, NamedTuple, Optional

from shani_chronoa import reflex
from shani_chronoa.reflex import ReflexRunner
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import Percept, Sense, discover_senses
from shani_chronoa.senses.latch import LatchRegistry
from shani_chronoa.senses.store import PerceptStore

logger = logging.getLogger(__name__)

# The one sense allowed to write durable (TTL-less) percepts, mirroring
# `senses/memory.py`'s own `CONSENT_SENSE`. Spelled as a constant here
# rather than imported so this infrastructure module does not import a sense
# module; `tests/test_sense_scheduler.py` asserts the two agree, so a rename
# on either side is a red test rather than a silently widened disk tier.
DURABLE_SENSE_NAME = "memory"

# How long the loop sleeps when nothing is ambient at all. Bounded rather than
# `Event.wait()` with no timeout so the thread re-checks its stop flag and any
# future registry change; it costs one wake-up a second.
_IDLE_TICK_SECONDS = 1.0

# How often armed *event* rules are re-read, when an event engine is supplied.
# Deliberately not `_IDLE_TICK_SECONDS`: `read_event_signal` shells out to
# `git status` and `systemctl show` (`_SIGNAL_TIMEOUT_SECONDS` is 10s because
# both are unbounded in the kernel), so polling them at the idle tick would
# make a background thread run `git status` once a second for as long as the
# loop lived. Matched to `triggers.HEARTBEAT_BUCKET_SECONDS` so a heartbeat
# bucket and a poll cannot alias into double-reporting the same transition.
_EVENT_POLL_SECONDS = 15.0

# A sense that raises is logged at ERROR once, then at DEBUG until it starts
# succeeding again. An ambient sense that fails on every tick would otherwise
# write one stack trace per interval, which is how a real fault gets missed.
_FAILURE_LOG_CEILING = 1

# Recent poll outcomes, so an unattended run can be reported afterwards rather
# than only to the log. Bounded for the same reason `store.py` bounds its
# transient window: a scheduler left running for a day would otherwise
# accumulate a record with no bound on it.
_RESULT_HISTORY = 128

# How long an ambient percept may hold unchanged before it is deposited again.
# Fifteen minutes is long enough that a stable condition does not restate
# itself every poll, and short enough that a user who missed the first notice
# is not still looking at a stale one hours later.
_DEFAULT_REARM_SECONDS = 900.0

# `config._SENSE_CONSENT_KEYS` is the authoritative sense -> consent-key list,
# read here through `getattr` with an empty default. A copy of the table would
# be a second list to keep in step with the first, and a bare attribute access
# would take the whole sensory layer down over a rename in a file this
# scheduler does not otherwise depend on for control flow. If the name ever
# moves, this returns [] and `tests/test_sense_scheduler.py` fails, which is
# the correct time to find out.
_CONSENT_KEY_TABLE = "_SENSE_CONSENT_KEYS"


class PollResult(NamedTuple):
    """The outcome of one poll attempt, successful or not.

    Returned rather than raised because a scheduler runs unattended: an
    unattended loop that exits on the first failure is not a scheduler, it is
    a one-shot. `ok` says whether a percept was deposited; `reason` is the
    user-facing explanation when it was not.

    `denied` distinguishes "consent said no" from "the sense broke". A
    consent denial is a standing state rather than an event, so `poll_due()`
    records it once instead of appending one refused result per interval.

    `suppressed` distinguishes a third case: the sense ran correctly and
    produced a percept, but the percept said exactly what the last one said,
    so it was not deposited. That is neither a success (nothing was stored)
    nor a failure (nothing broke) nor a denial (consent was fine), and
    collapsing it into any of the three would make the poll history lie about
    what the scheduler did.
    """

    name: str
    ok: bool
    percept: Optional[Percept]
    reason: str
    denied: bool = False
    suppressed: bool = False

    def as_dict(self) -> dict:
        return {
            "sense": self.name,
            "ok": self.ok,
            "reason": self.reason or None,
            "denied": self.denied,
            "suppressed": self.suppressed,
            "percept": None
            if self.percept is None
            else {
                "kind": self.percept.kind,
                "created_at": self.percept.created_at,
                "ttl_seconds": self.percept.ttl_seconds,
                "source": self.percept.source,
                "sensitivity": self.percept.sensitivity,
            },
        }


class AmbientEntry(NamedTuple):
    """One row of `plan()`: what the scheduler knows about a sense name.

    `registered` False with `allowed` True is a consent key whose module is
    missing (a deleted user drop-in, say) - and the whole point of reporting
    it rather than hiding it is that a user asking "will this ever poll?"
    should get "no, and here is why", not silence.
    """

    name: str
    registered: bool
    ambient: bool
    poll_interval: Optional[float]
    allowed: bool
    denial: str
    refusal: str
    will_poll: bool

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "registered": self.registered,
            "ambient": self.ambient,
            "poll_interval": self.poll_interval,
            "allowed": self.allowed,
            "denial": self.denial or None,
            "refusal": self.refusal or None,
            "will_poll": self.will_poll,
        }


def consent_sense_names() -> "list[str]":
    """Every sense name the config layer declares a consent key for.

    Queried from the table rather than from the registry, because the two are
    deliberately independent: a key may ship before its module does, and a
    module may exist without a key (a user drop-in, which is therefore
    permanently denied until a key exists - the same trap
    `tests/test_sense_manifest.py` documents for registered senses).
    """
    from shani_chronoa import config as config_module

    table = getattr(config_module, _CONSENT_KEY_TABLE, {})
    return sorted(table) if isinstance(table, dict) else []


class AmbientScheduler:
    """Polls ambient-capable senses on their declared intervals.

    `senses` defaults to the real registry (`discover_senses()`); passing one
    explicitly is how both the tests and the headless CLI share an already
    loaded registry instead of paying for a second load.

    `store` defaults to a real `PerceptStore`. There is deliberately no
    "discard" mode: an ambient sense that is polled and its percept thrown
    away is a capture with no consumer, which is the behaviour most worth
    avoiding.
    """

    def __init__(
        self,
        senses: Optional[Mapping[str, Sense]] = None,
        store: Optional[PerceptStore] = None,
        config_factory: Callable[[], ChronoaConfig] = ChronoaConfig,
        arguments: Optional[Mapping[str, dict]] = None,
        poll_immediately: bool = True,
        rearm_seconds: float = _DEFAULT_REARM_SECONDS,
        event_engine=None,
        event_poll_seconds: float = _EVENT_POLL_SECONDS,
        reflex_layer=None,
    ) -> None:
        # The reflex layer is a constructor argument so a test can substitute
        # one and so an embedding application can switch it off - but the
        # default is the real thing, because a reflex layer that exists and is
        # never constructed is exactly the dead-code shape this repo keeps
        # recording. `False` disables it explicitly.
        if reflex_layer is False:
            self._reflexes = None
        elif reflex_layer is None:
            self._reflexes = ReflexRunner()
        else:
            self._reflexes = reflex_layer
        self._senses: dict[str, Sense] = dict(senses if senses is not None else discover_senses())
        self._store = store if store is not None else PerceptStore()
        self._config_factory = config_factory
        self._arguments: dict[str, dict] = {
            name: dict(values) for name, values in (arguments or {}).items()
        }
        self._refusals = self._build_refusals()

        now = time.monotonic()
        # `_last_poll` is the scheduling state, on a monotonic clock so a
        # wall-clock jump (NTP, a suspended laptop) cannot make every sense
        # look instantly due - or never due. It is seeded one interval in the
        # past so the first tick is immediate by default: a scheduler that
        # reports nothing for a whole interval after `start()` looks broken.
        self._last_poll: dict[str, float] = {
            name: now - (sense.poll_interval or 0.0) if poll_immediately else now
            for name, sense in self._senses.items()
            if sense.is_ambient() and name not in self._refusals
        }

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._results: "deque[PollResult]" = deque(maxlen=_RESULT_HISTORY)
        self._denied: dict[str, str] = {}
        # Per-sense re-arm state for de-duplicating unchanged percepts. See
        # `poll()` for why this is not the "discard" mode ruled out above.
        self._rearm = LatchRegistry(rearm_seconds=rearm_seconds)
        # Consecutive-failure counts, for the log-level policy above. Written
        # and read only by whichever thread is polling, so it needs no lock.
        self._failures: dict[str, int] = {}
        self.polls = 0
        self.deposited = 0

        # Armed *event* rules (`triggers.EventEngine`), injected rather than
        # imported: this module has no dependency on `triggers`, on `Assistant`,
        # or on any sense, and `test_percepts_never_reach_assistant_history`
        # asserts the first two by AST on the parsed tree. An engine supplied
        # here is polled on the same thread and the same tick as the senses, so
        # there is still one thread and one wake-up. `None` - the default -
        # means exactly what it meant before this existed: no event rules are
        # read, and every existing construction site is unchanged.
        self._event_engine = event_engine
        self._event_seconds = max(0.0, float(event_poll_seconds))
        self._last_event_poll = (
            (now - self._event_seconds) if poll_immediately else now
        ) if event_engine is not None else now
        self._event_results: "deque[object]" = deque(maxlen=_RESULT_HISTORY)
        self._event_failures = 0
        self.event_polls = 0
        self.event_fired = 0

    # --- what will and will not be polled -----------------------------------

    def _build_refusals(self) -> "dict[str, str]":
        """Static reasons a registered sense will never be polled, by name.

        Computed once, from the registry alone, so a refusal cannot depend on
        transient state and cannot be forgotten by a later code path. Consent
        is deliberately *not* here: that is re-read every tick, so flipping a
        gsetting takes effect without a restart.
        """
        refusals: dict[str, str] = {}
        for name, sense in self._senses.items():
            if not sense.is_ambient():
                continue
            if sense.ttl_seconds is None and name != DURABLE_SENSE_NAME:
                reason = (
                    "declares ttl_seconds=None, which PerceptStore would write to "
                    f"disk on every poll; only the {DURABLE_SENSE_NAME!r} sense may be "
                    "durable"
                )
                refusals[name] = reason
                logger.error("Never polling sense %r: %s", name, reason)
        return refusals

    def ambient_senses(self) -> "dict[str, Sense]":
        """The senses this scheduler will actually poll, by name."""
        return {name: self._senses[name] for name in self._last_poll}

    def plan(self, names: "Iterable[str]" = ()) -> "list[AmbientEntry]":
        """Report every known sense name and whether it can be polled.

        `names` widens the report beyond what this module can see; the
        headless CLI passes the consent keys the *installed schema* declares,
        so a schema that is ahead of (or behind) `config` still shows up
        rather than silently missing from the answer.
        """
        config = self._config_factory()
        universe = dict.fromkeys([*self._senses, *consent_sense_names(), *names])
        entries: list[AmbientEntry] = []
        for name in sorted(universe):
            sense = self._senses.get(name)
            allowed = config.sense_allowed(name)
            denial = config.sense_allowed_reason(name)
            refusal = self._refusals.get(name, "")
            if sense is None:
                refusal = "no sense module is registered under that name"
            elif not sense.is_ambient():
                refusal = refusal or "reactive only (poll_interval is None)"
            entries.append(
                AmbientEntry(
                    name=name,
                    registered=sense is not None,
                    ambient=bool(sense is not None and sense.is_ambient()),
                    poll_interval=sense.poll_interval if sense is not None else None,
                    allowed=allowed,
                    denial=denial,
                    refusal=refusal,
                    will_poll=name in self._last_poll and allowed and not denial,
                )
            )
        return entries

    # --- polling -------------------------------------------------------------

    def poll(self, name: str) -> PollResult:
        """Run one sense once, if it is allowed to be run at all.

        The consent check is the first statement that can do anything
        observable. Everything before it is a dictionary lookup, and
        everything after it is real capture work.

        A sense that declared `poll_interval=None` is refused here as well as
        in the loop: `poll()` is public, and a public method that could run a
        self-declared reactive-only sense on demand would make that
        declaration advisory rather than binding.
        """
        sense = self._senses.get(name)
        if sense is None:
            return PollResult(name, False, None, "no sense module is registered under that name")

        refusal = self._refusals.get(name)
        if refusal is not None:
            return PollResult(name, False, None, refusal)

        if not sense.is_ambient():
            return PollResult(name, False, None, "reactive only (poll_interval is None)")

        config = self._config_factory()
        if not config.sense_allowed(name):
            return PollResult(
                name,
                False,
                None,
                config.sense_allowed_reason(name) or "consent denied",
                denied=True,
            )

        with self._lock:
            self.polls += 1
        try:
            result = sense.run(dict(self._arguments.get(name, {})))
        except Exception as exc:  # noqa: BLE001 - an unattended loop must survive
            self._note_failure(name, exc)
            return PollResult(name, False, None, f"{type(exc).__name__}: {exc}")

        # A `str` is the contract's documented convenience form; the loader
        # wraps it with this sense's declared shape, and the reactive CLI
        # (`cmd_run`) stores it the same way, so both modes file a refusal
        # string identically rather than one mode quietly diverging.
        percept = result if isinstance(result, Percept) else sense.to_percept(str(result))

        if not isinstance(percept, Percept):
            return PollResult(name, False, None, f"run() returned {type(result).__name__}, not text or a Percept")

        # Last gate before disk. The registry check above catches a sense that
        # *declares* a durable lifetime; this catches one that *returns* one.
        if percept.ttl_seconds is None and name != DURABLE_SENSE_NAME:
            reason = (
                "returned a durable (ttl_seconds=None) percept; only the "
                f"{DURABLE_SENSE_NAME!r} sense may write to disk"
            )
            logger.error("Discarding percept from sense %r: %s", name, reason)
            return PollResult(name, False, None, reason)

        # Re-arm / de-duplication. This is not the discard mode the class
        # docstring rules out: that one is about never keeping a percept at
        # all, which is a capture with no consumer. This is the opposite - the
        # percept is real, correct and wanted, it was kept the first time, and
        # re-adding an identical copy every interval would only spend context
        # budget restating a fact the store already holds. An ambient sense
        # polled once a minute would otherwise write 1440 identical entries a
        # day, and the tenth most-recent-most-relevant percepts the context
        # builder keeps would all be the same sentence. A condition that
        # *changes* re-states immediately; one that holds re-arms after the
        # quiet window, so "the camera is still in use" is re-asserted to a
        # user who may have stopped looking, without ever becoming a spam loop.
        if not self._rearm.should_emit(name, percept.content):
            return PollResult(
                name,
                False,
                percept,
                "unchanged since the last poll; not re-deposited",
                suppressed=True,
            )

        self._note_success(name)
        self._store.add(percept)
        with self._lock:
            self.deposited += 1
        return PollResult(name, True, percept, "")

    def _note_failure(self, name: str, exc: BaseException) -> None:
        """Log a failure loudly the first time and quietly thereafter."""
        count = self._failures.get(name, 0) + 1
        self._failures[name] = count
        message = "Ambient sense %r failed: %s: %s"
        if count <= _FAILURE_LOG_CEILING:
            logger.error(message, name, type(exc).__name__, exc, exc_info=True)
        else:
            logger.debug(message, name, type(exc).__name__, exc)

    def _note_success(self, name: str) -> None:
        self._failures.pop(name, None)

        # **The runner lock, added 2026-10-04.** `runner_lock.holds()` TAKES
        # the lock when it is free and reports False only when another process
        # already holds it, so `not holds()` means "the window or the daemon is
        # already running the engine" and this process leaves the rules alone,
        # retrying on its next poll. Without this, two processes poll one rules
        # file and every rule fires twice.
        #
        # This guard was missing from the committed implementation, which is why
        # `test_the_scheduler_leaves_the_rules_alone_without_the_lock` was
        # failing before any of this session's work. I briefly "fixed" it in the
        # other direction - `if runner_lock.holds()` - which was worse: it made
        # the lock-HOLDER refuse to poll, so the engine never ran at all and
        # `seconds_until_next_due()` stayed at 0.0. Six tests caught that. The
        # question is about acquiring, not about possession.
    def _poll_event_rules(self, now: float) -> "list[object]":
        """Re-read every armed event rule's signal, and dispatch what fired.

        A no-op unless an event engine was injected. Two things are borrowed
        from `poll()` on purpose and one is deliberately *not*:

        - the interval check and the stamp-after-the-run, so `event_poll_seconds`
          means the same thing here as `poll_interval` does for a sense;
        - the same log-then-go-quiet failure policy, because a corrupt rules
          file must not turn every ambient run into a stack trace.

        Consent is **not** checked here. `EventEngine._consent` re-reads it per
        rule per event, and a refusal recorded there is auditable - it names the
        key that is off on the rule that was denied. Skipping the poll at this
        level would be quieter and would throw that record away, which is the
        one outcome a consent gate must never produce.

        A raised exception is contained here rather than allowed out: this runs
        inside the sense loop, and one unreadable rules file must not stop the
        senses from being polled.
        """
        if self._event_engine is None:
            return []
        from shani_chronoa import runner_lock
        if not runner_lock.holds():
            return []
        if now - self._last_event_poll < self._event_seconds:
            return []
        self._last_event_poll = time.monotonic()
        try:
            # `poll()` reads the signals and runs the anti-noise layers;
            # `run_due()` dispatches the debounced runs whose delay elapsed,
            # re-checking consent and cooldown at the moment they act.
            out = list(self._event_engine.poll())
            out.extend(self._event_engine.run_due())
        except Exception as exc:  # noqa: BLE001 - the sense loop must survive
            self._event_failures += 1
            message = "Event trigger poll failed: %s: %s"
            if self._event_failures <= _FAILURE_LOG_CEILING:
                logger.error(message, type(exc).__name__, exc, exc_info=True)
            else:
                logger.debug(message, type(exc).__name__, exc)
            return []

        self._event_failures = 0
        self.event_polls += 1
        self.event_fired += sum(1 for r in out if getattr(r, "fired", False))
        self._event_results.extend(out)
        return out

    def poll_due(self, now: Optional[float] = None) -> "list[PollResult]":
        """Poll every ambient sense whose interval has elapsed.

        The state this advances lives on the instance, so calling it from a
        test with an explicit `now` and from the scheduler thread are the same
        code path - the thread test proves the threading, not a private
        duplicate of the scheduling.

        A consent denial is not returned as a result. It is a standing state
        that would otherwise append one refused row per interval for the life
        of the process, and `plan()` is where the user asks that question.
        """
        current = time.monotonic() if now is None else now
        results: list[PollResult] = []
        for position, (name, sense) in enumerate(self.ambient_senses().items()):
            if current - self._last_poll.get(name, current) < (sense.poll_interval or 0.0):
                continue
            # A stop cancels the senses *after* the one in hand; it must not
            # cancel the first. `break` on any stop meant a caller that started
            # and immediately stopped lost the very poll it was promised -
            # `test_start_is_idempotent` asserts one poll after a start/stop pair
            # and saw none. The purpose of the check is "do not begin another
            # slow sense once told to stop", which is about the ones still to
            # come, not the one already due.
            if position and self._stop.is_set():
                break
            result = self.poll(name)
            # Stamped after the run, from the real clock. Stamping before it
            # would make `poll_interval` mean "interval between *starts*" for a
            # sense slower than its own interval, so a 2s OCR at a 30s interval
            # would run back-to-back forever. The loop is strictly serial, so
            # there is no queue for an early stamp to build up behind.
            self._last_poll[name] = time.monotonic()
            if result.denied:
                self._denied[name] = result.reason
                continue
            self._results.append(result)
            results.append(result)
        self._poll_event_rules(current)
        return results

    def seconds_until_next_due(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds until the earliest sense - or event rule - is due.

        `None` only when there is genuinely nothing to wait for. An event
        engine with no ambient senses is still something to wait for, so it
        keeps the loop waking on its own interval instead of falling back to
        the one-second idle tick.
        """
        current = time.monotonic() if now is None else now
        remaining = [
            max(0.0, (sense.poll_interval or 0.0) - (current - self._last_poll.get(name, current)))
            for name, sense in self.ambient_senses().items()
        ]
        if self._event_engine is not None:
            remaining.append(
                max(0.0, self._event_seconds - (current - self._last_event_poll))
            )
        return min(remaining) if remaining else None

    def event_results(self) -> "list[object]":
        """Recent event-rule outcomes, oldest first. Safe from any thread."""
        with self._lock:
            return list(self._event_results)

    # --- lifecycle -----------------------------------------------------------

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Begin polling. Idempotent; a second call is a no-op, not a second thread."""
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="chronoa-ambient-senses", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop polling and wait for the thread. Idempotent.

        The join is bounded rather than unbounded: the sense currently running
        belongs to its own author, and a stop that can hang forever is how a
        shutdown path turns into a hung session. `Event.wait` in the loop is
        what normally makes this return in well under a millisecond.
        """
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def run_for(self, seconds: float) -> "list[PollResult]":
        """Start, run for `seconds` of real time, then stop. Returns the outcomes.

        The blocking entry point the headless CLI drives. Sleeps on the stop
        event rather than a bare `sleep` so a `KeyboardInterrupt` lands
        between polls rather than being swallowed by one.
        """
        self.start()
        try:
            deadline = time.monotonic() + max(0.0, seconds)
            while not self._stop.wait(0.05):
                if time.monotonic() >= deadline:
                    break
        except KeyboardInterrupt:
            logger.info("Ambient polling interrupted; stopping.")
        finally:
            self.stop()
        return self.results()

    @property
    def reflex_layer(self):
        """The reflex runner in use, or None when disabled."""
        return self._reflexes

    def _reflex_tick(self) -> list:
        """Run the reflex layer and say whatever it has to say.

        Deliberately on the sense loop rather than inside `poll_due()`. Two
        reasons, both about what a reflex is for: a reflex must answer when
        there is no percept to deposit and no model to deposit it to, and it
        must keep answering when every sense is refused or the store has
        failed. Both are conditions `poll_due()` can return from having done
        nothing at all, which is exactly when "battery is at 8%" must still
        reach the person.

        It cannot make the loop wait either. `due()` is pure reads of sysfs
        and /proc - sub-millisecond, measured in `tests/test_reflex.py` - and
        `notify` is contained, so a reflex cannot stall the sense tick.
        """
        if not self._reflexes:
            return []
        # Consolidation rides this tick because it is the only thing guaranteed
        # to run for the life of the process. It is off the hot path by its own
        # gate - one stat of the log, one read of the model's provenance - and
        # contained, because an offline pass that raises must not stop senses
        # from being polled.
        try:
            self._reflexes.consolidate()
        except Exception as exc:  # noqa: BLE001 - the tick must survive
            logger.debug("consolidation failed and was skipped: %s: %s",
                         type(exc).__name__, exc)
        try:
            urges = self._reflexes.due()
        except Exception as exc:  # noqa: BLE001 - the tick must survive
            logger.debug("reflex layer failed and said nothing: %s: %s",
                         type(exc).__name__, exc)
            return []
        if urges:
            try:
                reflex.notify(urges)
            except Exception as exc:  # noqa: BLE001
                logger.debug("reflex notify failed: %s: %s", type(exc).__name__, exc)
        return urges

    def _loop(self) -> None:
        # **The first iteration runs even if `stop()` has already been called.**
        # The loop otherwise begins `while not self._stop.is_set()`, so a caller
        # that starts and immediately stops can set the flag before the new
        # thread is ever scheduled - and the scheduler then reports a successful
        # start having polled nothing, which `test_start_is_idempotent` catches.
        #
        # The obvious alternative - polling synchronously inside `start()` - is
        # worse twice over: it polls a second time on top of the thread's own
        # first poll (two back-to-back polls, which
        # `test_a_slower_sense_is_not_polled_back_to_back` rejects), and placing
        # it inside `with self._lock` deadlocks, because `poll_due()` takes the
        # same non-reentrant lock. A deadlock is worse than a failure: the test
        # hangs and reports nothing at all.
        first = True
        while first or not self._stop.is_set():
            first = False
            self._reflex_tick()
            self.poll_due()
            if self._stop.is_set():
                break
            remaining = self.seconds_until_next_due()
            # `Event.wait`, never `time.sleep`: a sleep would keep this thread
            # holding the GIL in a syscall for the whole interval and would
            # make `stop()` wait out the interval before it took effect.
            self._stop.wait(
                _IDLE_TICK_SECONDS if remaining is None else max(0.001, remaining)
            )

    def __enter__(self) -> "AmbientScheduler":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # --- reporting -----------------------------------------------------------

    def results(self) -> "list[PollResult]":
        """Recent poll outcomes, oldest first. Safe to read from any thread."""
        with self._lock:
            return list(self._results)

    def summary(self) -> dict:
        """A small machine-readable report of what this scheduler is doing."""
        with self._lock:
            polls, deposited = self.polls, self.deposited
        return {
            "running": self.running,
            "ambient_senses": sorted(self.ambient_senses()),
            "refused": dict(self._refusals),
            "denied": dict(self._denied),
            "event_triggers": {
                "enabled": self._event_engine is not None,
                "interval_seconds": self._event_seconds,
                "polls": self.event_polls,
                "fired": self.event_fired,
                "rules": sorted(
                    r.name for r in self._event_engine.store().all()
                ) if self._event_engine is not None else [],
            },
            "polls": polls,
            "deposited": deposited,
            "last_results": [result.as_dict() for result in self.results()],
        }
