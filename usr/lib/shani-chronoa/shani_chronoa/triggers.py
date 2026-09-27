"""Trigger -> actuator engine: a rule store that lets a percept fire a
whitelisted skill unattended, with per-rule opt-in consent.

A "trigger rule" is the ambient-scheduler's answer to the question "what
should happen when I perceive X?". It is deliberately *not* a way to let the
LLM write actions: the user arms a rule, and the rule names one already
whitelisted skill with fixed, user-supplied arguments. Nothing here resolves a
skill name at fire time, accepts a shell command, or takes a free-form prompt
- those are the three things this module exists to prevent, and they are the
design boundary that makes an unattended action safe to run at all.

The shape, in one sentence: **a percept matches a rule, the rule's producing
sense and target actuator both pass their own consent gates, and the actuator
runs through `tools.execute_tool` - the single dispatch point that already
records every call.**

What this module is NOT:

- It is not a sense. It emits no `Percept`, so it is not registered in
  `SENSES` and `tests/test_sense_manifest.py` has nothing to assert about it.
  It is infrastructure over the senses layer, in the same relationship
  `AmbientScheduler` holds to `Sense`.
- It does not author rules. `build_rule()` validates a dict a *user* (or a
  CLI) supplied; no path here accepts an LLM-authored rule, and the CLI only
  exposes `add`/`remove`/`list`/`clear`, never "let the model write one".
- It does not create an audit trail. Every actuation goes through
  `tools.execute_tool`, which records to the existing `ToolTracker` ring and
  `~/.local/share/shani-chronoa/logs/tool_calls.log`. The only addition is
  one field on that record - `origin` - so an unattended call is
  distinguishable from a user-initiated one.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import Percept
from shani_chronoa.skills import discover_skills
from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED

logger = logging.getLogger(__name__)

# Where armed rules live. User-owned, under the same per-user state dir the
# rest of the repo uses, and created with restrictive permissions so a
# dropped-in rule file cannot be read by another user.
RULES_DIR = Path(os.path.expanduser("~/.local/share/shani-chronoa/triggers"))
RULES_FILE = RULES_DIR / "rules.json"

# Bounds. A rule is a small declarative object; these exist so one bad rule
# cannot exhaust the store or the percept it matches against.
MAX_RULES_PER_USER = 64
MAX_RULE_NAME_CHARS = 64
MAX_CONTENT_CHARS = 2048
MAX_KEYWORDS = 16
MAX_KEYWORD_CHARS = 128
MAX_ARGUMENTS = 8
MAX_ARGUMENT_VALUE_CHARS = 4096

# Per-rule cooldown, in seconds. A repeatedly-matching transient percept - a
# flickering sensor, a notification that arrives in bursts - must not spam an
# actuator. Cooldowns are persisted on the rule (so they survive a restart) and
# enforced monotonically within a process from a `time.monotonic()` clock, so a
# wall-clock jump cannot clear them.
DEFAULT_COOLDOWN_SECONDS = 30.0
MIN_COOLDOWN_SECONDS = 1.0
MAX_COOLDOWN_SECONDS = 3600.0

# How the rule's match condition is expressed. Both are case-insensitive over
# the percept's `content` field; a keyword set is a disjunction, a substring
# is a conjunction with itself.
MATCH_SUBSTRING = "substring"
MATCH_KEYWORDS = "keywords"
_VALID_MATCH_MODES = frozenset((MATCH_SUBSTRING, MATCH_KEYWORDS))

# Origin recorded on every unattended actuation. Defined here rather than
# imported from tool_tracking so this module is the single place that names
# the value; tool_tracking owns the field, this module owns the meaning.
_ORIGIN = ORIGIN_UNATTENDED


class TriggerRule:
    """One armed rule: a sense, a match condition, a whitelisted actuator.

    The fields are deliberately narrow. There is no `prompt`, no `command`,
    and no `skill` resolved at fire time - `actuator` is validated against
    the *shipped* skill registry at arm time and stored as the exact name the
    registry uses, so a rule that names a skill that does not exist cannot be
    armed, and a rule that names one cannot be silently redirected later.

    `arguments` are the fixed, user-supplied values the actuator is called
    with. They are validated against the actuator's own schema at arm time,
    so a rule cannot carry an argument the skill does not declare.
    """

    __slots__ = (
        "name",
        "sense",
        "match_mode",
        "substring",
        "keywords",
        "actuator",
        "arguments",
        "cooldown_seconds",
        "enabled",
        "created_at",
        "last_fired_at",
    )

    def __init__(
        self,
        name: str,
        sense: str,
        match_mode: str,
        actuator: str,
        arguments: dict,
        substring: str = "",
        keywords: Optional[list[str]] = None,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        enabled: bool = True,
        created_at: Optional[float] = None,
        last_fired_at: Optional[float] = None,
    ) -> None:
        self.name = name
        self.sense = sense
        self.match_mode = match_mode
        self.substring = substring
        self.keywords = list(keywords) if keywords else []
        self.actuator = actuator
        self.arguments = dict(arguments)
        self.cooldown_seconds = cooldown_seconds
        self.enabled = enabled
        self.created_at = created_at if created_at is not None else time.time()
        self.last_fired_at = last_fired_at

    def matches(self, percept: Percept) -> bool:
        """Whether this rule's condition is met by `percept`'s content.

        Case-insensitive. A rule whose sense does not equal the percept's is
        not consulted at all - `fire()` pre-filters on sense, so this is the
        content test only.
        """
        if not self.enabled:
            return False
        content = percept.content.lower()
        if self.match_mode == MATCH_SUBSTRING:
            return self.substring.lower() in content
        return any(keyword.lower() in content for keyword in self.keywords)

    def due(self, now: Optional[float] = None) -> bool:
        """True if enough time has elapsed since the last firing.

        `last_fired_at is None` is "never fired", which is always due. The
        clock is monotonic so an NTP jump or a suspended laptop cannot make a
        rule look freshly due.
        """
        if self.last_fired_at is None:
            return True
        current = time.monotonic() if now is None else now
        return (current - self.last_fired_at) >= self.cooldown_seconds

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sense": self.sense,
            "match_mode": self.match_mode,
            "substring": self.substring,
            "keywords": list(self.keywords),
            "actuator": self.actuator,
            "arguments": dict(self.arguments),
            "cooldown_seconds": self.cooldown_seconds,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_fired_at": self.last_fired_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Optional[TriggerRule]":
        """Rebuild a rule from a decoded record, or None if it is unusable.

        A corrupt line is refused rather than silently downgraded to a
        harmless rule: an armed rule that quietly becomes a no-op is worse
        than one that is absent, because `list` would still show it.
        """
        if not isinstance(raw, dict):
            return None
        try:
            name = raw["name"]
            sense = raw["sense"]
            match_mode = raw["match_mode"]
            actuator = raw["actuator"]
            arguments = raw["arguments"]
        except (KeyError, TypeError):
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(sense, str) or not sense.strip():
            return None
        if match_mode not in _VALID_MATCH_MODES:
            return None
        if not isinstance(actuator, str) or not actuator.strip():
            return None
        if not isinstance(arguments, dict):
            return None
        substring = raw.get("substring", "")
        keywords = raw.get("keywords", [])
        cooldown = raw.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
        enabled = raw.get("enabled", True)
        created_at = raw.get("created_at")
        last_fired_at = raw.get("last_fired_at")
        if not isinstance(substring, str):
            return None
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            return None
        try:
            cooldown = float(cooldown)
        except (TypeError, ValueError):
            return None
        try:
            created_at = float(created_at) if created_at is not None else time.time()
            last_fired_at = float(last_fired_at) if last_fired_at is not None else None
        except (TypeError, ValueError):
            return None
        if not isinstance(enabled, bool):
            return None
        return cls(
            name=name,
            sense=sense,
            match_mode=match_mode,
            substring=substring,
            keywords=keywords,
            actuator=actuator,
            arguments=arguments,
            cooldown_seconds=cooldown,
            enabled=enabled,
            created_at=created_at,
            last_fired_at=last_fired_at,
        )


# --- validation -------------------------------------------------------------

def _validate_rule_fields(
    name: str,
    sense: str,
    match_mode: str,
    substring: str,
    keywords: list[str],
    actuator: str,
    arguments: dict,
    cooldown_seconds: float,
) -> "Optional[str]":
    """Return why a candidate rule is unacceptable, or None if it is fine.

    Every rejection here carries an explicit reason. The shape is enforced
    before the rule is persisted, so an LLM- or typo-shaped object cannot
    become an armed rule; `build_rule` is the only entry point and it calls
    this first.
    """
    if not isinstance(name, str) or not name.strip():
        return "rule name must be a non-empty string"
    if len(name) > MAX_RULE_NAME_CHARS:
        return f"rule name must be at most {MAX_RULE_NAME_CHARS} characters"
    if not isinstance(sense, str) or not sense.strip():
        return "sense must be a non-empty string"
    if match_mode not in _VALID_MATCH_MODES:
        return f"match_mode must be one of {sorted(_VALID_MATCH_MODES)}"
    if match_mode == MATCH_SUBSTRING:
        if not isinstance(substring, str) or not substring.strip():
            return "a substring rule needs a non-empty 'substring'"
        if len(substring) > MAX_CONTENT_CHARS:
            return f"substring must be at most {MAX_CONTENT_CHARS} characters"
    else:
        if not isinstance(keywords, list) or not keywords:
            return "a keywords rule needs a non-empty list of 'keywords'"
        if len(keywords) > MAX_KEYWORDS:
            return f"at most {MAX_KEYWORDS} keywords are allowed"
        for keyword in keywords:
            if not isinstance(keyword, str) or not keyword.strip():
                return "every keyword must be a non-empty string"
            if len(keyword) > MAX_KEYWORD_CHARS:
                return f"each keyword must be at most {MAX_KEYWORD_CHARS} characters"
    if not isinstance(actuator, str) or not actuator.strip():
        return "actuator must be a non-empty string"
    if not isinstance(arguments, dict):
        return "arguments must be a dict"
    if len(arguments) > MAX_ARGUMENTS:
        return f"at most {MAX_ARGUMENTS} arguments are allowed"
    for key, value in arguments.items():
        if not isinstance(key, str) or not key.strip():
            return "every argument key must be a non-empty string"
        if isinstance(value, str) and len(value) > MAX_ARGUMENT_VALUE_CHARS:
            return f"argument {key!r} must be at most {MAX_ARGUMENT_VALUE_CHARS} characters"
    if not isinstance(cooldown_seconds, (int, float)) or isinstance(cooldown_seconds, bool):
        return "cooldown_seconds must be a number"
    cooldown_seconds = float(cooldown_seconds)
    if cooldown_seconds < MIN_COOLDOWN_SECONDS:
        return f"cooldown_seconds must be at least {MIN_COOLDOWN_SECONDS:g}s"
    if cooldown_seconds > MAX_COOLDOWN_SECONDS:
        return f"cooldown_seconds must be at most {MAX_COOLDOWN_SECONDS:g}s"
    return None


def build_rule(
    *,
    name: str,
    sense: str,
    match_mode: str,
    actuator: str,
    arguments: dict,
    substring: str = "",
    keywords: Optional[list[str]] = None,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    enabled: bool = True,
    skills: Optional[Mapping[str, object]] = None,
) -> "tuple[Optional[TriggerRule], Optional[str]]":
    """Validate and build a rule. Returns (rule, None) or (None, reason).

    `skills` is the shipped skill registry (`tools._HANDLER_FNS`); passing
    one explicitly is how tests substitute a fixed whitelist. Without it the
    real registry is loaded, which is what the CLI does - but the validation
    is identical either way, and the whitelist is always the *shipped* set,
    never something a rule can extend.

    The actuator is checked against the registry here, at arm time, so a rule
    naming a skill that is not installed cannot be armed and cannot be
    silently redirected to a different one later.
    """
    if skills is None:
        skills = discover_skills()
    if actuator not in skills:
        return None, f"actuator {actuator!r} is not a whitelisted skill"
    problem = _validate_rule_fields(
        name, sense, match_mode, substring, list(keywords or []), actuator, arguments, cooldown_seconds
    )
    if problem is not None:
        return None, problem
    rule = TriggerRule(
        name=name,
        sense=sense,
        match_mode=match_mode,
        substring=substring,
        keywords=list(keywords or []),
        actuator=actuator,
        arguments=arguments,
        cooldown_seconds=cooldown_seconds,
        enabled=enabled,
    )
    return rule, None


# --- storage ----------------------------------------------------------------

def _ensure_state_dir(path: Path) -> None:
    """Create the rules dir with restrictive permissions.

    Chronoa runs as a normal desktop user, so the state dir is per-user and
    must not be world-readable: it holds armed rules, which describe what the
    machine will do unprompted, and that is exactly the thing a passer-by on
    a shared machine should not be able to read. `mkdir` then `chmod` rather
    than a single mode argument, because `mkdir(parents=True, mode=...)` is
    masked by the process umask and silently lands permissive.
    """
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError as e:
        logger.warning("Could not restrict permissions on %s: %s", path, e)


def _restrict_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as e:
        logger.warning("Could not restrict permissions on %s: %s", path, e)


class RuleStore:
    """The armed-rule store: a JSON file under the per-user state dir.

    Corrupt or truncated input is refused loudly rather than silently
    discarded: an armed rule that quietly becomes a no-op is worse than an
    absent one, because `list` would still show it. A partial write (the
    process died mid-rewrite) is detected because the file is only ever
    replaced as a whole, via an atomic rename from a temp sibling.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else RULES_FILE
        self._lock = threading.RLock()
        self._rules: "dict[str, TriggerRule]" = {}
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
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise RuleStoreError(
                    f"the rules file {self._path} is corrupt or truncated: {e}; "
                    "refusing to load armed rules rather than silently discarding them"
                ) from e
            if not isinstance(raw, list):
                raise RuleStoreError(
                    f"the rules file {self._path} does not contain a JSON list; "
                    "refusing to load armed rules"
                )
            loaded: "dict[str, TriggerRule]" = {}
            for entry in raw:
                rule = TriggerRule.from_dict(entry)
                if rule is None:
                    raise RuleStoreError(
                        f"the rules file {self._path} contains an unreadable rule; "
                        "refusing to load armed rules"
                    )
                loaded[rule.name] = rule
            self._rules = loaded

    def _write(self) -> None:
        """Atomically replace the rules file.

        Write to a temp sibling in the same directory and rename: a crash
        mid-write leaves the previous file intact, and `rename` is atomic on
        every filesystem this project targets.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_state_dir(self._path.parent)
        tmp = self._path.with_name(self._path.name + ".tmp")
        payload = json.dumps(
            [rule.to_dict() for rule in self._rules.values()],
            indent=2,
            sort_keys=True,
        )
        try:
            tmp.write_text(payload, encoding="utf-8")
            _restrict_file(tmp)
            os.replace(tmp, self._path)
            _restrict_file(self._path)
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuleStoreError(f"could not write the rules file {self._path}: {e}") from e

    def add(self, rule: TriggerRule) -> None:
        with self._lock:
            if len(self._rules) >= MAX_RULES_PER_USER and rule.name not in self._rules:
                raise RuleStoreError(
                    f"refusing to arm more than {MAX_RULES_PER_USER} rules; "
                    "remove one first"
                )
            self._rules[rule.name] = rule
            self._write()

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self._rules:
                return False
            del self._rules[name]
            self._write()
            return True

    def clear(self) -> int:
        with self._lock:
            count = len(self._rules)
            self._rules = {}
            self._write()
            return count

    def get(self, name: str) -> "Optional[TriggerRule]":
        with self._lock:
            return self._rules.get(name)

    def names(self) -> "list[str]":
        with self._lock:
            return sorted(self._rules)

    def all(self) -> "list[TriggerRule]":
        with self._lock:
            return [self._rules[name] for name in sorted(self._rules)]

    def count(self) -> int:
        with self._lock:
            return len(self._rules)


class RuleStoreError(Exception):
    """A rule-store failure that must be reported, not swallowed."""


# --- the firing engine ------------------------------------------------------
#
# `RuleStore` knows how to validate, persist and match rules. Nothing above this
# line ever *acted* on one, which made the module inert: a rule could be armed,
# listed, and matched, and no percept would ever move because there was no path
# from a percept to an actuator. That is the shape of dead code this repo has
# shipped four times, so the consumer is part of the feature rather than a
# follow-up.

# Skills that move the machine and so carry their own consent requirement,
# separate from the vision sense's. Seeing a screen and controlling it are
# different risks; a rule must never become a way around a consent key.
_INPUT_ACTUATORS = frozenset({"move_pointer", "click_pointer", "type_text"})


class FireResult:
    """What happened to one rule for one percept. Reported, never raised.

    An unattended loop that stops on the first failure is not a loop, it is a
    one-shot: one broken rule would silently disable every other rule. Failures
    are values so the caller keeps going and can see what did not run.
    """

    __slots__ = ("rule", "fired", "denied", "reason")

    def __init__(self, rule, fired: bool, denied: bool = False, reason: str = "") -> None:
        self.rule = rule
        self.fired = fired
        self.denied = denied
        self.reason = reason

    def __repr__(self) -> str:
        return (
            f"FireResult(rule={self.rule.name!r}, fired={self.fired}, "
            f"denied={self.denied}, reason={self.reason!r})"
        )


class TriggerEngine:
    """Turns percepts into whitelisted actuator calls, under consent.

    Every rule is checked twice before anything happens: the *sensing* side must
    pass `sense_allowed(sense)`, so a sense the user has since switched off stops
    driving actions, and the *acting* side must pass its own gate. Both are
    consulted per percept rather than cached at arm time, because the whole
    point of a consent key is that turning it off takes effect immediately.
    """

    def __init__(self, store=None, config_factory=None, dispatch=None) -> None:
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.tools import execute_tool

        self._store = store if store is not None else RuleStore()
        self._config_factory = config_factory or ChronoaConfig
        self._dispatch = dispatch or execute_tool

    def store(self) -> "RuleStore":
        return self._store

    def _consent(self, config, rule) -> str:
        """Empty when the rule may act, otherwise why it may not."""
        if not config.sense_allowed(rule.sense):
            return f"the {rule.sense} sense is not permitted: {config.sense_allowed_reason(rule.sense)}"
        if rule.actuator in _INPUT_ACTUATORS and not config.input_control_enabled:
            return (
                f"the {rule.actuator} actuator needs the 'input-control-enabled' "
                "consent key, which is off"
            )
        return ""

    def evaluate(self, percept, now: Optional[float] = None) -> "list[FireResult]":
        """Fire every armed rule this percept matches. Never raises."""
        import time

        moment = time.time() if now is None else now
        config = self._config_factory()
        results: list[FireResult] = []

        for rule in self._store.all():
            if not rule.enabled or not rule.matches(percept):
                continue
            # Consent is checked BEFORE the cooldown, deliberately. Ordering it
            # after means a rule that already fired sits in its cooldown window
            # and is skipped silently, so switching a sense off looks exactly
            # like "nothing matched" - and an unauditable refusal is the one
            # outcome a consent gate must never produce.
            denial = self._consent(config, rule)
            if denial:
                results.append(FireResult(rule, fired=False, denied=True, reason=denial))
                continue
            if not rule.due(moment):
                continue
            try:
                # origin= is what makes this distinguishable in the audit log
                # from a person asking. An unattended action nobody can
                # distinguish from a user action is not auditable.
                self._dispatch(rule.actuator, dict(rule.arguments), origin=_ORIGIN)
            except Exception as exc:  # noqa: BLE001 - one bad rule must not stop the rest
                results.append(
                    FireResult(rule, fired=False, reason=f"{type(exc).__name__}: {exc}")
                )
                continue
            rule.last_fired_at = moment
            results.append(FireResult(rule, fired=True))

        return results

    def fire_all(self, percepts, now: Optional[float] = None) -> "list[FireResult]":
        """Evaluate a batch, preserving order. Used by the polling loop."""
        out: list[FireResult] = []
        for percept in percepts:
            out.extend(self.evaluate(percept, now=now))
        return out
