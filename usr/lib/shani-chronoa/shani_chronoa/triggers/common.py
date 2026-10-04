"""Limits, event-type and state names, file locations and the actuator safety rules every part of the trigger engine shares."""

from __future__ import annotations


import hashlib
import json
from pathlib import Path
from typing import Any, NamedTuple, Optional

from shani_chronoa import files
from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED


def _data_home() -> Path:
    """Delegates to `files.data_home`, read on every call through `triggers_dir()`.

    Armed rules are unattended capability. Their paths used to be module-level
    constants resolved at import, so a per-test HOME "landed too late" and the
    suite leaked into the real home; now nothing here captures a path at import.
    A relative XDG_DATA_HOME counts as unset, per the XDG spec, so it cannot
    resolve outside the data directory.

    This was the third copy of that rule in the package, alongside
    `egress.py`'s and `files.py`'s. One implementation means a change to the
    fallback cannot leave two of them disagreeing.
    """
    return files.data_home()


# Where armed rules live. User-owned, under the same per-user data dir the rest
# of the repo uses, and created with restrictive permissions so a dropped-in
# rule file cannot be read by another user.
def triggers_dir() -> Path:
    """Where armed rules and their state live - resolved on every call, never at import.

    A path captured at import ignored the per-test HOME/XDG_DATA_HOME, and test
    runs wrote armed rules and corrupt rule files into a real user's
    ~/.local/share/shani-chronoa/triggers (recorded twice: tests/conftest.py,
    and CHRONOA-HARVEST.md Part 8). Calling this each time is what makes a
    sandboxed run actually sandboxed.
    """
    return _data_home() / "shani-chronoa" / "triggers"

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


def _actuator_problem(actuator: str, allow_destructive: bool, ask_first: bool = False) -> "Optional[str]":
    """Why this actuator may not be driven unattended, or None if it may.

    Shared by both arm-time validators and both dispatch-time consent checks,
    so the refusal text cannot drift between them. Every rejection names the
    thing that would have to change, because a refusal a user cannot act on is
    indistinguishable from a bug.
    """
    if ask_first:
        # A person answers "Allow once" at the moment it would act, so this is
        # no longer an unattended action - the reason both refusals below exist.
        return None
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


# ============================================================================
# EVENT TRIGGERS
# ============================================================================
#
# Everything above this line is *percept*-triggered: a sense emits, the content
# is matched, an actuator runs. That model has exactly one kind of question -
# "does this percept contain the string?" - and the event types below do
# not fit it. None of them is a percept. Each is a question about a *state* the
# machine is in, and each is only interesting when that state **transitions**:
#
#   git        did HEAD / branch / dirty-set change?
#   fswatch    did something under this directory change?
#   failure    did a command's exit verdict change?
#   expiry     did a stored deadline cross a threshold?
#   containerrun did a supervised container exit or stall?
#   unithealth did a unit's health state transition?
#   screenlock did the session lock, unlock, go idle or come back?
#   powerstate did the machine go on battery / AC, or cross a charge level?
#   netstate   did connectivity, or a named connection, come up or go down?
#   usbplug    was a USB device plugged in or pulled out?
#   btconnect  did a named Bluetooth device connect or disconnect?
#   schedule   did a calendar time (daily 08:00, weekdays 18:30, every 30 minutes) arrive?
#   sleepwake  did the machine just resume from suspend?
#   audiodevice was an audio output or input (headphones, a USB mic) added or removed?
#   journalmatch did a new journal entry matching a pattern appear?
#   dbusprop   did a D-Bus property take a value (or change at all)?
#   calendar   is a calendar event about to start?
#   phone      did the paired phone connect, disconnect or run low?
#
# The last six share one shape, and it is what makes them safe to poll: the
# rule's `source` names the state the user wants to be told about ("locked",
# "on-battery", "daily 08:00"), the fingerprint is the observed state whether
# or not it is that one, and an event is produced only while the machine is in
# it. So the engine's baseline-then-transition logic fires exactly on the
# transition *into* the named state - arming "locked" while the screen is
# locked records a baseline and does not fire, and a screen that stays locked
# for an hour fires once.
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

EVENT_SCREENLOCK = "screenlock"
EVENT_POWERSTATE = "powerstate"
EVENT_NETSTATE = "netstate"
EVENT_USBPLUG = "usbplug"
EVENT_BTCONNECT = "btconnect"
EVENT_SCHEDULE = "schedule"
EVENT_SLEEPWAKE = "sleepwake"
EVENT_AUDIODEVICE = "audiodevice"
EVENT_JOURNALMATCH = "journalmatch"
EVENT_DBUSPROP = "dbusprop"
EVENT_CALENDAR = "calendar"
EVENT_PHONE = "phone"
EVENT_SOUND = "sound"

EVENT_TYPES = frozenset(
    (EVENT_GIT, EVENT_FSWATCH, EVENT_FAILURE, EVENT_EXPIRY,
     EVENT_CONTAINERRUN, EVENT_UNITHEALTH, EVENT_SCREENLOCK, EVENT_POWERSTATE,
     EVENT_NETSTATE, EVENT_USBPLUG, EVENT_BTCONNECT, EVENT_SCHEDULE, EVENT_SLEEPWAKE,
     EVENT_AUDIODEVICE, EVENT_JOURNALMATCH, EVENT_DBUSPROP, EVENT_CALENDAR, EVENT_PHONE, EVENT_SOUND)
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

# `git status` on a large tree and `systemctl show` on a busy machine are both
# unbounded in the kernel, and one hung poll stops every rule on that tick.
_SIGNAL_TIMEOUT_SECONDS = 10.0

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
