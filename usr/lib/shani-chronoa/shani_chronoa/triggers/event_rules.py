"""Event rules as data: what an armed event rule holds, how one is validated and built, and where they are stored."""

from __future__ import annotations


import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from shani_chronoa.skills import discover_skills

from .common import (  # noqa: F401
    BACKOFF_BASE_SECONDS,
    BACKOFF_CAP_SECONDS,
    BACKOFF_MAX_ATTEMPT,
    BACKOFF_MAX_CONSECUTIVE_FAILURES,
    BACKOFF_MAX_RULE_CONSECUTIVE_FAILURES,
    BACKOFF_RESTART_LIMIT,
    BACKOFF_RESTART_WINDOW_SECONDS,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DEBOUNCE_SECONDS,
    triggers_dir,
    EVENT_TYPES,
    Event,
    MATCH_ANY,
    MATCH_SUBSTRING,
    MAX_ARGUMENTS,
    MAX_CONTENT_CHARS,
    MAX_COOLDOWN_SECONDS,
    MAX_DEBOUNCE_SECONDS,
    MAX_KEYWORDS,
    MAX_RULE_NAME_CHARS,
    MIN_COOLDOWN_SECONDS,
    MIN_DEBOUNCE_SECONDS,
    RETRY_POLICIES,
    RETRY_RETRYABLE,
    RETRY_TERMINAL,
    _NOTIFY_ONLY_ACTUATORS,
    _NOTIFY_ONLY_EVENT_TYPES,
    _VALID_EVENT_MATCH_MODES,
    _actuator_problem,
    _clamped_seconds,
    _jsonable,
    _stored_arguments_problem,
)
from .sources import (  # noqa: F401
    EXPIRY_THRESHOLDS,
)
from .rules import (  # noqa: F401
    RuleStore,
    _validate_rule_fields,
)
def event_rules_file() -> Path:
    """The event-rule store, resolved per call (see common.triggers_dir)."""
    return triggers_dir() / "event_rules.json"


# --- layer 4: backoff, the policy behind the cooldown ----------------------

class BackoffPolicy:
    """Exponential `2^attempt` backoff with two independent caps.

    One cap alone is not enough, and this is the shape the failure needs:

    - `consecutive` is bounded by `max_consecutive_failures`, so a unit that
      fails five times in a row parks instead of retrying at 2^5, 2^6, ...
    - `restarts` is bounded by `restart_limit` *within a rolling window*, so a
      unit that fails once a minute is parked too. The consecutive cap cannot
      do that job: each failure is far enough apart to reset the counter, and
      the unit retries for as long as the machine is up.

    `delay_for` is the exponential ramp; the two caps are what stop it.
    """

    def __init__(
        self,
        base: float = BACKOFF_BASE_SECONDS,
        cap: float = BACKOFF_CAP_SECONDS,
        max_consecutive_failures: int = BACKOFF_MAX_CONSECUTIVE_FAILURES,
        restart_limit: int = BACKOFF_RESTART_LIMIT,
        restart_window: float = BACKOFF_RESTART_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base = float(base)
        self.cap = float(cap)
        self.max_consecutive_failures = int(max_consecutive_failures)
        self.restart_limit = int(restart_limit)
        self.restart_window = float(restart_window)
        self._clock = clock
        self.consecutive = 0
        self.restarts: list[float] = []

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before attempt `attempt + 1`."""
        if attempt <= 0:
            return 0.0
        return min(self.base * (2 ** (attempt - 1)), self.cap)

    def observe_failure(self) -> None:
        self.consecutive += 1
        self.restarts.append(self._clock())
        self._trim()

    def observe_success(self) -> None:
        """A run that worked resets the *consecutive* count only.

        The rolling restart list is deliberately not cleared: a unit that
        succeeds once a minute and crashes the rest of the time is a crash
        loop, and clearing the window on every success is precisely how a
        crash loop retries forever.
        """
        self.consecutive = 0
        self._trim()

    def _trim(self) -> None:
        horizon = self._clock() - self.restart_window
        self.restarts = [t for t in self.restarts if t >= horizon]

    def restart_allowance_left(self) -> int:
        self._trim()
        return max(0, self.restart_limit - len(self.restarts))

    def should_park(self) -> bool:
        """True when no further retry is allowed by either cap."""
        return (
            self.consecutive >= self.max_consecutive_failures
            or self.restart_allowance_left() <= 0
        )

    def to_dict(self) -> dict:
        return {
            "consecutive": self.consecutive,
            "restarts": list(self.restarts),
        }

    def load_dict(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        try:
            self.consecutive = int(raw.get("consecutive", 0))
            self.restarts = [float(t) for t in raw.get("restarts", [])]
        except (TypeError, ValueError):
            # A corrupt counter is a counter of zero, not a crash: refusing to
            # load the rule would let a truncated write disable a safety gate.
            self.consecutive = 0
            self.restarts = []


# --- event rules -----------------------------------------------------------

class EventRule:
    """One armed event rule: an event type, a source, a whitelisted actuator.

    Deliberately as narrow as `TriggerRule`, and narrow in the same places. No
    `prompt`, no `command`, no skill resolved at fire time. `params` carries
    only the *reader* configuration a type already declared, and it is
    restricted to JSON scalars - because it is persisted, and a live object (a
    watcher, a container runtime handle) in a persisted field is both
    unserialisable and a thing that would silently come back as a different
    value after a restart. Those are `seams`, which is not persisted at all.
    """

    __slots__ = (
        "name", "event_type", "source", "match_mode", "substring", "keywords",
        "actuator", "arguments", "params", "seams", "debounce_seconds",
        "cooldown_seconds", "retry_policy", "allow_destructive", "enabled",
        "created_at", "last_fired_at", "retry_at", "attempt", "parked",
        "parked_reason", "consecutive_failures", "restart_times",
        "fired_thresholds", "max_consecutive_failures", "ask_first",
    )

    def __init__(
        self,
        name: str,
        event_type: str,
        source: str,
        actuator: str,
        arguments: dict,
        match_mode: str = MATCH_ANY,
        substring: str = "",
        keywords: Optional[list[str]] = None,
        params: Optional[dict] = None,
        seams: Optional[dict] = None,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        retry_policy: str = RETRY_RETRYABLE,
        allow_destructive: bool = False,
        enabled: bool = True,
        created_at: Optional[float] = None,
        last_fired_at: Optional[float] = None,
        retry_at: Optional[float] = None,
        attempt: int = 0,
        parked: bool = False,
        parked_reason: str = "",
        consecutive_failures: int = 0,
        restart_times: Optional[list[float]] = None,
        fired_thresholds: Optional[list[str]] = None,
        max_consecutive_failures: int = BACKOFF_MAX_CONSECUTIVE_FAILURES,
        ask_first: bool = False,
    ) -> None:
        self.name = name
        self.event_type = event_type
        self.source = source
        self.match_mode = match_mode
        self.substring = substring
        self.keywords = list(keywords or [])
        self.actuator = actuator
        self.arguments = dict(arguments)
        self.params = {k: _jsonable(v) for k, v in dict(params or {}).items()}
        self.seams = dict(seams or {})
        self.debounce_seconds = float(debounce_seconds)
        self.cooldown_seconds = float(cooldown_seconds)
        self.retry_policy = retry_policy
        self.allow_destructive = bool(allow_destructive)
        self.enabled = enabled
        self.created_at = created_at if created_at is not None else time.time()
        self.last_fired_at = last_fired_at
        self.retry_at = retry_at
        self.attempt = int(attempt)
        self.parked = bool(parked)
        self.parked_reason = parked_reason
        self.consecutive_failures = int(consecutive_failures)
        self.restart_times = list(restart_times or [])
        self.fired_thresholds = list(fired_thresholds or [])
        self.max_consecutive_failures = int(max_consecutive_failures)
        self.ask_first = bool(ask_first)

    @property
    def sense(self) -> str:
        """The producing sense this rule is gated on.

        An event type is not a `Sense` and emits no percept, but the *consent*
        it needs is the same kind of question - "may I perceive this?" - and
        reusing `TriggerEngine._consent` unchanged is what stops the two
        engines drifting apart on the input-actuator guard. When `config.py`
        has no consent key for an event type, `sense_allowed` returns False and
        the rule is denied, which is the fail-closed direction.
        """
        return self.event_type

    def matches(self, event: Event) -> bool:
        """Whether this rule cares about this particular event.

        This is `filter_mismatch`, the second anti-noise layer, and it is the
        only one that can legitimately discard a real transition: the state
        changed, but this rule is about a different unit, file or branch. A
        rule whose filter rejects everything therefore never arms a timer, which
        is why the filter is evaluated before the debounce window rather than
        after it.
        """
        if self.event_type != event.kind:
            return False
        if self.match_mode == MATCH_ANY:
            return True
        if self.match_mode == MATCH_SUBSTRING:
            if not self.substring:
                return False
            return self.substring.lower() in event.summary.lower()
        return any(keyword.lower() in event.summary.lower() for keyword in self.keywords)

    def due(self, now: float) -> bool:
        """The cooldown layer, plus the retry clock a failure arms.

        Two clocks, deliberately separate. `last_fired_at` is the *cooldown* and
        is set only on success, which is the invariant this module already
        documented: a FAILED verdict starts no cooldown, so the rule is not
        silenced for a window having done nothing. `retry_at` is the *backoff*,
        set only on failure, and it is what spaces a retryable rule's attempts
        by `2^attempt`. Collapsing them would either silence failures for a
        whole cooldown (the documented bug) or retry them on every poll (a hot
        loop that never backs off), and the two requirements in the spec are
        only satisfiable with both.
        """
        if self.last_fired_at is not None and (
            (now - self.last_fired_at) < self.cooldown_seconds
        ):
            return False
        if self.retry_at is not None and now < self.retry_at:
            return False
        return True

    def backoff_delay(self) -> float:
        if self.retry_policy == RETRY_TERMINAL:
            return 0.0
        return self._policy().delay_for(self.attempt)

    def _policy(self) -> BackoffPolicy:
        # `restart_limit` tracks the budget rather than staying at the module
        # default: `should_park()` is an OR of the two caps, so a rule allowed
        # twelve failures still parks after five inside the rolling window if the
        # restart cap is left alone, and the budget is then a fiction.
        policy = BackoffPolicy(
            max_consecutive_failures=self.max_consecutive_failures,
            restart_limit=max(
                BACKOFF_RESTART_LIMIT, self.max_consecutive_failures
            ),
        )
        policy.consecutive = self.consecutive_failures
        policy.restarts = list(self.restart_times)
        return policy

    def exhausted(self) -> bool:
        return self._policy().should_park()

    def note_failure(self, now: float) -> None:
        policy = self._policy()
        policy.observe_failure()
        self.attempt += 1
        self.consecutive_failures = policy.consecutive
        self.restart_times = list(policy.restarts)
        self.retry_at = now + self.backoff_delay()

    def note_success(self, now: float) -> None:
        policy = self._policy()
        policy.observe_success()
        self.attempt = 0
        self.consecutive_failures = policy.consecutive
        self.restart_times = list(policy.restarts)
        self.retry_at = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "event_type": self.event_type, "source": self.source,
            "match_mode": self.match_mode, "substring": self.substring,
            "keywords": list(self.keywords), "actuator": self.actuator,
            "arguments": dict(self.arguments), "params": dict(self.params),
            "debounce_seconds": self.debounce_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "retry_policy": self.retry_policy,
            "allow_destructive": self.allow_destructive, "enabled": self.enabled,
            "created_at": self.created_at, "last_fired_at": self.last_fired_at,
            "retry_at": self.retry_at,
            "attempt": self.attempt, "parked": self.parked,
            "parked_reason": self.parked_reason,
            "consecutive_failures": self.consecutive_failures,
            "restart_times": list(self.restart_times),
            "fired_thresholds": list(self.fired_thresholds),
            "max_consecutive_failures": self.max_consecutive_failures,
            "ask_first": self.ask_first,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Optional[EventRule]":
        """Rebuild an event rule from a decoded record, or None if unusable.

        The rules file is JSON in the user's own data directory, so it is
        editable by hand and by anything running as that user. Every bound the
        arm-time validator enforces is therefore re-applied here, split the way
        `_clamped_seconds` and `_stored_arguments_problem` split them: numbers
        that govern cadence are clamped toward firing less, and anything that
        governs what the rule matches or what the actuator is told is refused.

        Before this, a hand-edited file yielded `cooldown_seconds=-1.0`,
        `debounce_seconds=-99`, `attempt=100000`, `consecutive_failures=-1`, a
        100k-character `substring` and 100 arguments - all accepted. A negative
        cooldown and a negative debounce are the permissive direction: the rule
        fires on every poll instead of waiting, and never parks.
        """
        if not isinstance(raw, dict):
            return None
        try:
            name = raw["name"]
            event_type = raw["event_type"]
            source = raw["source"]
            actuator = raw["actuator"]
            arguments = raw["arguments"]
        except (KeyError, TypeError):
            return None
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_RULE_NAME_CHARS:
            return None
        if event_type not in EVENT_TYPES:
            return None
        if not isinstance(source, str) or not source.strip():
            return None
        if not isinstance(actuator, str) or not actuator.strip():
            return None
        problem = _stored_arguments_problem(arguments)
        if problem is not None:
            return None
        match_mode = raw.get("match_mode", MATCH_ANY)
        if match_mode not in _VALID_EVENT_MATCH_MODES:
            return None
        substring = raw.get("substring", "")
        keywords = raw.get("keywords", [])
        params = raw.get("params", {})
        if not isinstance(substring, str):
            return None
        if len(substring) > MAX_CONTENT_CHARS:
            return None
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            return None
        if len(keywords) > MAX_KEYWORDS:
            return None
        if not isinstance(params, dict):
            return None
        if len(params) > MAX_ARGUMENTS:
            return None
        for key, value in params.items():
            if not isinstance(key, str) or not key.strip():
                return None
            if not isinstance(value, (str, int, float, bool, type(None))):
                return None
        retry_policy = raw.get("retry_policy", RETRY_RETRYABLE)
        if retry_policy not in RETRY_POLICIES:
            return None
        if not isinstance(raw.get("enabled", True), bool):
            return None
        if not isinstance(raw.get("allow_destructive", False), bool):
            return None
        if not isinstance(raw.get("ask_first", False), bool):
            return None
        debounce = _clamped_seconds(
            raw.get("debounce_seconds", DEFAULT_DEBOUNCE_SECONDS),
            MIN_DEBOUNCE_SECONDS, MAX_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS,
        )
        cooldown = _clamped_seconds(
            raw.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS),
            MIN_COOLDOWN_SECONDS, MAX_COOLDOWN_SECONDS, DEFAULT_COOLDOWN_SECONDS,
        )
        if debounce is None or cooldown is None:
            return None
        # `attempt` and `consecutive_failures` are the backoff counters, and both
        # bounds here are one-directional. `attempt` feeds `2 ** attempt`, so an
        # unbounded value is a `2 ** 100000` before the cap is applied; and a
        # negative `consecutive_failures` compares False against
        # `BACKOFF_MAX_CONSECUTIVE_FAILURES`, so `should_park()` is False and a
        # rule that is failing every time never reaches the cap.
        attempt = _clamped_seconds(raw.get("attempt", 0), 0, BACKOFF_MAX_ATTEMPT, 0)
        budget = _clamped_seconds(
            raw.get("max_consecutive_failures", BACKOFF_MAX_CONSECUTIVE_FAILURES),
            1, BACKOFF_MAX_RULE_CONSECUTIVE_FAILURES, BACKOFF_MAX_CONSECUTIVE_FAILURES,
        )
        # Clamped to *this rule's* budget, not the module default. Clamping to the
        # default meant a rule configured for twelve reloads claiming five, and
        # `should_park()` - which compares the counter against the budget - then
        # flips to False: a parked rule silently un-parks on restart and starts
        # driving a broken actuator again with nothing reporting the change.
        if attempt is None or budget is None:
            return None
        consecutive = _clamped_seconds(
            raw.get("consecutive_failures", 0), 0, budget, 0
        )
        if consecutive is None:
            return None
        restart_times = raw.get("restart_times", []) or []
        fired_thresholds = raw.get("fired_thresholds", []) or []
        if not isinstance(restart_times, list) or not isinstance(fired_thresholds, list):
            return None
        if len(restart_times) > max(BACKOFF_RESTART_LIMIT, int(budget)):
            return None
        if len(fired_thresholds) > len(EXPIRY_THRESHOLDS):
            return None
        try:
            return cls(
                name=name, event_type=event_type, source=source, actuator=actuator,
                arguments=arguments, match_mode=match_mode, substring=substring,
                keywords=keywords, params=params,
                debounce_seconds=debounce,
                cooldown_seconds=cooldown,
                retry_policy=retry_policy,
                allow_destructive=raw.get("allow_destructive", False),
                enabled=raw.get("enabled", True),
                created_at=raw.get("created_at"),
                last_fired_at=raw.get("last_fired_at"),
                retry_at=raw.get("retry_at"),
                attempt=int(attempt),
                parked=bool(raw.get("parked", False)),
                parked_reason=str(raw.get("parked_reason", "")),
                consecutive_failures=int(consecutive),
                restart_times=restart_times,
                fired_thresholds=fired_thresholds,
                max_consecutive_failures=int(budget),
                ask_first=raw.get("ask_first", False),
            )
        except (TypeError, ValueError):
            return None


def build_event_rule(
    *,
    name: str,
    event_type: str,
    source: str,
    actuator: str,
    arguments: dict,
    match_mode: str = MATCH_SUBSTRING,
    substring: str = "",
    keywords: Optional[list[str]] = None,
    params: Optional[dict] = None,
    seams: Optional[dict] = None,
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    retry_policy: str = RETRY_RETRYABLE,
    allow_destructive: bool = False,
    skills: Optional[Mapping[str, object]] = None,
    ask_first: bool = False,
) -> "tuple[Optional[EventRule], Optional[str]]":
    """Validate and build an event rule. Returns (rule, None) or (None, why).

    Three refusals here are load-bearing rather than defensive. A destructive
    actuator is refused at *arm* time unless the caller opted in, an actuator
    with no consent key of its own that can overwrite a file is refused with no
    opt-in at all, and an expiry rule may not name any actuator but `notify` -
    the last has no opt-in either, because "the machine noticed its credential
    is about to expire and renewed it while nobody was there" is the capability
    this module exists to not have.

    The first two come from `_actuator_problem`, shared with `build_rule`.
    Sharing is the fix, not tidiness: until 2026-09-30 this function carried
    its own copy of the destructive table and the percept path carried none,
    which is how `build_rule` came to accept all eight destructive actuators.
    """
    if skills is None:
        _tools, skills = discover_skills()
    if actuator not in skills:
        return None, f"actuator {actuator!r} is not a whitelisted skill"
    if event_type not in EVENT_TYPES:
        return None, f"event_type must be one of {sorted(EVENT_TYPES)}"
    if event_type in _NOTIFY_ONLY_EVENT_TYPES and actuator not in _NOTIFY_ONLY_ACTUATORS:
        return None, (
            f"a {event_type} rule may only notify ({sorted(_NOTIFY_ONLY_ACTUATORS)}); "
            f"{actuator!r} would let the machine act on its own deadline without a "
            "person present"
        )
    problem = _actuator_problem(actuator, allow_destructive, ask_first)
    if problem is not None:
        return None, problem
    problem = _validate_rule_fields(
        name, event_type, match_mode, substring, list(keywords or []),
        actuator, arguments, cooldown_seconds,
    )
    if problem is not None:
        return None, problem
    if match_mode not in _VALID_EVENT_MATCH_MODES:
        return None, f"match_mode must be one of {sorted(_VALID_EVENT_MATCH_MODES)}"
    if not isinstance(source, str) or not source.strip():
        return None, "source must be a non-empty string"
    params = dict(params or {})
    if not isinstance(params, dict):
        return None, "params must be a dict"
    if len(params) > MAX_ARGUMENTS:
        return None, f"at most {MAX_ARGUMENTS} params are allowed"
    for key, value in params.items():
        if not isinstance(key, str) or not key.strip():
            return None, "every param key must be a non-empty string"
        if not isinstance(value, (str, int, float, bool, type(None))):
            return None, (
                f"param {key!r} must be a JSON scalar, not {type(value).__name__}: "
                "params are persisted, so a live object belongs in 'seams' where "
                "it cannot be written out or come back stale"
            )
    if retry_policy not in RETRY_POLICIES:
        return None, f"retry_policy must be one of {sorted(RETRY_POLICIES)}"
    if isinstance(debounce_seconds, bool) or not isinstance(debounce_seconds, (int, float)):
        return None, "debounce_seconds must be a number"
    if not MIN_DEBOUNCE_SECONDS <= float(debounce_seconds) <= MAX_DEBOUNCE_SECONDS:
        return None, (
            f"debounce_seconds must be between {MIN_DEBOUNCE_SECONDS:g} and "
            f"{MAX_DEBOUNCE_SECONDS:g}"
        )
    rule = EventRule(
        name=name, event_type=event_type, source=source, actuator=actuator,
        arguments=arguments, match_mode=match_mode, substring=substring,
        keywords=list(keywords or []), params=params, seams=seams,
        debounce_seconds=float(debounce_seconds), cooldown_seconds=float(cooldown_seconds),
        retry_policy=retry_policy, allow_destructive=allow_destructive,
        ask_first=bool(ask_first),
    )
    return rule, None


class EventRuleStore(RuleStore):
    """`RuleStore` over `EventRule`, in a sibling file.

    A separate file rather than a new key in `rules.json` because `TriggerRule
    .from_dict` *refuses* an unreadable record rather than skipping it, and
    adding an event-only field to that record would make every existing rules
    file fail to load the moment the loader learned the new shape.
    """

    RULE_CLASS = EventRule

    @classmethod
    def default_path(cls) -> Path:
        return event_rules_file()
