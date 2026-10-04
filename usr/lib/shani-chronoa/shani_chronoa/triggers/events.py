"""The event engine: reads each armed rule's source, and fires its actuator through debouncing, dedupe, backoff and consent."""

from __future__ import annotations


import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from shani_chronoa import verification
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.tool_tracking import ORIGIN_APPROVED

from .common import (  # noqa: F401
    EVENT_AUDIODEVICE,
    EVENT_BTCONNECT,
    EVENT_CALENDAR,
    EVENT_CONTAINERRUN,
    EVENT_DBUSPROP,
    EVENT_EXPIRY,
    EVENT_FAILURE,
    EVENT_FSWATCH,
    EVENT_GIT,
    EVENT_JOURNALMATCH,
    EVENT_NETSTATE,
    EVENT_PHONE,
    EVENT_SOUND,
    EVENT_POWERSTATE,
    triggers_dir,
    EVENT_SCHEDULE,
    EVENT_SCREENLOCK,
    EVENT_SLEEPWAKE,
    EVENT_UNITHEALTH,
    EVENT_USBPLUG,
    Event,
    EventSignal,
    FAILURE_ACTUATOR_DID_NOT_RUN,
    FAILURE_ACTUATOR_RAISED,
    FAILURE_NONE,
    FAILURE_VERIFICATION_FAILED,
    HEARTBEAT_BUCKET_SECONDS,
    LAYER_COOLDOWN,
    LAYER_DEDUPE_WINDOW,
    LAYER_DUPLICATE_EVENT,
    LAYER_FILTER_MISMATCH,
    RETRY_TERMINAL,
    SIGNAL_OK,
    SIGNAL_UNAVAILABLE,
    _DESTRUCTIVE_ACTUATORS,
    _NOTIFY_ONLY_ACTUATORS,
    _NOTIFY_ONLY_EVENT_TYPES,
    _ORIGIN,
    _actuator_problem,
    _ensure_state_dir,
    _restrict_file,
    _unavailable,
)
from .sources import (  # noqa: F401
    read_container_state,
    read_expiry,
    read_failure_verdict,
    read_git_state,
    read_unit_health,
    read_watched_path,
)
from .desktop_sources import (  # noqa: F401
    SCHEDULE_GRACE_MINUTES,
    read_audiodevice,
    read_btconnect,
    read_calendar,
    read_dbusprop,
    read_journalmatch,
    read_netstate,
    read_phone,
    read_sound,
    read_powerstate,
    read_schedule,
    read_screenlock,
    read_sleepwake,
    read_usbplug,
)
from .rules import (  # noqa: F401
    RuleStoreError,
    TriggerEngine,
)
from .event_rules import (  # noqa: F401
    EventRule,
    EventRuleStore,
)
def fingerprints_file() -> Path:
    """Durable fingerprints of fired events, resolved per call (see common.triggers_dir)."""
    return triggers_dir() / "fingerprints.json"


# --- layer 3: debounce, the window `latch` and `due()` do not cover -------

class _PendingRun(NamedTuple):
    event: "Event"
    scheduled_for: float


class DedupeWindow:
    """Collapse a burst for one subject into ONE delayed run.

    The operation is `scheduled_for = max(existing, now + delay)`, and the
    `max` is the design. A window that *dropped* a second event would lose it -
    and a lost build failure is invisible in the audit log, because the log
    faithfully records that the first one ran. Delaying instead means a burst
    of twenty writes produces one run carrying the last event, at a time that
    is never earlier than any of them.

    Keyed by subject, not globally. A single global window means one hot file
    pushes the deadline out forever and every other path in the tree starves -
    the failure this class exists to prevent, and the reason it is keyed rather
    than being a single timestamp on the engine.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: "dict[str, _PendingRun]" = {}

    def submit(self, key: str, event: "Event", now: float, delay: float) -> float:
        """Arm (or push back) the one run for `key`. Returns `scheduled_for`."""
        with self._lock:
            existing = self._pending.get(key)
            when = now + max(0.0, delay)
            if existing is not None and existing.scheduled_for > when:
                when = existing.scheduled_for
            self._pending[key] = _PendingRun(event, when)
            return when

    def due(self, key: str, now: float) -> bool:
        with self._lock:
            entry = self._pending.get(key)
            return entry is not None and entry.scheduled_for <= now

    def peek(self, key: str) -> "Optional[float]":
        with self._lock:
            entry = self._pending.get(key)
            return None if entry is None else entry.scheduled_for

    def consume(self, key: str) -> "Optional[Event]":
        """Take the armed run, if any. A consumed run fires exactly once."""
        with self._lock:
            entry = self._pending.pop(key, None)
            return None if entry is None else entry.event

    def cancel(self, key: str) -> None:
        with self._lock:
            self._pending.pop(key, None)

    def keys(self) -> "list[str]":
        with self._lock:
            return sorted(self._pending)

    def pending(self) -> "dict[str, float]":
        with self._lock:
            return {key: entry.scheduled_for for key, entry in self._pending.items()}


class HeartbeatBucket:
    """At most one progress update per subject per window.

    Only *progress* is bucketed. A state transition bypasses the bucket
    entirely, because a stalled run that is preceded by a progress line must
    still be reported - bucketing the transition would hide exactly the event
    the bucket exists to make visible.
    """

    def __init__(
        self, window: float = HEARTBEAT_BUCKET_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._window = float(window)
        self._clock = clock
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def should_report(self, subject: str) -> bool:
        now = self._clock()
        with self._lock:
            last = self._last.get(subject)
            if last is not None and (now - last) < self._window:
                return False
            self._last[subject] = now
            return True

    def reset(self, subject: Optional[str] = None) -> None:
        with self._lock:
            if subject is None:
                self._last.clear()
            else:
                self._last.pop(subject, None)


class DurableFingerprints:
    """The fingerprint store that makes "changed?" survive a reboot.

    `senses/latch.py` is per-process by design and says so: persisting would
    mean deciding what "unchanged since" means across a reboot. That decision
    is unavoidable here rather than optional, because a trigger that re-notifies
    on every boot about a repository that has not changed is precisely the spam
    this layer exists to stop - and it is silent, because every individual
    notification is individually reasonable.

    Written whole via an atomic rename, like `RuleStore`: a truncated counter
    file would make every rule look changed at once, which is the one state
    from which the engine cannot recover.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else fingerprints_file()
        self._lock = threading.RLock()
        self._values: "dict[str, str]" = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        with self._lock:
            if not self._path.is_file():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuleStoreError(
                    f"the fingerprint file {self._path} is corrupt or truncated: "
                    f"{exc}; refusing to load rather than re-firing every rule"
                ) from exc
            if not isinstance(raw, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
            ):
                raise RuleStoreError(
                    f"the fingerprint file {self._path} is not a string->string map; "
                    "refusing to load"
                )
            self._values = raw

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_state_dir(self._path.parent)
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(self._values, indent=2, sort_keys=True), encoding="utf-8"
            )
            _restrict_file(tmp)
            os.replace(tmp, self._path)
            _restrict_file(self._path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuleStoreError(
                f"could not write the fingerprint file {self._path}: {exc}"
            ) from exc

    def seen(self, key: str) -> bool:
        """Whether a baseline has ever been recorded for `key`.

        Separate from `changed()` on purpose. The first observation of a state
        is the *baseline*, not a change: there is no previous state to have
        changed from, and reporting it would mean arming a rule on a repository
        notifies you about the repository's current contents, and arming one on
        a passing check announces that it is passing. `changed()` answers True
        for an unseen key, so a caller that skips this check converts "arm" into
        "act immediately" for all six types.
        """
        with self._lock:
            return key in self._values

    def get(self, key: str) -> "Optional[str]":
        with self._lock:
            return self._values.get(key)

    def changed(self, key: str, value: str) -> bool:
        """Whether `value` differs from what was last recorded for `key`."""
        with self._lock:
            return self._values.get(key) != value

    def record(self, key: str, value: str) -> None:
        with self._lock:
            if self._values.get(key) == value:
                return
            self._values[key] = value
            self._write()

    def restore(self, key: str, value: str) -> None:
        """Put back a value `record()` replaced, for a failed attempt.

        Not `forget()`: deleting the key entirely makes the next read look like a
        *first sighting* and re-baseline, so a failure silently converts the
        retry into a fresh arm and the rule never retries the state that failed.
        Restoring the previous value keeps `seen()` true and `changed()` true,
        which is what "this transition is still outstanding" means.
        """
        self.record(key, value)

    def count(self) -> int:
        with self._lock:
            return len(self._values)


def read_event_signal(
    rule: "EventRule", now: Optional[float] = None
) -> EventSignal:
    """Dispatch one rule to its reader.

    Reads the rule's own declared source, and nothing else. There is no
    fallback reader and no generic path: an event type this function does not
    name returns unavailable rather than being approximated by another type's
    signal, because a plausible-looking wrong answer is the failure the senses
    section of `AGENTS.md` is about.

    `seams` (a live watcher, a container runtime path) is consulted before
    `params`, and only for the reader options that have one. A seam is not
    persisted, so a reloaded rule falls back to the real reader rather than
    rehydrating a stale handle.
    """
    params = {**rule.params, **rule.seams}
    if rule.event_type == EVENT_GIT:
        return read_git_state(Path(rule.source), now=now)
    if rule.event_type == EVENT_FSWATCH:
        return read_watched_path(
            rule.source, watcher=params.get("watcher"), now=now
        )
    if rule.event_type == EVENT_FAILURE:
        return read_failure_verdict(
            rule.source, source=params.get("signal", "verdict-file"),
            directory=params.get("directory"), now=now,
        )
    if rule.event_type == EVENT_EXPIRY:
        return read_expiry(
            rule.source, directory=params.get("directory"), now=now
        )
    if rule.event_type == EVENT_CONTAINERRUN:
        return read_container_state(
            rule.source, runtime=params.get("runtime"),
            stalled_after=float(params.get("stalled_after", 900.0)), now=now,
        )
    if rule.event_type == EVENT_UNITHEALTH:
        return read_unit_health(rule.source, now=now)
    if rule.event_type == EVENT_SCREENLOCK:
        return read_screenlock(rule.source, now=now)
    if rule.event_type == EVENT_POWERSTATE:
        return read_powerstate(rule.source, now=now)
    if rule.event_type == EVENT_NETSTATE:
        return read_netstate(rule.source, now=now)
    if rule.event_type == EVENT_USBPLUG:
        return read_usbplug(rule.source, now=now)
    if rule.event_type == EVENT_BTCONNECT:
        return read_btconnect(rule.source, now=now)
    if rule.event_type == EVENT_PHONE:
        return read_phone(rule.source, now=now)
    if rule.event_type == EVENT_SOUND:
        return read_sound(rule.source, now=now)
    if rule.event_type == EVENT_CALENDAR:
        return read_calendar(rule.source, now=now)
    if rule.event_type == EVENT_SLEEPWAKE:
        return read_sleepwake(rule.source, now=now)
    if rule.event_type == EVENT_AUDIODEVICE:
        return read_audiodevice(rule.source, now=now)
    if rule.event_type == EVENT_JOURNALMATCH:
        return read_journalmatch(rule.source, now=now)
    if rule.event_type == EVENT_DBUSPROP:
        return read_dbusprop(rule.source, now=now)
    if rule.event_type == EVENT_SCHEDULE:
        return read_schedule(rule.source, grace_minutes=float(params.get("grace_minutes", SCHEDULE_GRACE_MINUTES)),
                             now=now)
    return _unavailable(
        rule.event_type, rule.source, f"{rule.event_type!r} is not a known event type"
    )


# --- the event firing engine -----------------------------------------------

class EventEvaluation(NamedTuple):
    """What happened to one event rule for one signal read.

    `layer` names the anti-noise layer that answered, and it is `None` only
    when the rule actually fired. `censored` is separate from `suppressed`
    because a pre-empted firing must be *recorded* as pre-empted: a denial that
    leaves no trace, or that is averaged in with the duplicate events and the
    cooldown skips, is exactly the unauditable outcome a consent gate must never
    produce.
    """

    rule: "EventRule"
    fired: bool = False
    suppressed: bool = False
    censored: bool = False
    layer: "Optional[str]" = None
    reason: str = ""
    verdict: "Optional[verification.Verdict]" = None
    event: "Optional[Event]" = None
    signal_status: str = SIGNAL_OK
    scheduled_for: "Optional[float]" = None
    failure_kind: str = FAILURE_NONE

    def __repr__(self) -> str:
        return (
            f"EventEvaluation(rule={self.rule.name!r}, fired={self.fired}, "
            f"suppressed={self.suppressed}, censored={self.censored}, "
            f"layer={self.layer!r}, failure_kind={self.failure_kind!r}, "
            f"reason={self.reason!r})"
        )


class EventEngine:
    """Turns the event types into whitelisted actuator calls, under consent.

    The order of the checks is the design, and it is the order the percept
    engine above already established:

    1. **Signal availability first.** An unreadable dependency is
       `signal unavailable` and stops here - never "no change", never a firing.
    2. **Fingerprint, before anything is scheduled.** An unchanged state is
       `duplicate_event` and returns *without touching the debounce window*.
       This is the property that stops trigger spam: if the no-op path
       scheduled a timer, an unchanging repository would re-arm a run on every
       poll forever.
    3. **Filter.** `filter_mismatch` - a real transition this rule does not
       want. Also before the debounce window, so a rule that filters
       everything out never arms a timer at all.
    4. **Consent, before the cooldown and before the debounce window**, for the
       reason `TriggerEngine.evaluate` gives: a rule that already fired sits in
       its cooldown, so checking cooldown first makes a switched-off consent key
       look exactly like "nothing matched", and a refusal nobody can see is the
       one outcome a consent gate must never produce.
    5. **Debounce, then cooldown.** A burst becomes one *delayed* run
       (`scheduled_for = max(existing, now + debounce)`); the delayed run
       re-checks consent and cooldown at the moment it actually dispatches, so
       neither is cached from schedule time.
    """

    def __init__(
        self,
        store=None,
        config_factory=None,
        dispatch=None,
        fingerprint_store=None,
        dedupe: Optional[DedupeWindow] = None,
        heartbeats: Optional[HeartbeatBucket] = None,
        approver=None,
    ) -> None:
        from shani_chronoa.tools import execute_tool_outcome
        from shani_chronoa import approvals

        # Who asks a person for an `ask_first` rule; a seam so tests answer for them.
        self._approver = approver if approver is not None else approvals.NotifyApprover()
        self.approval_log: list = []

        self._store = store if store is not None else EventRuleStore()
        self._config_factory = config_factory or ChronoaConfig
        self._dispatch = dispatch or execute_tool_outcome
        self._prints = fingerprint_store if fingerprint_store is not None else DurableFingerprints()
        self._dedupe = dedupe if dedupe is not None else DedupeWindow()
        self._heartbeats = heartbeats if heartbeats is not None else HeartbeatBucket()

    def store(self) -> EventRuleStore:
        return self._store

    def dedupe(self) -> DedupeWindow:
        return self._dedupe

    def _consent(self, config, rule: EventRule) -> str:
        """Empty when the rule may act, otherwise why it may not.

        Delegates to `TriggerEngine._consent` first and adds only what is
        specific to an event rule. The delegation is load-bearing and it was the
        whole of the fix on 2026-09-30: `trigger-control-enabled` used to be
        checked *here only*, so the same switch gated the event path and did
        nothing for the percept path. Keeping one implementation in the base is
        what stops the two engines drifting apart again - the module's own rule,
        and the reason `EventRule.sense` reuses the base check unchanged.

        Every refusal is read per event rather than at arm time, because the
        whole point of a consent key is that turning it off takes effect
        immediately. Fail-closed, like every other consent key here: an
        undeclared key denies, because `get_bool` returns the supplied default
        and the default the base check asks for is `False`.

        The two refusals below are additions to the base's, not replacements
        for it, and both have no percept counterpart: an expiry rule may only
        notify, and a destructive actuator needs the per-rule opt-in.
        """
        denial = TriggerEngine._consent(self, config, rule)
        if denial:
            return denial
        if rule.event_type in _NOTIFY_ONLY_EVENT_TYPES and (
            rule.actuator not in _NOTIFY_ONLY_ACTUATORS
        ):
            return (
                f"a {rule.event_type} rule may only notify; {rule.actuator!r} would "
                "let the machine act on its own deadline unattended"
            )
        return _actuator_problem(rule.actuator, rule.allow_destructive, getattr(rule, "ask_first", False))

    def _fingerprint_key(self, rule: EventRule, event: Event) -> str:
        """Identifies the rule's *view of one subject*, and holds its state.

        The fingerprint is the stored **value**, not part of the key. Folding it
        into the key makes every change look like a first sighting - the rule
        re-baselines on each transition and can never report one - which is
        silent, because every individual read still looks correct.
        """
        return f"{rule.name}\x00{event.subject}"

    def _dedupe_key(self, rule: EventRule, event: Event) -> str:
        """Per rule, per subject, **and** per transition.

        The subject is in the key so a burst on one watched path cannot consume
        the debounce budget of another, and the fingerprint is in it so two
        different transitions under the same subject get independent runs
        rather than one shadowing the other.
        """
        return f"{self._fingerprint_key(rule, event)}\x00{event.fingerprint}"

    def feed(
        self, rule: EventRule, event: Event, now: Optional[float] = None
    ) -> EventEvaluation:
        """Run one already-read event through the four anti-noise layers.

        The pipeline half of `poll`, split out so the layers are testable
        without a `git` repository, a container runtime or systemd. Every event
        type goes through this same function, which is what makes "one
        transition fires once, a no-op does not" a property of the engine
        rather than of six readers.
        """
        moment = time.time() if now is None else now
        if not rule.enabled:
            return EventEvaluation(rule, suppressed=True, reason="the rule is disabled")
        if rule.parked:
            return EventEvaluation(
                rule, suppressed=True, layer=LAYER_COOLDOWN,
                reason=f"the rule is parked: {rule.parked_reason or 'no reason recorded'}",
            )

        key = self._fingerprint_key(rule, event)
        dedupe_key = self._dedupe_key(rule, event)

        # Layer 1, and the order that matters: a no-op returns here, before any
        # timer exists to be pushed back. The first sighting of a state is the
        # baseline rather than a change, so it is recorded and returns without
        # arming anything either.
        previous = self._prints.get(key)
        if previous is None:
            self._prints.record(key, event.fingerprint)
            return EventEvaluation(
                rule, suppressed=True, layer=LAYER_DUPLICATE_EVENT,
                reason="the first sighting of this state was recorded as the "
                       "baseline; nothing has changed yet",
                event=event,
            )
        if not self._prints.changed(key, event.fingerprint):
            return EventEvaluation(
                rule, suppressed=True, layer=LAYER_DUPLICATE_EVENT,
                reason="the fingerprinted state is unchanged since this rule last saw it",
                event=event,
            )

        if event.progress and not self._heartbeats.should_report(event.subject):
            return EventEvaluation(
                rule, suppressed=True, layer=LAYER_DUPLICATE_EVENT,
                reason=f"a progress heartbeat for {event.subject} was already reported "
                       f"within {HEARTBEAT_BUCKET_SECONDS:g}s",
                event=event,
            )

        # Layer 2. Recorded before the consent check below, so a rule that is
        # not consented still does not re-report the same transition on every
        # poll - the state is still consumed, only the *firing* is refused.
        if not rule.matches(event):
            self._prints.record(key, event.fingerprint)
            return EventEvaluation(
                rule, suppressed=True, layer=LAYER_FILTER_MISMATCH,
                reason=f"this rule filters out {event.subject!r}", event=event,
            )

        if rule.event_type == EVENT_CONTAINERRUN and rule.actuator in _DESTRUCTIVE_ACTUATORS:
            self._prints.record(key, event.fingerprint)
            return EventEvaluation(
                rule, suppressed=True, censored=True,
                reason=(
                    f"{rule.actuator!r} would act on a container run; killing one is "
                    "destructive, so this rule is refused even though it opted in"
                ),
                event=event,
            )

        config = self._config_factory()
        denial = self._consent(config, rule)
        if denial:
            self._prints.record(key, event.fingerprint)
            return EventEvaluation(
                rule, suppressed=True, censored=True, reason=denial, event=event,
            )

        self._prints.record(key, event.fingerprint)

        # Layers 3 and 4. A terminal or bad state parks a TERMINAL rule here,
        # before any dispatch: a rule that says "do not retry this" must not
        # act once and then act again on the same bad news.
        if rule.retry_policy == RETRY_TERMINAL and (event.terminal or event.failed):
            rule.parked = True
            rule.parked_reason = (
                f"{rule.event_type} {event.subject}: a '{rule.retry_policy}' rule does "
                f"not retry this state - {event.summary}"
            )
            return EventEvaluation(
                rule, suppressed=True, censored=True, layer=LAYER_COOLDOWN,
                reason=rule.parked_reason, event=event,
            )
        if rule.event_type == EVENT_EXPIRY and event.terminal:
            if event.detail.get("threshold") and event.detail["threshold"] not in rule.fired_thresholds:
                rule.fired_thresholds.append(str(event.detail["threshold"]))

        scheduled = self._dedupe.submit(
            dedupe_key, event, moment, rule.debounce_seconds
        )
        if scheduled > moment:
            return EventEvaluation(
                rule, suppressed=True, layer=LAYER_DEDUPE_WINDOW,
                reason=(
                    f"a burst on {event.subject!r} collapsed into one run at "
                    f"{scheduled - moment:.3f}s from now"
                ),
                event=event, scheduled_for=scheduled,
            )
        # The run this window was holding has just happened, so the armed entry
        # has to go. `submit()` above stores unconditionally, and when the delay
        # has already elapsed this branch dispatches it directly - so leaving
        # the entry armed lets `run_due()` dispatch the *same* event a second
        # time on the very next line of the poll. Found by running the real
        # `shani-chronoa-sense ambient --once` against a real repository, not
        # by reading: two `notify` calls landed in `tool_calls.log` for one
        # commit. Only reachable at `debounce_seconds == 0`, which is why the
        # suite missed it - a positive debounce returns above, and every test
        # that reaches a firing calls `feed()` directly rather than the
        # `poll()` + `run_due()` pair that runs in production. Cancelling here
        # costs nothing on the delayed path, which never gets here.
        self._dedupe.cancel(dedupe_key)
        return self._dispatch_now(
            rule, event, moment, fingerprint_key=key, previous_fingerprint=previous
        )

    def _dispatch_now(
        self, rule: EventRule, event: Event, moment: float,
        fingerprint_key: str = "", previous_fingerprint: "Optional[str]" = None,
    ) -> EventEvaluation:
        """Layer 4 plus the dispatch, with the FAILED verdict handled as before.

        The `verdict is FAILED` branch is the one this module is strongest on and
        the one an earlier pass in this repo regressed: cooldown is deliberately
        NOT started, so the rule stays due and the next transition retries
        instead of the failure being spent silently in a window.

        Three things happen here that they did not before, all of them about
        *not* calling a broken actuator a success.

        The default dispatch seam is `tools.execute_tool_outcome`, not
        `execute_tool`. `execute_tool` returns the prose string a model reads
        and throws the verdict away; `execute_tool_outcome` returns the same
        text plus the verdict and `ran` the dispatcher had already computed. A
        caller deciding whether to record a success is exactly the second kind
        of caller, and `tools.py` says so itself. An injected seam returning a
        plain string or a `verification.Result` still works - every read below
        goes through `getattr`, which is why nothing else in the suite had to
        change.

        `ran is False` is now a failure. `tools.DispatchResult` documents four
        rows, and the fourth - `ran=False, UNVERIFIED - never executed: unknown
        tool, refused by plan mode, non-zero exit, or an exception` - was
        previously indistinguishable from row two, `ran=True, UNVERIFIED - ran,
        and there is nothing to check it against`. The first says nothing
        happened; the second says it happened and cannot be checked. Recording
        the first as a success started a cooldown window on a rule that had
        done nothing.

        The exception branch now checks `exhausted()`. It called `note_failure`
        and then never asked whether that failure had reached a cap, so a raising
        actuator retried forever - measured: `consecutive_failures` climbing to
        12 with `exhausted()` True from the fifth run onward, and `parked`
        staying False the whole time. The FAILED branch already parked on
        exhaustion; the raising path is the same failure arriving by another
        door.
        """
        if not rule.due(moment):
            return EventEvaluation(
                rule, suppressed=True, layer=LAYER_COOLDOWN,
                reason=(
                    f"fired {moment - rule.last_fired_at:.1f}s ago and the cooldown is "
                    f"{rule.cooldown_seconds:g}s"
                ),
                event=event,
            )

        if getattr(rule, "ask_first", False):
            return self._ask_first(rule, event, moment)

        try:
            # origin= is what makes this distinguishable in the audit log from
            # a person asking, exactly as it is on the percept path.
            outcome = self._dispatch(rule.actuator, dict(rule.arguments), origin=_ORIGIN)
        except Exception as exc:  # noqa: BLE001 - one bad rule must not stop the rest
            reason = f"{type(exc).__name__}: {exc}"
            return self._failed(rule, event, moment, reason, None, fingerprint_key,
                                previous_fingerprint, FAILURE_ACTUATOR_RAISED)

        text = getattr(outcome, "text", outcome)
        verdict = getattr(outcome, "verdict", verification.verdict_from_text(text or ""))
        if getattr(outcome, "ran", None) is False:
            # The one UNVERIFIED that is not "it ran and cannot be checked".
            return self._failed(
                rule, event, moment,
                f"the actuator did not run: {getattr(outcome, 'text', '') or 'no detail'}",
                verdict, fingerprint_key, previous_fingerprint,
                FAILURE_ACTUATOR_DID_NOT_RUN,
            )
        if verdict is verification.Verdict.FAILED:
            return self._failed(
                rule, event, moment,
                f"actuator ran but verification failed: "
                f"{getattr(outcome, 'evidence', '') or 'no evidence'}",
                verdict, fingerprint_key, previous_fingerprint,
                FAILURE_VERIFICATION_FAILED,
            )

        rule.last_fired_at = moment
        rule.note_success(moment)
        return EventEvaluation(
            rule, fired=True, verdict=verdict, event=event,
            reason=f"{rule.event_type} {event.subject}: {event.summary}",
        )

    def _ask_first(self, rule: EventRule, event: Event, moment: float) -> EventEvaluation:
        """Post the question instead of acting; the answer arrives on another thread.

        The cooldown starts now, so one transition asks once - not once per poll
        while the notification is up. Nothing runs unless the answer is Allow,
        and then only after consent is read again: a switch turned off while the
        question was waiting wins over the tap.
        """
        from shani_chronoa import approvals

        if not self._approver.available():
            return EventEvaluation(rule, suppressed=True, censored=True, event=event,
                                   reason="this rule asks first, and there is no notification server to ask with")
        title, body = approvals.describe(rule.name, rule.actuator, rule.arguments, event.summary)
        name = rule.name

        def on_answer(answer: str) -> None:
            self.approval_log.append((name, answer))
            if answer == approvals.ALLOW:
                self.run_approved(name, event)

        asked = self._approver.request(f"{name}\x00{event.fingerprint}", title, body, on_answer)
        rule.last_fired_at = moment
        return EventEvaluation(rule, suppressed=True, event=event,
                               reason=("asked a person to allow it" if asked else
                                       "the same question is already waiting for an answer"))

    def run_approved(self, rule_name: str, event: Event) -> "Optional[EventEvaluation]":
        """Run a rule's actuator because a person allowed it - consent re-read first."""
        rule = self._store.get(rule_name)
        if rule is None:
            return None
        denial = self._consent(self._config_factory(), rule)
        if denial:
            return EventEvaluation(rule, suppressed=True, censored=True, event=event,
                                   reason=f"allowed, but no longer permitted: {denial}")
        try:
            outcome = self._dispatch(rule.actuator, dict(rule.arguments), origin=ORIGIN_APPROVED)
        except Exception as exc:  # noqa: BLE001
            return EventEvaluation(rule, suppressed=True, event=event, reason=f"{type(exc).__name__}: {exc}")
        verdict = getattr(outcome, "verdict", verification.verdict_from_text(getattr(outcome, "text", outcome) or ""))
        rule.note_success(time.time())
        return EventEvaluation(rule, fired=True, verdict=verdict, event=event,
                               reason=f"allowed by a person: {event.summary}")

    def _failed(
        self, rule: EventRule, event: Event, moment: float, reason: str,
        verdict: "Optional[verification.Verdict]",
        fingerprint_key: str = "", previous_fingerprint: "Optional[str]" = None,
        failure_kind: str = FAILURE_NONE,
    ) -> EventEvaluation:
        """One non-success outcome, from any of the three ways it can arrive.

        Shared so the raising actuator, the one that never ran and the one whose
        post-condition did not hold cannot each grow their own idea of what a
        failure does to the fingerprint, the backoff counters and the park flag.

        `verdict` was already threaded through here and is *not* total: the
        exception door passes `None`, and a bare-string seam leaves it
        UNVERIFIED-or-recovered-by-prose. `failure_kind` is that idea completed -
        it says which door, so a caller never has to infer it from the wording.
        """
        rule.note_failure(moment)
        # The recorded state is rolled back so the *same* observation is
        # eligible again. On the percept path the retry arrives as the next
        # matching percept; on the event path the same observation is the
        # retry, and leaving the new fingerprint in place would make "no
        # cooldown on a FAILED verdict" vacuous - the rule would stay due
        # and then never see an event it was allowed to act on.
        if fingerprint_key and previous_fingerprint is not None:
            self._prints.restore(fingerprint_key, previous_fingerprint)
        exhausted = rule.exhausted()
        if exhausted:
            rule.parked = True
            rule.parked_reason = reason
        return EventEvaluation(
            rule, fired=False, reason=reason, verdict=verdict, event=event,
            suppressed=not exhausted,
            censored=exhausted,
            layer=LAYER_COOLDOWN if exhausted else None,
            failure_kind=failure_kind,
        )

    def poll(self, now: Optional[float] = None) -> "list[EventEvaluation]":
        """Read every armed rule's signal and run it through `feed`."""
        moment = time.time() if now is None else now
        out: list[EventEvaluation] = []
        for rule in self._store.all():
            if not rule.enabled:
                continue
            try:
                signal = read_event_signal(rule, now=moment)
            except Exception as exc:  # noqa: BLE001 - a broken reader is not a firing
                out.append(EventEvaluation(
                    rule, suppressed=True, signal_status=SIGNAL_UNAVAILABLE,
                    reason=f"{rule.event_type} signal could not be read: "
                           f"{type(exc).__name__}: {exc}",
                ))
                continue
            if signal.status != SIGNAL_OK:
                out.append(EventEvaluation(
                    rule, suppressed=True, signal_status=signal.status,
                    reason=(
                        f"{rule.event_type} signal unavailable: {signal.detail}"
                        if signal.status == SIGNAL_UNAVAILABLE
                        else f"{rule.event_type} watcher error: {signal.detail}"
                    ),
                ))
                continue
            if signal.event is None or signal.fingerprint is None:
                # A read that succeeded and observed a state that produced no
                # event. It still has to be *recorded*, or a rule whose baseline
                # would have come from this quiet read keeps its old one and the
                # next real transition looks like the first sighting - which
                # `feed()` treats as a baseline and silently swallows. Recording
                # is not acting, so this happens regardless of filter or consent.
                subject = (signal.payload or {}).get("subject") or rule.source
                self._prints.record(
                    f"{rule.name}\x00{subject}", signal.fingerprint
                )
                continue
            out.append(self.feed(rule, signal.event, now=moment))
        return out

    def run_due(self, now: Optional[float] = None) -> "list[EventEvaluation]":
        """Dispatch the debounced runs whose delay has elapsed.

        Consent and cooldown are re-checked here, at dispatch time rather than
        at schedule time: a rule that was consented when it was scheduled and
        is not consented now must be recorded as censored, and one that fired
        while it waited must not fire a second time.
        """
        moment = time.time() if now is None else now
        out: list[EventEvaluation] = []
        for key in self._dedupe.keys():
            if not self._dedupe.due(key, moment):
                continue
            rule_name = key.split("\x00", 1)[0]
            rule = self._store.get(rule_name)
            if rule is None:
                self._dedupe.cancel(key)
                continue
            event = self._dedupe.consume(key)
            if event is None:
                continue
            config = self._config_factory()
            denial = self._consent(config, rule)
            if denial:
                out.append(EventEvaluation(
                    rule, suppressed=True, censored=True, layer=LAYER_DEDUPE_WINDOW,
                    reason=f"the delayed run was cancelled: {denial}", event=event,
                ))
                continue
            out.append(self._dispatch_now(rule, event, moment, key))
        return out
