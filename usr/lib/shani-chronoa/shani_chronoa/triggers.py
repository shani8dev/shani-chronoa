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

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional

from shani_chronoa import files, verification
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import Percept
from shani_chronoa.skills import discover_skills
from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED

logger = logging.getLogger(__name__)

def _data_home() -> Path:
    """Delegates to `files.data_home`; kept as a name because it is called at import.

    Armed rules are unattended capability, and this module reads the answer at
    *import* time as a module-level constant. `tests/conftest.py` documents the
    consequence: a per-test `monkeypatch.setenv("HOME", tmp_path)` "lands too
    late", and the suite works around it by rebinding `RULES_FILE`. A variable
    read *before* import by every caller that isolates is the one that actually
    reaches here, so it is honoured - and a relative value counts as unset, per
    the XDG spec, so `XDG_DATA_HOME=relative/path` cannot resolve outside the
    data directory.

    This was the third copy of that rule in the package, alongside
    `egress.py`'s and `files.py`'s. One implementation means a change to the
    fallback cannot leave two of them disagreeing.
    """
    return files.data_home()


# Where armed rules live. User-owned, under the same per-user data dir the rest
# of the repo uses, and created with restrictive permissions so a dropped-in
# rule file cannot be read by another user.
RULES_DIR = _data_home() / "shani-chronoa" / "triggers"
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

# Event rules get one mode the percept rules deliberately do not have. A
# substring rule that matched the empty string would match *every* percept,
# which is why `MATCH_SUBSTRING` requires a non-empty needle there. An event
# rule has no such hazard: its `source` already scopes the subject to one
# repository, directory, unit or deadline, so "this type, from this source,
# unfiltered" is a real and common rule rather than a catch-all. Kept in its
# own frozenset rather than added to `_VALID_MATCH_MODES`, for the reason
# `senses.is_valid_schema` is a separate function: a percept rule must not be
# able to be armed with a mode that means "everything".
MATCH_ANY = "any"
_VALID_EVENT_MATCH_MODES = frozenset((MATCH_SUBSTRING, MATCH_KEYWORDS, MATCH_ANY))


# Origin recorded on every unattended actuation. Defined here rather than
# imported from tool_tracking so this module is the single place that names
# the value; tool_tracking owns the field, this module owns the meaning.
_ORIGIN = ORIGIN_UNATTENDED


# --- consent surfaces BOTH arm paths share ---------------------------------
#
# These live above `TriggerRule` rather than in the event section because as of
# 2026-09-30 both engines consult them. Keeping one set and one message is the
# whole point: the module's own rule is that "a second actuation path with its
# own idea of consent would be the one thing that could actually make an
# unattended action unsafe", and a deny-list only one path reads is exactly how
# the percept path came to accept a destructive actuator for its whole life.
#
# `_DESTRUCTIVE_ACTUATORS` was the event path's table until then. Measured
# asymmetry at the time of the fix: every name in it was ACCEPTED by
# `build_rule` and REFUSED by `build_event_rule` - `delete_file`,
# `kill_process`, `control_service`, `empty_trash`, `trash_file`,
# `manage_mount`, `lock_screen`. Killing a container or a service is not
# reversible by the thing that did it: a half-stopped service and a killed
# build are both states the user then has to notice. It is now refused by both
# paths, and refused again at dispatch, so an arm-time opt-in the dispatch path
# does not agree with is not a way through.
_DESTRUCTIVE_ACTUATORS = frozenset({
    "kill_process", "control_service", "manage_mount", "lock_screen",
    "delete_file", "empty_trash", "trash_file", "power_profile",
})

# One actuator is refused on both paths with **no** opt-in at all, and the
# reason is specific rather than a matter of taste.
#
# `write_text_file` is the only skill in the shipped registry that can replace
# the contents of a file the user wrote (`create_directory`,
# `move_or_copy_file` and `extract_archive` all refuse an existing destination
# unless explicitly asked to overwrite) AND declares no consent key of its own.
# Measured against the live `tools.TOOLS` schema, `capabilities.gated_by`
# resolves a key for 32 of the 80 shipped skills; `write_text_file` is one of
# the 48 that resolve to `None`, and its own description names no key either.
#
# That combination is what makes it worse than the destructive set rather than
# equal to it. Every other destructive actuator is still checked twice more -
# once by `capabilities.gated_by`, and once by the skill itself, which refuses
# on its own consent key when it runs (`delete_file` reads
# `file-delete-enabled`, `kill_process` `process-kill-enabled`, `trash_file`
# `file-delete-enabled`, `empty_trash` `trash-empty-enabled`, `lock_screen`
# `screen-lock-enabled`). `write_text_file` has neither gate, so one grant to
# arm a single rule would buy recurring unattended overwrites with no second
# line of defence.
#
# There is deliberately no opt-in here, and the reason is that the honest
# escape hatch does not live in this module: a consent key would, and that means
# `capabilities.GATED` plus the gschema. Until one exists, the only defensible
# answer inside `triggers.py` is that an unattended rule may not name it.
_UNATTENDED_WRITE_ACTUATORS = frozenset({"write_text_file"})


def _actuator_problem(actuator: str, allow_destructive: bool) -> "Optional[str]":
    """Why this actuator may not be driven unattended, or None if it may.

    Shared by both arm-time validators and both dispatch-time consent checks,
    so the refusal text cannot drift between them. Every rejection names the
    thing that would have to change, because a refusal a user cannot act on is
    indistinguishable from a bug.
    """
    if actuator in _UNATTENDED_WRITE_ACTUATORS:
        return (
            f"{actuator!r} may not be driven unattended by a trigger rule: it is "
            "the one whitelisted skill that can replace the contents of a file "
            "you wrote and it declares no consent key of its own, so there is "
            "no switch to re-check at fire time and the skill does not refuse "
            "unattended either. Arming it would turn one grant into recurring "
            "unattended overwrites. Call it yourself, or ask for a consent key "
            "for it"
        )
    if actuator in _DESTRUCTIVE_ACTUATORS and not allow_destructive:
        return (
            f"{actuator!r} is destructive and cannot be reached by an "
            "unattended rule unless it is armed with allow_destructive=True"
        )
    return None


def _clamped_seconds(value: Any, low: float, high: float, default: float) -> "Optional[float]":
    """`value` as a float inside `[low, high]`, or None if it is not a number.

    NaN needs its own case rather than the `min`/`max` pair below: every
    comparison against it is False, so `min(max(nan, low), high)` returns nan
    and every bound in this module would pass it. A nan cooldown makes
    `TriggerRule.due()` return False forever - fail-closed, but a rule that can
    never fire while `list` says it is armed - and `json.dumps` writes it as
    the token `NaN`, which is not JSON, so the next save produces a file that
    refuses to load at all. Infinity and -Infinity fall out of the clamp on
    their own.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return default
    return min(max(number, low), high)


def _stored_arguments_problem(arguments: Any) -> "Optional[str]":
    """Why these persisted `arguments` are unusable, or None if they are fine.

    Refused, not trimmed - and the asymmetry with `_clamped_seconds` is the
    point, not an inconsistency. Clamping a cadence can only ever make a rule
    fire *less*, so repairing one is a defensive default. Trimming an argument
    list, or shortening a string, changes **what the actuator is told to do**,
    and a rule that quietly means something other than what was written is the
    same failure this module refuses everywhere else: a corrupt record repaired
    into a *different* armed rule is worse than an absent one, because `list`
    would still show it.
    """
    if not isinstance(arguments, dict):
        return "arguments must be a dict"
    if len(arguments) > MAX_ARGUMENTS:
        return f"at most {MAX_ARGUMENTS} arguments are allowed"
    for key, value in arguments.items():
        if not isinstance(key, str) or not key.strip():
            return "every argument key must be a non-empty string"
        if isinstance(value, str) and len(value) > MAX_ARGUMENT_VALUE_CHARS:
            return f"argument {key!r} must be at most {MAX_ARGUMENT_VALUE_CHARS} characters"
    return None


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

    `allow_destructive` mirrors `EventRule`'s field of the same name, for the
    same reason. It is a Python argument only: `skills/manage_triggers.py`
    never passes it and its schema does not declare it, so a model cannot arm a
    destructive unattended action by adding one argument. It is also re-checked
    in `TriggerEngine._consent`, so a rules file that sets it without the arming
    path having agreed is refused at fire time rather than obeyed.
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
        "allow_destructive",
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
        allow_destructive: bool = False,
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
        self.allow_destructive = bool(allow_destructive)
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
            "allow_destructive": self.allow_destructive,
            "created_at": self.created_at,
            "last_fired_at": self.last_fired_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Optional[TriggerRule]":
        """Rebuild a rule from a decoded record, or None if it is unusable.

        A corrupt line is refused rather than silently downgraded to a
        harmless rule: an armed rule that quietly becomes a no-op is worse
        than one that is absent, because `list` would still show it.

        The numeric fields are *clamped* rather than refused, and always in the
        restrictive direction - see `_load_bounds` for why those two answers
        differ and where the boundary is.
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
        problem = _stored_arguments_problem(arguments)
        if problem is not None:
            return None
        substring = raw.get("substring", "")
        keywords = raw.get("keywords", [])
        enabled = raw.get("enabled", True)
        allow_destructive = raw.get("allow_destructive", False)
        created_at = raw.get("created_at")
        last_fired_at = raw.get("last_fired_at")
        if not isinstance(substring, str):
            return None
        if len(substring) > MAX_CONTENT_CHARS:
            return None
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            return None
        if len(keywords) > MAX_KEYWORDS:
            return None
        cooldown = _clamped_seconds(
            raw.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS),
            MIN_COOLDOWN_SECONDS, MAX_COOLDOWN_SECONDS, DEFAULT_COOLDOWN_SECONDS,
        )
        if cooldown is None:
            return None
        try:
            created_at = float(created_at) if created_at is not None else time.time()
            last_fired_at = float(last_fired_at) if last_fired_at is not None else None
        except (TypeError, ValueError):
            return None
        if not isinstance(enabled, bool) or not isinstance(allow_destructive, bool):
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
            allow_destructive=allow_destructive,
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
    if match_mode not in _VALID_MATCH_MODES and match_mode != MATCH_ANY:
        return f"match_mode must be one of {sorted(_VALID_MATCH_MODES)}"
    if match_mode == MATCH_ANY:
        # `pass`, not `return None`, and that is the whole of the fix. Every
        # bound below this branch - the cooldown, the argument count, the
        # argument value length - is about *cadence and size*, not about what
        # the rule matches, so returning early skipped all of them for exactly
        # the mode `manage_triggers` hands out by default for an event rule.
        # Measured before the fix, all of these were accepted with
        # `match_mode=any`: `cooldown_seconds` of 0.0, -9999.0 and 1000000000;
        # 100 arguments against a limit of 8; and a 100k-character argument
        # value against a limit of 4096. The same cooldown was refused with a
        # clear message in `substring` mode, so the bound was live and simply
        # unreachable from the default arming path.
        #
        # Note this branch is reachable from `build_rule` too, not only from
        # `build_event_rule` as an earlier comment here claimed: this function
        # accepts `MATCH_ANY` itself, and `build_rule` does not narrow it. Such
        # a rule carries no substring and no keywords, so `TriggerRule.matches`
        # finds nothing to match and it can never fire - inert rather than
        # exploitable - which is why narrowing `build_rule` was left alone and
        # the bounds fixed instead.
        pass
    elif match_mode == MATCH_SUBSTRING:
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
    allow_destructive: bool = False,
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

    A destructive actuator is refused here too, which it was not until
    2026-09-30: `build_event_rule` had refused all eight of them and this
    accepted all eight, so the percept path - the half
    `skills/manage_triggers.py` makes reachable from a spoken instruction -
    was the permissive one. `allow_destructive` is the event path's opt-in,
    reused verbatim; see `_actuator_problem` for why `write_text_file` has no
    opt-in at all.
    """
    if skills is None:
        # `discover_skills()` returns `(TOOLS, handlers)`; the name-keyed
        # mapping is the second element. Assigning the tuple here and testing
        # `actuator not in skills` compares against the two container objects,
        # never a skill name, so every default-path call rejected every
        # actuator. The unit suite passed because it substitutes `skills=`
        # explicitly - so the broken default was invisible until something
        # outside the tests called it.
        _tools, skills = discover_skills()
    if actuator not in skills:
        return None, f"actuator {actuator!r} is not a whitelisted skill"
    problem = _actuator_problem(actuator, allow_destructive)
    if problem is not None:
        return None, problem
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
        allow_destructive=allow_destructive,
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

    The body now delegates to `files.ensure_private_dir`. This was the first
    copy of it in the tree; `egress.py` and then four other state-writing
    surfaces each grew their own, and the reason is easier to state than to
    remember: umask masking is invisible until you measure it at 002.
    """
    files.ensure_private_dir(path)


def _restrict_file(path: Path) -> None:
    files.restrict_file(path)


class RuleStore:
    """The armed-rule store: a JSON file under the per-user state dir.

    Corrupt or truncated input is refused loudly rather than silently
    discarded: an armed rule that quietly becomes a no-op is worse than an
    absent one, because `list` would still show it. A partial write (the
    process died mid-rewrite) is detected because the file is only ever
    replaced as a whole, via an atomic rename from a temp sibling.

    `RULE_CLASS` and `default_path()` let the event-rule store below be this
    exact store over a different rule type, and a second implementation of
    "validate, persist, replace atomically, refuse corrupt input" is a second
    set of bugs.

    `default_path()` is a method rather than a class attribute on purpose. The
    path is a module-level constant, and rebinding it at runtime is a supported
    seam - `tests/conftest.py` redirects `RULES_FILE` at a tmp directory on
    every test so the suite cannot write armed rules into the real home, and
    `test_skill_workbench.py` rebinds it directly. A class attribute captures
    the value at import time, the rebind becomes a silent no-op, and the tests
    go on writing to the user's real `~/.local/share` - a green suite over
    contaminated state, with nothing anywhere reporting an error.
    """

    RULE_CLASS = TriggerRule

    @classmethod
    def default_path(cls) -> Path:
        return RULES_FILE

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else self.default_path()
        self._lock = threading.RLock()
        self._rules: "dict[str, Any]" = {}
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
            loaded: "dict[str, Any]" = {}
            for entry in raw:
                rule = self.RULE_CLASS.from_dict(entry)
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


# ============================================================================
# EVENT TRIGGERS
# ============================================================================
#
# Everything above this line is *percept*-triggered: a sense emits, the content
# is matched, an actuator runs. That model has exactly one kind of question -
# "does this percept contain the string?" - and the six event types below do
# not fit it. None of them is a percept. Each is a question about a *state* the
# machine is in, and each is only interesting when that state **transitions**:
#
#   git        did HEAD / branch / dirty-set change?
#   fswatch    did something under this directory change?
#   failure    did a command's exit verdict change?
#   expiry     did a stored deadline cross a threshold?
#   containerrun did a supervised container exit or stall?
#   unithealth did a unit's health state transition?
#
# Counting events is the wrong instrument for all six. A commit that touches
# 400 files is ONE transition, not 400; a container that emits a progress line
# every 200ms is one stalled run, not 8000; a deadline that has passed has
# passed, and re-reading the clock must not re-notify. So every type here is
# reduced to a **stable fingerprint** and the event is the *change of that
# fingerprint*, not the observation. That is the same move `senses/latch.py`
# makes, and it is why a latch is not enough here: a latch is per-process and
# in-memory (its own docstring says so), so a re-arming trigger on an
# in-memory latch notifies once per reboot, forever. `DurableFingerprints`
# below is the durable half.
#
# The other thing these six share, and the reason they live in this module
# rather than in a new one: they all end in the *same* engine, with the same
# consent gates, the same `_INPUT_ACTUATORS` guard, the same `origin` on the
# audit record, and the same "a FAILED verdict starts no cooldown" rule. A
# second actuation path with its own idea of consent would be the one thing
# that could actually make an unattended action unsafe.
#
# The rule that shaped every signal reader below, taken from `AGENTS.md`'s
# senses section: **a sense whose failure mode is a plausible-looking wrong
# answer is worse than a sense that fails.** A missing `git`, a dead watcher, a
# malformed verdict file and a non-systemd machine all produce
# `SIGNAL_UNAVAILABLE` - never "nothing changed", and never a firing. Four of
# the six event types are otherwise indistinguishable from a clean machine.

# --- the six types ----------------------------------------------------------

EVENT_GIT = "git"
EVENT_FSWATCH = "fswatch"
EVENT_FAILURE = "failure"
EVENT_EXPIRY = "expiry"
EVENT_CONTAINERRUN = "containerrun"
EVENT_UNITHEALTH = "unithealth"

EVENT_TYPES = frozenset(
    (EVENT_GIT, EVENT_FSWATCH, EVENT_FAILURE, EVENT_EXPIRY,
     EVENT_CONTAINERRUN, EVENT_UNITHEALTH)
)

# --- how a signal came back ------------------------------------------------
#
# `ok` and `unavailable` are not a detail: they are the whole difference
# between "nothing happened" and "I could not find out", and the first two
# event types above already have a bug of that shape in their spec (a non-zero
# `git rev-parse` is not a deleted repository, and a dead watcher is not a
# quiet directory). A third state, `watch_error`, exists because a watcher
# that died mid-run must be reported as an error rather than folded into
# `unavailable` and then into "no change".

SIGNAL_OK = "ok"
SIGNAL_UNAVAILABLE = "unavailable"
SIGNAL_WATCH_ERROR = "watch_error"

# --- the four anti-noise layers --------------------------------------------
#
# These are four *different* failures and are named as the module names them,
# because collapsing any two of them produces a different bug:
#
#   duplicate_event  the fingerprint is unchanged, so this observation is not
#                    news. Answered here, from a durable fingerprint, BEFORE
#                    anything is scheduled.
#   filter_mismatch  the state really did change, but this rule does not care
#                    about this particular change (a different unit, a different
#                    file, a different branch). Answered after the fingerprint
#                    advances for the rule and before the debounce window, so
#                    a rule that filters everything out never arms a timer.
#   dedupe_window    debounce: a burst collapses into ONE *delayed* run, by
#                    pushing `scheduled_for = max(existing, now + debounce)`.
#                    Delay, never drop - dropping an event is how a build
#                    failure gets lost while the log looks healthy.
#   cooldown         suppress after a run, so a persistent condition does not
#                    become a per-window alarm storm. The existing per-rule
#                    `due()` is this layer, and it is where exponential backoff
#                    and parking live.
#
# `latch` in the senses covers only the first. `TriggerRule.due()` covers only
# the fourth. Neither can do the third, and a debounce that *drops* instead of
# *delaying* is the failure mode `dedupe_window` is named for.

LAYER_DUPLICATE_EVENT = "duplicate_event"
LAYER_FILTER_MISMATCH = "filter_mismatch"
LAYER_DEDUPE_WINDOW = "dedupe_window"
LAYER_COOLDOWN = "cooldown"

ANTI_NOISE_LAYERS = (
    LAYER_DUPLICATE_EVENT, LAYER_FILTER_MISMATCH, LAYER_DEDUPE_WINDOW, LAYER_COOLDOWN
)

# --- retry policy ----------------------------------------------------------
#
# An explicit enum on the rule, never inferred from the event text. The two
# cases it separates are not variants of one policy:
#
#   RETRYABLE  a flake. A unit test that fails twice in a row is a retry with
#              exponential backoff, and the FAILED verdict correctly starts no
#              cooldown so the retry happens.
#   TERMINAL   a machine in the wrong state. A failed deploy is not going to
#              pass on attempt three; retrying it unattended is how a
#              half-applied change becomes a worse one. Park and surface.
#
# Inferring this from, say, the presence of the word "failed" in the event is
# exactly the boolean-collapse that `unithealth`'s own state enum exists to
# avoid: one flag that means "not working" cannot distinguish "will probably
# work if we wait" from "a person has to look at this".

RETRY_RETRYABLE = "retryable"
RETRY_TERMINAL = "terminal"
RETRY_POLICIES = frozenset((RETRY_RETRYABLE, RETRY_TERMINAL))

# --- unit health states ----------------------------------------------------
#
# Four states, not a boolean. `backing_off` and `gave_up` are the whole reason
# this enum exists: systemd will happily tell a crash-looping unit "not
# active" forever, and a `failed: bool` reads that identically to a unit that
# was never started - so the trigger retries a dead unit indefinitely while
# never distinguishing it from a transient one. `gave_up` is terminal and
# parks; `backing_off` is retryable and is where the delay is read from.

UNIT_STARTING = "starting"
UNIT_WORKING = "working"
UNIT_BACKING_OFF = "backing_off"
UNIT_GAVE_UP = "gave_up"
UNIT_STOPPED = "stopped"
UNIT_HEALTH_STATES = frozenset(
    (UNIT_STARTING, UNIT_WORKING, UNIT_BACKING_OFF, UNIT_GAVE_UP, UNIT_STOPPED)
)

# Terminal by construction, so no caller can disagree with the table.
_TERMINAL_HEALTH_STATES = frozenset((UNIT_GAVE_UP,))

# --- bounds ----------------------------------------------------------------

# A git status is bounded so one enormous untracked directory cannot turn a
# fingerprint into a multi-megabyte string hashed on every poll. The *count* of
# codes goes into the fingerprint, so truncating the list still registers a
# change in the true count.
MAX_PORCELAIN_CODES = 4096

# Long enough to collapse an editor's save cascade (write temp, rename,
# truncate, write), short enough not to delay a real build step past useful.
DEFAULT_DEBOUNCE_SECONDS = 5.0
MIN_DEBOUNCE_SECONDS = 0.0
MAX_DEBOUNCE_SECONDS = 600.0

# A container reporting progress every 200ms for an hour is one stalled run.
# Emitting per poll is 18000 events, and the genuine state transition then
# arrives behind a wall of progress noise.
HEARTBEAT_BUCKET_SECONDS = 15.0

# systemd's own policy, copied whole rather than reinvented. The rolling-window
# restart cap is the load-bearing part: without it a crash loop that resets its
# attempt counter on every good second retries forever.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 60.0
BACKOFF_MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_RESTART_LIMIT = 5
BACKOFF_RESTART_WINDOW_SECONDS = 300.0

# Why a firing failed, as data rather than as an English prefix in `reason` -
# the same prose-matching problem `DispatchResult.ran` exists to stop. These are
# the three doors `_dispatch_now` and `TriggerEngine.evaluate` already had;
# `reason` remains the human-readable text.
FAILURE_NONE = ""
FAILURE_ACTUATOR_RAISED = "actuator_raised"
FAILURE_ACTUATOR_DID_NOT_RUN = "actuator_did_not_run"
FAILURE_VERIFICATION_FAILED = "verification_failed"
FAILURE_KINDS = frozenset((
    FAILURE_ACTUATOR_RAISED, FAILURE_ACTUATOR_DID_NOT_RUN, FAILURE_VERIFICATION_FAILED,
))

# A ceiling on `attempt` for its arithmetic, not for its policy. `delay_for`
# returns `min(base * 2 ** (attempt - 1), cap)`, so the delay stops growing at
# attempt 7 either way - but Python still *computes* `2 ** (attempt - 1)`
# first, and a persisted `attempt` of 100000 is a 30000-digit integer built to
# be discarded. The policy cap is `BACKOFF_MAX_CONSECUTIVE_FAILURES`; this only
# keeps the expression finite.
BACKOFF_MAX_ATTEMPT = 64

# The ceiling on a *persisted* per-rule budget. `BACKOFF_MAX_CONSECUTIVE_FAILURES`
# is the default a rule gets, not a limit a rule may not exceed, and 20 is chosen
# so that a rule allowed twelve failures is reachable in wall-clock terms: the
# delay ramp saturates at `BACKOFF_CAP_SECONDS` (60s) by attempt 7, so twenty
# consecutive failures is roughly 16 minutes of a broken actuator being retried
# before the rule parks and says so. Beyond that the budget stops describing
# patience and starts describing a backoff that will never fire again.
BACKOFF_MAX_RULE_CONSECUTIVE_FAILURES = 20

# Two firings, then park. "A week out" is the reminder with time left to act;
# "tomorrow" is the one where losing access is imminent. A third threshold adds
# nothing and turns a deadline into a nagging loop.
EXPIRY_THRESHOLDS = ((7 * 86400.0, "7d"), (86400.0, "1d"))

# `git status` on a large tree and `systemctl show` on a busy machine are both
# unbounded in the kernel, and one hung poll stops every rule on that tick.
_SIGNAL_TIMEOUT_SECONDS = 10.0

MAX_WATCH_ENTRIES = 20000

# --- consent surfaces the event rules add ----------------------------------
#
# `_INPUT_ACTUATORS` (above) covers the pointer and keyboard. An event rule adds
# two more risks that a percept rule never had:

# `_DESTRUCTIVE_ACTUATORS` is not here. It is defined once, beside
# `_actuator_problem` and `_UNATTENDED_WRITE_ACTUATORS`, in the "consent
# surfaces BOTH arm paths share" block above `TriggerRule`. A guard only one
# engine reads is not a guard the module has, and that asymmetry is exactly how
# the percept path came to accept every destructive actuator.
#
# `_NOTIFY_ONLY_EVENT_TYPES` is event-only on purpose. An expiry rule being
# narrowed to `notify` is a property of the *event type*; there is no percept
# rule for it to narrow.
#
# An expiry deadline may only be *told to* the user. A machine that notices its
# own credential is about to expire and then re-authorises itself unattended is
# the exact capability this whole module exists to not have: the consent keys
# here are all "may I perceive", never "may I grant myself". So the actuator
# set for an expiry rule is one skill wide, and it is `notify`.
_NOTIFY_ONLY_EVENT_TYPES = frozenset((EVENT_EXPIRY,))
_NOTIFY_ONLY_ACTUATORS = frozenset({"notify"})

# The key that governs *arming* a rule and *firing* one too, on both paths. A
# key that only stops you changing the rules is not a key that stops the rules,
# and reading it per event rather than caching it at arm time is what makes
# turning it off take effect immediately. Spelled here as a constant so no
# reader can drift, and so a test can assert the one the skill gates on is the
# one both engines gate on.
TRIGGER_CONTROL_KEY = "trigger-control-enabled"

# The `failure` signal source may be a stored verdict file, or one of a fixed
# table of command aliases. The table is the point: this module never runs a
# command string, and a rule cannot add to it. `AGENTS.md`'s permanent boundary
# is that the whitelist never becomes generic shell-exec, and an event rule
# that could name its own argv would be that boundary re-entering through the
# side door.
_FAILURE_COMMANDS = {
    "pacman-database-outdated": (
        ["pacman", "-Sy", "--quiet"],
        "pacman refused to read its database; the update check cannot be asked",
    ),
    "systemd-failed-units": (
        ["systemctl", "list-units", "--state=failed", "--no-legend",
         "--no-pager", "--plain"],
        "systemctl could not be asked about failed units",
    ),
}

# `git -c core.fsmonitor=false` on every read. A configured fsmonitor hook
# answers from its own cache, so two reads a second apart can disagree about
# the same tree - and a fingerprint that disagrees with itself produces events
# nobody caused. The same flag set makes the read deterministic across
# machines, which is what a durable fingerprint needs.
_GIT_DETERMINISM = ("-c", "core.fsmonitor=false")

EVENT_RULES_DIR = RULES_DIR
EVENT_RULES_FILE = EVENT_RULES_DIR / "event_rules.json"
FINGERPRINTS_FILE = EVENT_RULES_DIR / "fingerprints.json"
VERDICTS_DIR = EVENT_RULES_DIR / "verdicts"
DEADLINES_DIR = EVENT_RULES_DIR / "deadlines"


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
        self._path = Path(path) if path else FINGERPRINTS_FILE
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


def _fingerprint(value: Any) -> str:
    """A stable, order-independent string for an arbitrary observation.

    `repr` is deliberately not used: a dict's repr order follows insertion, so
    two structurally identical observations would hash differently and every
    poll would look like a transition. `senses/latch.fingerprint` already
    normalises ordering; it returns a hashable tuple, which is not
    JSON-serialisable, so the tuple is canonicalised here before hashing.
    """
    from shani_chronoa.senses.latch import fingerprint as _structural

    canonical = json.dumps(_jsonable(_structural(value)), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# --- events and signals ----------------------------------------------------

class Event(NamedTuple):
    """One state transition, already reduced to something worth acting on.

    `fingerprint` is the durable identity of the *state*, not of the event: two
    observations of the same state share it, so comparing fingerprints is what
    turns a stream of readings into a stream of transitions.

    `failed` says the state is a *bad* one. It is separate from `terminal`
    because the two answer different questions and only one of them is a
    property of the event: `terminal` is "retrying cannot help", which is a fact
    about the machine (`gave_up`, `exited`); `failed` is "this is not healthy",
    which is what a rule's `retry_policy` reacts to. A `failure` event cannot
    declare itself terminal, because whether a failed deploy is worth retrying
    is a decision only the armed rule can make - and inferring it from the text
    is what this module refuses to do.

    `progress` marks a heartbeat, the one kind of event the heartbeat bucket is
    allowed to swallow. A transition is never progress, which is what stops the
    bucket hiding the event it exists to make visible.
    """

    kind: str
    subject: str
    summary: str
    fingerprint: str
    detail: dict
    terminal: bool = False
    failed: bool = False
    progress: bool = False
    created_at: float = 0.0


class EventSignal(NamedTuple):
    """What one signal read produced. Never raises; never guesses.

    `status != SIGNAL_OK` with a `None` fingerprint is the whole "signal
    unavailable" contract, and it is checked by callers *before* the fingerprint
    is compared - because "unavailable" and "unchanged" produce the same
    fingerprint (there is none), and treating them alike is how a machine with
    no `git` installed comes to look like a machine with a clean repository.
    """

    status: str
    fingerprint: "Optional[str]" = None
    event: "Optional[Event]" = None
    detail: str = ""
    payload: Optional[dict] = None


def _unavailable(kind: str, subject: str, detail: str) -> EventSignal:
    return EventSignal(status=SIGNAL_UNAVAILABLE, detail=detail, payload={"kind": kind, "subject": subject})


def _watch_error(kind: str, subject: str, detail: str) -> EventSignal:
    return EventSignal(status=SIGNAL_WATCH_ERROR, detail=detail, payload={"kind": kind, "subject": subject})


def _run_argv(
    argv: "list[str]", timeout: float = _SIGNAL_TIMEOUT_SECONDS
) -> "Optional[subprocess.CompletedProcess]":
    """Run `argv` with no shell, or return None if it could not be run."""
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("trigger signal %s failed: %s", argv[:2], exc)
        return None


# --- 1. git ----------------------------------------------------------------

def _git(repo: Path, *args: str) -> "Optional[subprocess.CompletedProcess]":
    if shutil.which("git") is None:
        return None
    return _run_argv(["git", "-C", str(repo), *_GIT_DETERMINISM, *args])


def read_git_state(repo: Path, now: Optional[float] = None) -> EventSignal:
    """Git HEAD / branch / dirty-set, as a fingerprinted state.

    The fingerprint is `(HEAD sha, branch, sorted porcelain codes)`. Three
    choices in it, each of which a count-based version gets wrong:

    - **HEAD sha, not file count.** A commit touching 400 files is one event.
    - **Porcelain *codes*, not paths.** `git status` reports both, and paths
      churn without a state change (a file moving from modified to
      renamed-but-identical is the same dirty-ness). Codes also make the
      fingerprint independent of the checkout's absolute location.
    - **Sorted, from a `-z` read.** Unsorted, and a concurrent write reorders
      the listing between two polls and manufactures a transition nobody made.

    `upstream_gone` is reported as its own state rather than as `behind = 0`,
    because the two call for opposite responses: "nothing to pull" is a clean
    repository, and "the branch I track no longer exists" means a remote was
    renamed or a fetch was never run.

    Deliberately *not* in the fingerprint: `ahead`/`behind`. A push moves
    `ahead` with no change to HEAD, the tree or the branch, and a rule that
    notified on your own push would be a rule that notifies on you working.
    It is still in the payload, so a rule that wants it can match on it.
    """
    moment = time.time() if now is None else now
    repo = Path(repo)
    subject = str(repo)

    head = _git(repo, "rev-parse", "--verify", "HEAD")
    if head is None:
        return _unavailable(EVENT_GIT, subject, "the git binary is not available")
    if head.returncode != 0:
        # An unborn branch, a bare repo with no commits, a corrupt object
        # store. None of those is "the repository was deleted", and reporting
        # that would fire a rule that says "the repo went away".
        return _unavailable(
            EVENT_GIT, subject,
            f"git rev-parse --verify HEAD exited {head.returncode} "
            f"({(head.stderr or '').strip()[:200] or 'no stderr'})",
        )
    head_sha = (head.stdout or "").strip()

    branch_proc = _git(repo, "branch", "--show-current")
    branch = (branch_proc.stdout or "").strip() if branch_proc else ""

    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all",
                  "--no-renames", "-z")
    if status is None or status.returncode != 0:
        return _unavailable(
            EVENT_GIT, subject,
            "git status could not be read, so the working-tree half of the "
            "fingerprint is unknown",
        )
    codes: list[str] = []
    total = 0
    for record in (status.stdout or "").split("\0"):
        if len(record) < 2:
            continue
        total += 1
        if len(codes) < MAX_PORCELAIN_CODES:
            codes.append(record[:2])
    codes.sort()
    if total > len(codes):
        codes.append(f"truncated:{total}")

    ahead = behind = None
    upstream = None
    upstream_gone = False
    upstream_proc = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream_proc is not None and upstream_proc.returncode == 0:
        upstream = (upstream_proc.stdout or "").strip() or None
    if upstream:
        counts = _git(repo, "rev-list", "--count", "--left-right", f"{upstream}...HEAD")
        parts = (counts.stdout or "").split() if counts is not None else []
        if counts is not None and counts.returncode == 0 and len(parts) == 2 \
                and parts[0].isdigit() and parts[1].isdigit():
            behind, ahead = int(parts[0]), int(parts[1])
        else:
            upstream_gone = True
    elif branch:
        # `@{u}` failing is ambiguous on its own - it is what git says both when
        # no upstream is configured and when the remote-tracking ref is missing
        # (a renamed remote, or a never-fetched branch). Reading `branch.<b>.merge`
        # tells the two apart, and conflating them reports "the branch I track
        # no longer exists" as "this branch tracks nothing".
        configured = _git(repo, "config", "--get", f"branch.{branch}.merge")
        if configured is not None and configured.returncode == 0:
            remote = _git(repo, "config", "--get", f"branch.{branch}.remote")
            remote_name = (
                (remote.stdout or "").strip() if remote is not None
                and remote.returncode == 0 else "origin"
            ) or "origin"
            ref = (configured.stdout or "").strip()
            upstream = f"{remote_name}/{ref.rsplit('/', 1)[-1]}" if ref else None
            upstream_gone = True

    payload = {
        "head": head_sha,
        "branch": branch,
        "dirty": total,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "upstream_gone": upstream_gone,
    }
    fingerprint = _fingerprint(("git", head_sha, branch, tuple(codes)))

    if upstream_gone:
        summary = (
            f"git repository {repo} tracks {upstream!r}, which is gone; the "
            f"branch is {branch or 'detached'} at {head_sha[:12]}"
        )
    elif total:
        summary = (
            f"git repository {repo} is on {branch or 'a detached HEAD'} at "
            f"{head_sha[:12]} with {total} uncommitted change(s)"
        )
    else:
        summary = (
            f"git repository {repo} is on {branch or 'a detached HEAD'} at "
            f"{head_sha[:12]}, clean"
        )

    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload=payload,
        event=Event(
            kind=EVENT_GIT, subject=subject, summary=summary,
            fingerprint=fingerprint, detail=payload, terminal=False,
            created_at=moment,
        ),
    )


# --- 2. fswatch ------------------------------------------------------------

class WatcherError(Exception):
    """The watch itself failed. Distinct from "nothing changed"."""


class PollingDirWatcher:
    """A recursive (mtime, size) snapshot diff, in stdlib only.

    Not inotify: there is no stdlib binding, and a dependency would be a new
    failure mode rather than a new capability. A snapshot is honest about what
    it is, and `poll` returning a path is the same shape an inotify backend
    would return, so swapping one in is a subclass rather than a rewrite.

    `first` is the baseline: the first poll arms and returns nothing, because
    every file in a freshly-watched tree is "changed" relative to nothing and
    firing on that would notify about the user's entire home directory the
    first time a rule was armed.
    """

    def __init__(self) -> None:
        self._baseline: "Optional[dict[str, tuple[int, int]]]" = None

    def _snapshot(self, root: Path) -> "dict[str, tuple[int, int]]":
        found: dict[str, tuple[int, int]] = {}
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames):
                full = Path(dirpath) / name
                try:
                    info = full.stat()
                except OSError:
                    continue
                found[str(full)] = (info.st_mtime_ns, info.st_size)
                if len(found) >= MAX_WATCH_ENTRIES:
                    raise WatcherError(
                        f"{root} holds more than {MAX_WATCH_ENTRIES} files, so a "
                        "snapshot cannot tell change from noise"
                    )
        return found

    def poll(self, root: Path) -> "list[str]":
        current = self._snapshot(root)
        if self._baseline is None:
            self._baseline = current
            return []
        before, self._baseline = self._baseline, current
        changed = [
            path for path, stamp in current.items()
            if before.get(path) != stamp
        ]
        removed = [path for path in before if path not in current]
        return sorted(changed + removed)


def _confine_to_home(raw: str) -> "tuple[Optional[Path], str]":
    """Resolve `raw` inside the home directory, or explain why not.

    The confinement is tested on the *resolved* path, for the reason
    `senses/filesystem.py` gives: containment tested on the named path is
    defeated by a symlink, so `~/link -> /etc` passes a `startswith(home)` test
    and then watches `/etc`. A watch is a recursive read of everything beneath
    it, so this is the one bound in this module that has to be right.
    """
    home = Path.home().resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = home / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"could not resolve {raw!r} ({type(exc).__name__})"
    if not resolved.is_relative_to(home):
        return None, (
            f"{raw!r} resolves to {resolved}, which is outside your home "
            f"directory ({home}); Chronoa only watches inside your home"
        )
    if not resolved.is_dir():
        return None, f"{resolved} is not a directory, so there is nothing to watch"
    return resolved, ""


def read_watched_path(
    raw_path: str, watcher: "Optional[Any]" = None, now: Optional[float] = None
) -> EventSignal:
    """Recursive change detection on a user-named directory.

    The path is resolved and `realpath`-ed *before* the watcher is armed, and
    the arming key is the resolved path - so a rule cannot be starved by a
    second rule watching a symlink to the same directory, and the debounce
    window below is per resolved path.

    A `WatcherError` is surfaced as `SIGNAL_WATCH_ERROR`, never as "nothing
    changed". A dead watcher and a quiet directory are the same observation to
    every layer above this one, so folding them together is how a watch silently
    stops watching.
    """
    moment = time.time() if now is None else now
    resolved, problem = _confine_to_home(raw_path)
    if resolved is None:
        return _unavailable(EVENT_FSWATCH, raw_path, problem)

    active = watcher if watcher is not None else PollingDirWatcher()
    try:
        changed = list(active.poll(resolved))
    except WatcherError as exc:
        return _watch_error(EVENT_FSWATCH, str(resolved), str(exc))
    except OSError as exc:
        return _watch_error(EVENT_FSWATCH, str(resolved), f"the watch failed: {exc}")

    if not changed:
        return EventSignal(
            status=SIGNAL_OK, fingerprint=_fingerprint(("fswatch", str(resolved), "quiet")),
            detail=f"no change under {resolved}",
            payload={"kind": EVENT_FSWATCH, "subject": str(resolved), "changed": []},
        )

    subject = str(resolved)
    # Fingerprint over the resolved *root* and the changed set, so two
    # different files changing in the same poll are two distinct fingerprints
    # and neither can hide behind the other.
    fingerprint = _fingerprint(("fswatch", subject, tuple(changed)))
    summary = (
        f"{len(changed)} path(s) under {resolved} changed: "
        + ", ".join(os.path.basename(p) or p for p in changed[:5])
        + (", ..." if len(changed) > 5 else "")
    )
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={"kind": EVENT_FSWATCH, "subject": subject, "changed": changed},
        event=Event(
            kind=EVENT_FSWATCH, subject=subject, summary=summary,
            fingerprint=fingerprint,
            detail={"changed": changed[:50], "count": len(changed)},
            terminal=False, created_at=moment,
        ),
    )


# --- 3. failure ------------------------------------------------------------

_VERDICT_PASSED = "passed"
_VERDICT_FAILED = "failed"
_VERDICTS = frozenset((_VERDICT_PASSED, _VERDICT_FAILED))


def record_verdict(name: str, verdict: str, detail: str = "",
                   directory: Optional[Path] = None) -> Path:
    """Write a command's exit verdict where a `failure` rule will read it.

    Exists so the signal has a real producer rather than being a shape nobody
    writes. Deliberately the *only* place this module runs a command: a rule
    supplies the argv, and this is the code that executes it, so there is no
    path from a rule to a shell.
    """
    if verdict not in _VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(_VERDICTS)}")
    base = Path(directory) if directory else VERDICTS_DIR
    _ensure_state_dir(base)
    path = base / f"{name}.json"
    payload = {"verdict": verdict, "detail": detail, "recorded_at": time.time()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _restrict_file(tmp)
    os.replace(tmp, path)
    _restrict_file(path)
    return path


def read_failure_verdict(
    name: str,
    source: str = "verdict-file",
    directory: Optional[Path] = None,
    now: Optional[float] = None,
) -> EventSignal:
    """A command's exit verdict, fingerprinted as the verdict itself.

    Two sources and no third. `verdict-file` reads what something already ran
    and recorded; `command` runs one of the fixed aliases in
    `_FAILURE_COMMANDS`, whose argv is chosen in this module and cannot be
    chosen by a rule. A rule cannot supply a command string, because that is
    the generic-shell-exec boundary `AGENTS.md` forbids, arriving through the
    trigger path instead of the skill path.

    A missing or malformed verdict is `SIGNAL_UNAVAILABLE`, not "passed". The
    difference is the whole point of the event type: a check that stopped
    reporting is not a check that passed, and treating it as one is how a dead
    monitor is mistaken for a healthy system.

    The event is terminal for `failed` when the rule says so and retryable
    otherwise - which is decided by the rule, here, and not by parsing the
    text. See `RETRY_RETRYABLE` / `RETRY_TERMINAL`.
    """
    moment = time.time() if now is None else now
    subject = f"failure:{name}"

    if source == "command":
        entry = _FAILURE_COMMANDS.get(name)
        if entry is None:
            return _unavailable(
                EVENT_FAILURE, subject,
                f"{name!r} is not one of the fixed command aliases "
                f"({sorted(_FAILURE_COMMANDS)}); a rule cannot supply its own argv",
            )
        argv, reason = entry
        completed = _run_argv(list(argv))
        if completed is None:
            return _unavailable(
                EVENT_FAILURE, subject,
                f"{reason} ({argv[0]} could not be run at all)",
            )
        verdict = _VERDICT_FAILED if completed.returncode != 0 else _VERDICT_PASSED
        detail = (completed.stderr or completed.stdout or "").strip()[:200]
    else:
        base = Path(directory) if directory else VERDICTS_DIR
        path = base / f"{name}.json"
        if not path.is_file():
            return _unavailable(
                EVENT_FAILURE, subject,
                f"no verdict has been recorded at {path}, so whether the command "
                "passed is unknown",
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            return _unavailable(
                EVENT_FAILURE, subject, f"the recorded verdict at {path} is unreadable: {exc}"
            )
        if not isinstance(raw, dict) or raw.get("verdict") not in _VERDICTS:
            return _unavailable(
                EVENT_FAILURE, subject,
                f"the recorded verdict at {path} does not name one of "
                f"{sorted(_VERDICTS)}",
            )
        verdict = raw["verdict"]
        detail = str(raw.get("detail") or "")[:200]

    fingerprint = _fingerprint(("failure", name, verdict))
    summary = f"the check {name!r} is now {verdict}"
    if detail:
        summary = f"{summary}: {detail}"

    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={"kind": EVENT_FAILURE, "subject": subject, "verdict": verdict},
        event=Event(
            kind=EVENT_FAILURE, subject=subject, summary=summary,
            fingerprint=fingerprint, detail={"verdict": verdict, "note": detail},
            failed=verdict == _VERDICT_FAILED, created_at=moment,
        ),
    )


# --- 4. expiry -------------------------------------------------------------

def record_deadline(
    name: str, expires_at: float, label: str = "", directory: Optional[Path] = None
) -> Path:
    """Store a deadline for an `expiry` rule to count down to."""
    base = Path(directory) if directory else DEADLINES_DIR
    _ensure_state_dir(base)
    path = base / f"{name}.json"
    payload = {"expires_at": float(expires_at), "label": label,
               "recorded_at": time.time()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _restrict_file(tmp)
    os.replace(tmp, path)
    _restrict_file(path)
    return path


def read_expiry(
    name: str, directory: Optional[Path] = None, now: Optional[float] = None
) -> EventSignal:
    """A stored deadline, fired at fixed thresholds.

    Fires from a *timer* derived from the stored instant, never from an
    observed failure - so it cannot flap. A refresh that failed is a different
    event with a different fingerprint, not a retry of this one, and the
    fingerprint carries `expires_at` so that a *new* deadline is a new key and
    fires again while the same deadline never does. Without `expires_at` in the
    key, a renewed credential would be permanently muted by the first one's
    fingerprint, which is the failure this is shaped around.

    The highest threshold already reached is what gets recorded, so the 1d
    reminder cannot be re-raised by a poll that somehow still sees 7d as
    nearest, and once every threshold is spent the rule parks.
    """
    moment = time.time() if now is None else now
    subject = f"expiry:{name}"
    base = Path(directory) if directory else DEADLINES_DIR
    path = base / f"{name}.json"
    if not path.is_file():
        return _unavailable(
            EVENT_EXPIRY, subject, f"no deadline is stored at {path}"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return _unavailable(EVENT_EXPIRY, subject, f"the deadline at {path} is unreadable: {exc}")
    if not isinstance(raw, dict):
        return _unavailable(EVENT_EXPIRY, subject, f"the deadline at {path} is not an object")
    try:
        expires_at = float(raw["expires_at"])
    except (KeyError, TypeError, ValueError):
        return _unavailable(
            EVENT_EXPIRY, subject, f"the deadline at {path} has no usable 'expires_at'"
        )
    if expires_at != expires_at or expires_at in (float("inf"), float("-inf")):
        return _unavailable(EVENT_EXPIRY, subject, f"the deadline at {path} is not a real instant")

    remaining = expires_at - moment
    label = str(raw.get("label") or name)
    passed = [tag for threshold, tag in EXPIRY_THRESHOLDS if remaining <= threshold]

    if not passed:
        return EventSignal(
            status=SIGNAL_OK,
            fingerprint=_fingerprint(("expiry", name, expires_at, "before-threshold")),
            detail=f"{label} expires in {remaining / 86400.0:.1f} day(s)",
            payload={
                "kind": EVENT_EXPIRY, "subject": subject,
                "expires_at": expires_at, "remaining": remaining,
                "thresholds_passed": [],
            },
        )

    nearest = min(
        (t for t in EXPIRY_THRESHOLDS if remaining <= t[0]), key=lambda t: t[0]
    )[1]
    fingerprint = _fingerprint(("expiry", name, expires_at, nearest))
    summary = (
        f"{label} expires in {remaining / 86400.0:.2f} day(s) "
        f"({nearest} threshold reached)"
    )
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={
            "kind": EVENT_EXPIRY, "subject": subject, "expires_at": expires_at,
            "remaining": remaining, "thresholds_passed": passed, "threshold": nearest,
        },
        event=Event(
            kind=EVENT_EXPIRY, subject=subject, summary=summary,
            fingerprint=fingerprint,
            detail={"expires_at": expires_at, "threshold": nearest, "label": label},
            terminal=nearest == EXPIRY_THRESHOLDS[-1][1],
            created_at=moment,
        ),
    )


# --- 5. containerrun -------------------------------------------------------

CONTAINER_RUNNING = "running"
CONTAINER_EXITED = "exited"
CONTAINER_STALLED = "stalled"
CONTAINER_ABSENT = "absent"

_TERMINAL_CONTAINER_STATES = frozenset((CONTAINER_EXITED,))


def _container_runtime() -> "Optional[str]":
    for candidate in ("docker", "podman"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def read_container_state(
    name: str,
    runtime: "Optional[str]" = None,
    stalled_after: float = 900.0,
    now: Optional[float] = None,
) -> EventSignal:
    """A supervised container's state, with progress bucketed.

    `exited` is terminal and `stalled` is retryable, and the distinction is the
    reason this is a three-state read rather than "is it running". A container
    that finished is done; a container that has not reported progress for
    `stalled_after` is stuck and the right response differs.

    Progress is bucketed at `HEARTBEAT_BUCKET_SECONDS` by the engine, not here,
    so the *transition* into stalled is never bucketed away - a run whose last
    progress line is 20 minutes old is the event, and a bucket that swallowed
    it would be the bug.

    With no runtime installed the answer is unavailable. An empty
    `docker ps -a` on a machine that has never had docker is the same text as
    one whose container was removed, and reporting "exited" for it would fire
    a rule about something that never existed.
    """
    moment = time.time() if now is None else now
    subject = f"container:{name}"
    binary = runtime if runtime is not None else _container_runtime()
    if not binary:
        return _unavailable(
            EVENT_CONTAINERRUN, subject,
            "no container runtime (docker or podman) is installed, so this "
            "machine's container state cannot be read",
        )

    listed = _run_argv([binary, "inspect", "--format",
                        "{{.State.Status}}|{{.State.Running}}|{{.State.ExitCode}}",
                        name])
    if listed is None:
        return _unavailable(
            EVENT_CONTAINERRUN, subject, f"{binary} could not be run to inspect {name!r}"
        )
    if listed.returncode != 0:
        stderr = (listed.stderr or "").strip()[:200]
        if "No such" in (listed.stderr or "") or "not found" in (listed.stderr or "").lower():
            return _unavailable(
                EVENT_CONTAINERRUN, subject,
                f"no container named {name!r} exists, so its run cannot be tracked",
            )
        return _unavailable(
            EVENT_CONTAINERRUN, subject, f"{binary} inspect failed: {stderr}"
        )

    parts = (listed.stdout or "").strip().split("|")
    if len(parts) != 3:
        return _unavailable(
            EVENT_CONTAINERRUN, subject,
            f"{binary} inspect returned {len(parts)} field(s), not the 3 expected",
        )
    status, running, exit_code = parts
    if running == "true" and status == CONTAINER_RUNNING:
        # An event even for the healthy state. A reader that emitted nothing
        # here would leave the rule with no baseline, and the *first* notable
        # state would then be swallowed as "nothing has changed yet".
        healthy = _fingerprint(("containerrun", name, CONTAINER_RUNNING))
        return EventSignal(
            status=SIGNAL_OK, fingerprint=healthy,
            detail=f"container {name} is running",
            payload={"kind": EVENT_CONTAINERRUN, "subject": subject,
                     "state": CONTAINER_RUNNING},
            event=Event(
                kind=EVENT_CONTAINERRUN, subject=subject,
                summary=f"container {name} is running", fingerprint=healthy,
                detail={"state": CONTAINER_RUNNING, "name": name}, created_at=moment,
            ),
        )

    exit_line = _run_argv([binary, "inspect", "--format={{.State.ExitCode}}", name])
    stalled = False
    if running == "true" and status == "created":
        stalled = True
    if status not in (CONTAINER_EXITED, "created", "dead"):
        return _unavailable(
            EVENT_CONTAINERRUN, subject,
            f"{binary} reported state {status!r}, which this reader does not know; "
            "guessing a state is how a live run is reported as finished",
        )

    state = CONTAINER_EXITED if status == CONTAINER_EXITED else CONTAINER_STALLED
    if state == CONTAINER_STALLED and stalled_after is not None and stalled_after > 0:
        state = CONTAINER_STALLED
    summary = (
        f"container {name} {state}"
        + (f" with exit code {(exit_line.stdout or '?').strip()}" if state == CONTAINER_EXITED else "")
    )
    fingerprint = _fingerprint(("containerrun", name, state, (exit_line.stdout or "").strip()))
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={"kind": EVENT_CONTAINERRUN, "subject": subject, "state": state},
        event=Event(
            kind=EVENT_CONTAINERRUN, subject=subject, summary=summary,
            fingerprint=fingerprint, detail={"state": state, "name": name},
            terminal=state in _TERMINAL_CONTAINER_STATES, failed=True,
            created_at=moment,
        ),
    )


# --- 6. unithealth ---------------------------------------------------------

_SYSTEMD_ACTIVE = {
    "active": UNIT_WORKING,
    "activating": UNIT_STARTING,
    "reloading": UNIT_STARTING,
    "deactivating": UNIT_STOPPED,
    "inactive": UNIT_STOPPED,
    "failed": UNIT_GAVE_UP,
}
_SYSTEMD_SUB = {
    "auto-restart": UNIT_BACKING_OFF,
    "start": UNIT_STARTING,
    "start-pre": UNIT_STARTING,
    "running": UNIT_WORKING,
    "exited": UNIT_WORKING,
    "dead": UNIT_GAVE_UP,
    # A unit that has given up reports SubState=failed in practice, not just
    # `dead`; without this the most important state in the table is
    # unreachable and every gave-up unit reads as unmappable.
    "failed": UNIT_GAVE_UP,
}


def read_unit_health(unit: str, now: Optional[float] = None) -> EventSignal:
    """One systemd unit's health state, as a transition event plus a snapshot.

    The fingerprint is the health state, so only a *transition* is ever an
    event - and `payload["snapshot"]` carries the same state alongside
    `result`, `restarts` and `backoff`, so a consumer polling this can read the
    current state without inferring transitions itself. That is the difference
    between an event source and a question, and getting it wrong is how a
    status display ends up firing notifications of its own.

    `backing_off` and `gave_up` are separate states on purpose. systemd reports
    a unit that has given up as `ActiveState=failed`, and one that is waiting
    out its restart delay as `SubState=auto-restart`; a boolean reads them the
    same and then retries a dead unit for as long as the machine is up.
    """
    moment = time.time() if now is None else now
    subject = f"unit:{unit}"
    if shutil.which("systemctl") is None:
        return _unavailable(
            EVENT_UNITHEALTH, subject,
            "systemctl is not installed, so this machine is not systemd-managed "
            "and the unit's health cannot be read",
        )
    proc = _run_argv([
        "systemctl", "show", unit, "--no-pager",
        "--property=ActiveState", "--property=SubState",
        "--property=Result", "--property=NRestarts",
        "--property=StateChangeTimestamp",
    ])
    if proc is None:
        return _unavailable(EVENT_UNITHEALTH, subject, "systemctl could not be run")
    if proc.returncode != 0:
        return _unavailable(
            EVENT_UNITHEALTH, subject,
            f"systemctl does not know a unit named {unit!r} "
            f"({(proc.stderr or '').strip()[:200]})",
        )

    props: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    active = props.get("ActiveState", "")
    sub = props.get("SubState", "")
    if not active:
        return _unavailable(
            EVENT_UNITHEALTH, subject, "systemctl reported no ActiveState for the unit"
        )

    # Strict on the sub-state, with the active state only as the fallback when
    # systemd did not report one. A *known* sub-state under an odd active state
    # is trustworthy; an unknown sub-state is how a systemd upgrade introduces a
    # new one, and falling back to `ActiveState` there would report a starting
    # or running unit as merely working.
    if sub:
        health = _SYSTEMD_SUB.get(sub, "")
    else:
        health = _SYSTEMD_ACTIVE.get(active, "")
    if health == "":
        return _unavailable(
            EVENT_UNITHEALTH, subject,
            f"systemd reported ActiveState={active!r} SubState={sub!r}, which maps "
            "to no known health state; reporting a guess here is how a live unit "
            "gets reported as failed",
        )

    try:
        restarts = int(props.get("NRestarts", "0") or 0)
    except ValueError:
        restarts = -1
    snapshot = {
        "unit": unit,
        "health": health,
        "active_state": active,
        "sub_state": sub,
        "result": props.get("Result", ""),
        "restarts": restarts,
        "state_changed": props.get("StateChangeTimestamp", ""),
    }
    fingerprint = _fingerprint(("unithealth", unit, health))
    summary = f"unit {unit} is {health} (ActiveState={active}, SubState={sub})"
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={"kind": EVENT_UNITHEALTH, "subject": subject, "snapshot": snapshot},
        event=Event(
            kind=EVENT_UNITHEALTH, subject=subject, summary=summary,
            fingerprint=fingerprint, detail=snapshot,
            terminal=health in _TERMINAL_HEALTH_STATES,
            failed=health in (UNIT_BACKING_OFF, UNIT_GAVE_UP), created_at=moment,
        ),
    )


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
    return _unavailable(
        rule.event_type, rule.source, f"{rule.event_type!r} is not a known event type"
    )


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
        "fired_thresholds", "max_consecutive_failures",
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
    problem = _actuator_problem(actuator, allow_destructive)
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
        return EVENT_RULES_FILE


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
    """Turns the six event types into whitelisted actuator calls, under consent.

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
    ) -> None:
        from shani_chronoa.tools import execute_tool_outcome

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
        return _actuator_problem(rule.actuator, rule.allow_destructive)

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

    __slots__ = ("rule", "fired", "denied", "reason", "verdict", "failure_kind")

    def __init__(
        self,
        rule,
        fired: bool,
        denied: bool = False,
        reason: str = "",
        verdict: Optional[verification.Verdict] = None,
        failure_kind: str = FAILURE_NONE,
    ) -> None:
        self.rule = rule
        self.fired = fired
        self.denied = denied
        self.reason = reason
        # None when no verdict applies: the rule was denied, or only dry-run.
        self.verdict = verdict
        # Same values as `EventEvaluation.failure_kind`. Denials and dry runs
        # stay `FAILURE_NONE`: neither is a failure of the actuator.
        self.failure_kind = failure_kind

    def __repr__(self) -> str:
        return (
            f"FireResult(rule={self.rule.name!r}, fired={self.fired}, "
            f"denied={self.denied}, failure_kind={self.failure_kind!r}, "
            f"reason={self.reason!r}, verdict={self.verdict})"
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
        from shani_chronoa.tools import execute_tool_outcome

        self._store = store if store is not None else RuleStore()
        self._config_factory = config_factory or ChronoaConfig
        self._dispatch = dispatch or execute_tool_outcome

    def store(self) -> "RuleStore":
        return self._store

    def _consent(self, config, rule) -> str:
        """Empty when the rule may act, otherwise why it may not.

        Three refusals, all read per percept rather than cached at arm time,
        because the whole point of a consent key is that turning it off takes
        effect immediately.

        The first was **missing** until 2026-09-30 and it is the load-bearing
        one. `EventEngine._consent` gained it on that date; this function, the
        one the *percept* half of the module runs through, did not, and still
        had no `trigger-control-enabled` check when the omission was fixed
        there. So the switch that gates arming a rule gated nothing at all on
        the percept firing path: a user who armed a rule and then turned the key
        off had revoked permission to *change* the rules and nothing else. The
        key reads `false` by default in the schema, so the fail-closed
        direction is also the default one - a schema predating the key yields
        the supplied `False` and denies every rule rather than permitting all.

        The severity is honestly **latent**, not a live unattended escalation:
        `fire_all` and `evaluate_many` have exactly one caller -
        `shani-chronoa-sense trigger run` - and `AmbientScheduler` drives the
        *event* engine only, so no percept rule fires on its own today. It
        becomes critical the moment the scheduler is wired to percept rules,
        and `skills/manage_triggers.py` is what makes that arming path
        reachable from a spoken instruction in the meantime.

        The third is the arm-time destructive default-deny re-checked here, so
        a rules file edited by hand is not a way around it - the same argument
        the event path already made for its own copy.
        """
        if not config.get_bool(TRIGGER_CONTROL_KEY, False):
            return (
                f"arming or disarming automatic rules is turned off (enable "
                f"'{TRIGGER_CONTROL_KEY}' in Settings), so no rule may act "
                "unattended - including one armed before the key was turned off"
            )
        if not config.sense_allowed(rule.sense):
            return f"the {rule.sense} sense is not permitted: {config.sense_allowed_reason(rule.sense)}"
        if rule.actuator in _INPUT_ACTUATORS and not config.input_control_enabled:
            return (
                f"the {rule.actuator} actuator needs the 'input-control-enabled' "
                "consent key, which is off"
            )
        return _actuator_problem(rule.actuator, rule.allow_destructive)

    def evaluate(
        self, percept, now: Optional[float] = None, dry_run: bool = False
    ) -> "list[FireResult]":
        """Fire every armed rule this percept matches. Never raises.

        `dry_run` reports what *would* fire without dispatching anything. It
        exists because the alternative is unacceptable: without it there is no
        way to ask "is this rule armed, does it match, and is it consented?"
        except by letting it act. A user cannot review an unattended action
        before it happens, and a denied rule cannot be distinguished from an
        absent one without firing something.
        """
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
            if dry_run:
                results.append(FireResult(rule, fired=False, reason="would fire (dry run)"))
                continue
            try:
                # origin= is what makes this distinguishable in the audit log
                # from a person asking. An unattended action nobody can
                # distinguish from a user action is not auditable.
                outcome = self._dispatch(rule.actuator, dict(rule.arguments), origin=_ORIGIN)
            except Exception as exc:  # noqa: BLE001 - one bad rule must not stop the rest
                results.append(FireResult(
                    rule, fired=False, reason=f"{type(exc).__name__}: {exc}",
                    failure_kind=FAILURE_ACTUATOR_RAISED,
                ))
                continue
            # An injected dispatch may still be the plain string-returning
            # seam, so accept either shape rather than assuming.
            text = getattr(outcome, "text", outcome)
            verdict = getattr(
                outcome, "verdict", verification.verdict_from_text(text or "")
            )
            if getattr(outcome, "ran", None) is False:
                # The one UNVERIFIED that is not "it ran and cannot be checked
                # against": the tool never executed. Reported as not-fired, and
                # with no cooldown started, on the same reasoning as FAILED
                # below - a rule must not sit out its window having done
                # nothing. This engine has no backoff counters to park on, so
                # the next matching percept is the retry, which is the whole of
                # the retry story on this path.
                results.append(FireResult(
                    rule, fired=False,
                    reason=f"the actuator did not run: {text or 'no detail'}",
                    verdict=verdict,
                    failure_kind=FAILURE_ACTUATOR_DID_NOT_RUN,
                ))
                continue
            if verdict is verification.Verdict.FAILED:
                # The actuator ran and its post-condition says the effect is
                # not there. Cooldown is deliberately NOT started: the rule
                # stays due so the next matching percept retries, and the
                # failure is reported rather than being spent silently.
                results.append(FireResult(
                    rule, fired=False,
                    reason=f"actuator ran but verification failed: "
                           f"{getattr(outcome, 'evidence', '') or 'no evidence'}",
                    verdict=verdict,
                    failure_kind=FAILURE_VERIFICATION_FAILED,
                ))
                continue
            rule.last_fired_at = moment
            results.append(FireResult(rule, fired=True, verdict=verdict))

        return results

    def evaluate_many(
        self, percepts, now: Optional[float] = None, dry_run: bool = False
    ) -> "list[FireResult]":
        """Evaluate a batch without dispatching. Preserves order."""
        out: list[FireResult] = []
        for percept in percepts:
            out.extend(self.evaluate(percept, now=now, dry_run=dry_run))
        return out

    def fire_all(self, percepts, now: Optional[float] = None) -> "list[FireResult]":
        """Evaluate a batch, preserving order. Used by the polling loop."""
        out: list[FireResult] = []
        for percept in percepts:
            out.extend(self.evaluate(percept, now=now))
        return out
