"""Skill: arm, list and disarm trigger rules - percept-triggered and event-triggered.

**This is what makes `triggers.py` reachable from an LLM turn.** Without it the
engine's only consumer is the `shani-chronoa-sense trigger ...` CLI - a
subcommand nobody types - so a user cannot ask for a schedule, cannot see what
is armed, and cannot disarm something they armed by accident. That is the same
dead-code class `AGENTS.md` records repeatedly (`gateway_supervisor.py`,
`sandbox/profiles.py`, and the senses layer before 2026-09-27), and the fix is
the same one: wire the consumer, don't rewrite the engine.

Both rule kinds are armed here. A *percept* rule fires when a sense reports
something matching it, via `triggers.build_rule`. An *event* rule fires on one
of the nineteen event signals (git, fswatch, failure, expiry, containerrun,
unithealth, screenlock, powerstate, netstate, usbplug, btconnect, schedule,
sleepwake, audiodevice, journalmatch, dbusprop, calendar, phone, sound) via
`triggers.build_event_rule`. The two have deliberately
different validation - the event path default-denies a destructive actuator -
so read both before changing either.

**What this deliberately is not, and the reason is the whole safety story.**
A rule names one *already-whitelisted* skill with fixed user-supplied
arguments, validated by `triggers.build_rule` at arm time. There is no
`prompt` field, no `command` field, and no skill resolved at fire time, and
this skill adds none of those - an unattended action a language model authored
is not one the consent gates can reason about. `build_rule` refuses an
actuator that is not in the shipped registry, so a rule cannot name a skill
that does not exist and cannot later be silently redirected to one that does.

**`list` is not gated; `add`, `remove` and `clear` are.** A user must be able
to find out what is armed without first being granted permission to arm
anything, or the gate hides the very state it exists to protect - and a rule
the user cannot see is a rule they cannot revoke. This is the same split
`find_and_replace` makes between its dry run and its write.

**A rule is not a promise that anything will happen.** A rule whose sense is
switched off, or whose actuator is gated, is denied at fire time by the engine
and reported there. So the reply after `add` says what would fire *now* and
names what would block it, rather than "you will be reminded at 8am" - a
reminder the user then does not receive is worse than one they were told
would not happen.
"""

from __future__ import annotations

import json
import re

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.triggers import EVENT_TYPES, DEFAULT_COOLDOWN_SECONDS, DEFAULT_DEBOUNCE_SECONDS, MATCH_ANY, MATCH_KEYWORDS, MATCH_SUBSTRING, RETRY_POLICIES, RETRY_RETRYABLE, TRIGGER_CONTROL_KEY, EventEngine, EventRuleStore, RuleStoreError, TriggerEngine, build_event_rule, build_rule

_CONSENT_KEY = TRIGGER_CONTROL_KEY
_ACTIONS = ("list", "add", "remove", "clear")
_MATCH_MODES = (MATCH_SUBSTRING, MATCH_KEYWORDS)
_EVENT_MATCH_MODES = (MATCH_ANY, MATCH_SUBSTRING, MATCH_KEYWORDS)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "manage_triggers",
        "description": (
            "Arm, list, or remove automatic rules that run a Chronoa action "
            "when a perception matches - for example, notify when the battery "
            "sense reports low. A rule names one already-whitelisted skill; it "
            "cannot run an arbitrary command or an LLM-authored prompt. Listing "
            "needs no permission; arming or disarming requires the "
            "'trigger-control-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": f"What to do: {', '.join(_ACTIONS)}. Defaults to list.",
                },
                "name": {
                    "type": "string",
                    "description": "A name for the rule. Required for add and remove.",
                },
                "sense": {
                    "type": "string",
                    "description": (
                        "Which sense's perceptions to match, e.g. 'power' "
                        "(which reports battery and charging), or "
                        "'heard-sound' for sounds in the room. Required "
                        "for add. The settings window lists every sense and what "
                        "it reports."
                    ),
                },
                "match_mode": {
                    "type": "string",
                    "description": (
                        f"'{MATCH_SUBSTRING}' or '{MATCH_KEYWORDS}'. Defaults to "
                        f"substring. With event_type, '{MATCH_ANY}' is also allowed "
                        f"and is the default there - fire on any real change."
                    ),
                },
                "substring": {
                    "type": "string",
                    "description": "The text to match, for a substring rule.",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Any of these matching is enough, for a keywords rule.",
                },
                "actuator": {
                    "type": "string",
                    "description": (
                        "The whitelisted skill to run, e.g. 'notify'. Must "
                        "already be an installed Chronoa skill."
                    ),
                },
                "arguments": {
                    "type": "object",
                    "description": (
                        "The fixed arguments to call that skill with, e.g. "
                        "{\"summary\": \"Battery is low\"}. Checked against that "
                        "skill's own declared schema."
                    ),
                },
                "cooldown_seconds": {
                    "type": "number",
                    "description": "Minimum gap between firings. Defaults to 30.",
                },
                "event_type": {
                    "type": "string",
                    "description": (
                        "Watch a machine event instead of a sense. One of: "
                        f"{', '.join(sorted(EVENT_TYPES))}. An event rule polls its "
                        "source directly rather than matching a perception. Leave "
                        "it out for an ordinary sense rule."
                    ),
                },
                "source": {
                    "type": "string",
                    "description": (
                        "What the event rule watches: a path, unit or container, or "
                        "the state to be told about ('locked', 'on-battery', "
                        "'daily 08:00', or a sound such as 'doorbell'). A source that type cannot read is refused "
                        "with the forms it accepts. Required with event_type."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Reader options for that event type, as plain JSON values "
                        "only (a live watcher handle is not accepted and is not "
                        "persisted). 'failure' takes signal='verdict-file' or one of "
                        "the fixed command aliases; 'schedule' takes "
                        "grace_minutes; 'containerrun' takes "
                        "stalled_after seconds."
                    ),
                },
                "ask_first": {
                    "type": "boolean",
                    "description": "Event rules: ask with an Allow/Deny notification before each run.",
                },
                "retry_policy": {
                    "type": "string",
                    "description": (
                        f"What to do about a state that will not fix itself. One of: "
                        f"{', '.join(RETRY_POLICIES)}. Defaults to {RETRY_RETRYABLE}."
                    ),
                },
                "debounce_seconds": {
                    "type": "number",
                    "description": (
                        "Collapse a burst of changes into one delayed run, so one "
                        "commit touching 400 files notifies once."
                    ),
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"arming or disarming automatic rules is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Listing what is already armed needs "
            f"no such permission - only changing it does, because an armed rule "
            f"acts without asking again."
        )
    return True, ""


def _render(rules) -> str:
    if not rules:
        return "No trigger rules are armed."
    lines = [f"{len(rules)} trigger rule(s) armed:"]
    for rule in rules:
        state = "enabled" if rule.enabled else "disabled"
        match = (
            f"--{rule.match_mode}-- {rule.substring!r}"
            if rule.match_mode == MATCH_SUBSTRING
            else f"--{rule.match_mode}-- {rule.keywords}"
        )
        lines.append(
            f"  {rule.name}  [{state}]  {rule.sense} {match}  ->  "
            f"{rule.actuator}  (cooldown {rule.cooldown_seconds:g}s)"
        )
        lines.append(f"      arguments: {json.dumps(rule.arguments, sort_keys=True)}")
    lines.append(
        "  An armed rule only fires while its sense and its action are both "
        "permitted; either being off makes it a no-op rather than an error."
    )
    return "\n".join(lines)


def _render_event(rules) -> str:
    """Event rules, in the same shape `_render` uses so the two read alike.

    A parked rule says so, and says why: a rule that stopped on purpose and
    one that stopped because its consent was withdrawn are different facts, and
    only the first is the user's own doing.
    """
    if not rules:
        return "No event rules are armed."
    lines = [f"{len(rules)} event rule(s) armed:"]
    for rule in rules:
        state = "disabled" if not rule.enabled else (
            f"parked ({rule.parked_reason})" if rule.parked else "enabled"
        )
        lines.append(
            f"  {rule.name}  [{state}]  {rule.event_type} on {rule.source}  ->  "
            f"{rule.actuator}  (cooldown {rule.cooldown_seconds:g}s, "
            f"debounce {rule.debounce_seconds:g}s, retry {rule.retry_policy})"
        )
        lines.append(f"      arguments: {json.dumps(rule.arguments, sort_keys=True)}")
        if rule.params:
            lines.append(
                f"      params: {json.dumps(rule.params, sort_keys=True)}"
            )
    lines.append(
        "  An event rule polls its source itself, so it needs its own consent "
        "switch as well as permission to arm rules. It stays inert, and says "
        "why, until both are granted."
    )
    return "\n".join(lines)


def _check_arguments(actuator: str, arguments) -> "tuple[dict, str]":
    """(arguments, problem) - checked against the actuator's own schema.

    `triggers.build_rule` validates the *shape* (a dict, at most 8 entries, no
    over-long values) and its own docstring claims the arguments are "validated
    against the actuator's own schema at arm time". Running it shows that is
    not true: a rule armed with `{'totally_bogus_arg': 1}` is accepted and the
    bogus key is carried through to the actuator unchanged. The engine's
    `guardrail` pass only checks types on keys the schema declares, so an
    undeclared extra reaches the skill as a real argument. Checking here is
    what makes the docstring's claim true for every rule armed from this
    skill, without editing the engine another agent owns.
    """
    if arguments is None:
        return {}, ""
    if not isinstance(arguments, dict):
        return {}, f"arguments must be a JSON object, not {type(arguments).__name__}."

    from shani_chronoa.skills import discover_skills
    tools, _handlers = discover_skills()
    for tool in tools:
        function = tool.get("function") or {}
        if function.get("name") != actuator:
            continue
        declared = ((function.get("parameters") or {}).get("properties") or {})
        undeclared = sorted(set(arguments) - set(declared))
        if undeclared:
            return {}, (
                f"arguments {undeclared} are not declared by the {actuator} "
                f"skill, so they would be passed to it unrecognised. It accepts: "
                f"{', '.join(sorted(declared)) or 'no arguments'}."
            )
        missing = sorted(
            set(((function.get("parameters") or {}).get("required") or []))
            - set(arguments)
        )
        if missing:
            return {}, (
                f"arguments are missing the required {missing} for the "
                f"{actuator} skill, so the rule could not fire usefully."
            )
        return dict(arguments), ""
    return {}, (
        f"The {actuator} skill is not installed, so a rule naming it could not "
        f"be checked against its arguments."
    )


#: The status a reader returns when it cannot read the source at all.
_UNREADABLE_SOURCE = "unavailable"

#: Phrasings that mean *the source string itself* is wrong, rather than the
#: thing it names being absent. Both arrive as SIGNAL_UNAVAILABLE, and the
#: difference matters: arming `git` on a repository you have not cloned yet is
#: sensible and must be allowed, while `doorbell:1.5` is a typo that would
#: otherwise sit armed and never fire.
#:
#: This is still prose-matching, and deliberately so - it is a much smaller
#: surface than the regex it replaces (a fixed list of complete phrases, rather
#: than the openings of whatever English a reader happened to write), and it
#: sits *behind* the status check, so a reader that says none of them is
#: allowed through rather than wrongly blocked. The mistake it can still make
#: is admitting a malformed source, which is the behaviour that already existed;
#: the mistake it can no longer make is refusing a legitimate one.
_MALFORMED_SOURCE_PHRASES = (
    "source is the name of",
    "must be between",
    "needs a percentage from",
    "must be a percentage from",
    "is not a unit name",
    "is not a day",
    "is not a time of day",
    "source must be",
    "the pattern must be",
    "use 'daily",
    "every N minutes",
    "hourly :MM",
    "a bus name, path",
)


def _names_the_source(detail: str) -> bool:
    """Whether this refusal is about the source's form, not its target."""
    return any(phrase in detail for phrase in _MALFORMED_SOURCE_PHRASES)


def _open(kind: str):
    """The store for one rule kind, or a message explaining why there isn't one.

    `percept` is the original sense-matching store; `event` is the event-source
    store. Kept lazy and per-kind so a corrupt `event_rules.json` cannot stop
    `list` from reporting the percept rules, and so adding an ordinary sense
    rule never depends on the event store being readable at all.
    """
    try:
        if kind == "event":
            return EventRuleStore(), ""
        return TriggerEngine().store(), ""
    except (RuleStoreError, OSError) as exc:
        return None, f"Could not open the {kind} trigger store: {exc}"


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "list").strip().lower()
    if action not in _ACTIONS:
        return f"Action must be one of {', '.join(_ACTIONS)}, not {action!r}."

    if action == "list":
        return _list_both()

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change armed rules: {reason}"

    if action in ("remove", "clear"):
        return _remove_or_clear(action, arguments)

    event_type = (arguments.get("event_type") or "").strip().lower()
    if event_type:
        return _add_event_rule(arguments, event_type)

    return _add_percept_rule(arguments)


def _list_both() -> str:
    """Every armed rule of either kind, plus any store that would not open.

    Ungated, and both stores. A listing that silently covered only the sense
    rules would report "nothing is armed" while an event rule sat armed and
    firing unattended - the gate hiding the state it exists to protect.
    """
    blocks: list[str] = []
    unreadable: list[str] = []
    for kind, renderer in (("percept", _render), ("event", _render_event)):
        store, problem = _open(kind)
        if store is None:
            unreadable.append(problem)
            continue
        blocks.append(renderer(store.all()))
    text = "\n\n".join(blocks)
    if unreadable:
        text = (
            f"{text}\n\nNOT SHOWN - an armed rule you cannot see is one you "
            f"cannot revoke: " + "; ".join(unreadable)
        )
    return text


def _remove_or_clear(action: str, arguments: dict) -> str:
    name = (arguments.get("name") or "").strip()
    if action == "remove" and not name:
        return "No rule name was given, so nothing was removed."

    removed: list[str] = []
    names: list[str] = []
    problems: list[str] = []
    for kind in ("percept", "event"):
        store, problem = _open(kind)
        if store is None:
            problems.append(problem)
            continue
        names.extend(store.names())
        if action == "clear":
            count = store.clear()
            if count:
                removed.append(f"{count} {kind} rule(s)")
        elif store.remove(name):
            removed.append(f"the {kind} rule")

    if problems:
        return (
            f"Refusing to report a clean result: {'; '.join(problems)} "
            "Nothing was removed."
        )
    if not removed:
        if action == "remove":
            armed = ", ".join(sorted(set(names))) or "none"
            return (
                f"No rule named {name!r} is armed (armed: {armed}). "
                "Nothing was removed."
            )
        return "No trigger rules were armed, so nothing was removed."
    return (
        f"Removed {', '.join(removed)}. "
        f"{'It' if len(removed) == 1 else 'They'} can no longer fire."
    )


def _add_percept_rule(arguments: dict) -> str:
    store, problem = _open("percept")
    if store is None:
        return problem
    engine = TriggerEngine(store=store)

    name = (arguments.get("name") or "").strip()
    if not name:
        return "A rule needs a name, so nothing was armed."
    sense = (arguments.get("sense") or "").strip()
    if not sense:
        return "A rule needs a sense to match, so nothing was armed."
    actuator = (arguments.get("actuator") or "").strip()
    if not actuator:
        return (
            "A rule needs an actuator - the whitelisted skill to run. Nothing "
            "was armed. Use list_capabilities to see what is installed."
        )
    match_mode = (arguments.get("match_mode") or MATCH_SUBSTRING).strip().lower()
    if match_mode not in _MATCH_MODES:
        return (
            f"match_mode must be {MATCH_SUBSTRING} or {MATCH_KEYWORDS}, not "
            f"{match_mode!r}. Nothing was armed."
        )

    checked, problem = _check_arguments(actuator, arguments.get("arguments"))
    if problem:
        return f"Refusing to arm {name!r}: {problem} Nothing was armed."

    keywords = [k for k in (arguments.get("keywords") or []) if isinstance(k, str) and k]
    rule, why = build_rule(
        name=name, sense=sense, match_mode=match_mode, actuator=actuator,
        arguments=checked,
        substring=(arguments.get("substring") or ""),
        keywords=keywords,
        cooldown_seconds=arguments.get("cooldown_seconds") or 30.0,
    )
    if rule is None:
        return f"Refusing to arm {name!r}: {why or 'the rule was rejected'}. Nothing was armed."

    try:
        store.add(rule)
    except RuleStoreError as exc:
        return f"Refusing to arm {name!r}: {exc} Nothing was armed."

    # Say what would actually happen now, and what would stop it. "You will be
    # reminded at 8am" for a rule whose sense is switched off is a promise the
    # engine will silently not keep.
    config = ChronoaConfig()
    denial = engine._consent(config, rule)
    armed = (
        f"Armed {name!r}: {rule.sense} --{rule.match_mode}--> {rule.actuator}."
    )
    if denial:
        return (
            f"{armed} It would NOT fire right now: {denial}. The rule is stored "
            f"and will act once that is allowed."
        )
    return (
        f"{armed} It would fire the next time a {rule.sense} percept matches, "
        f"at most once every {rule.cooldown_seconds:g}s. Use remove with this "
        f"name to stop it."
    )


def _add_event_rule(arguments: dict, event_type: str) -> str:
    """Arm an event rule through `triggers.build_event_rule`.

    Two things this deliberately does not accept from the model, and both are
    the reason an event rule is safe to arm at all:

    - **`allow_destructive`.** `build_event_rule` refuses a destructive
      actuator unless the caller opts in, and `EventEngine._consent` refuses it
      again at dispatch unless the rule carries the flag. Exposing the flag
      here would let a model turn both refusals off with one argument, so
      arming a destructive unattended action stays a hand-edited-rules-file
      act. Not in the schema, so it cannot arrive from a turn.
    - **A prompt or a command.** Same boundary as the percept path: the rule
      names one whitelisted skill and fixed arguments. `params` is restricted
      to JSON scalars by `build_event_rule` precisely because it is persisted.
    """
    store, problem = _open("event")
    if store is None:
        return problem

    name = (arguments.get("name") or "").strip()
    if not name:
        return "A rule needs a name, so nothing was armed."
    if event_type not in EVENT_TYPES:
        return (
            f"event_type must be one of {', '.join(sorted(EVENT_TYPES))}, not "
            f"{event_type!r}. Nothing was armed."
        )
    source = (arguments.get("source") or "").strip()
    if not source:
        return (
            f"A {event_type} rule needs a source to watch - the path, unit or "
            "container it reports on. Nothing was armed."
        )
    actuator = (arguments.get("actuator") or "").strip()
    if not actuator:
        return (
            "A rule needs an actuator - the whitelisted skill to run. Nothing "
            "was armed. Use list_capabilities to see what is installed."
        )

    checked, problem = _check_arguments(actuator, arguments.get("arguments"))
    if problem:
        return f"Refusing to arm {name!r}: {problem} Nothing was armed."

    keywords = [k for k in (arguments.get("keywords") or []) if isinstance(k, str) and k]
    # `any` is the default here, not `substring`: an event rule's question is
    # normally "tell me when this source changes", and the reader already
    # decided the change was real. A substring default would make every such
    # request fail validation for want of a substring the caller never wanted.
    match_mode = (arguments.get("match_mode") or MATCH_ANY).strip().lower()
    if match_mode not in _EVENT_MATCH_MODES:
        return (
            f"match_mode for an event rule must be one of "
            f"{', '.join(_EVENT_MATCH_MODES)}, not {match_mode!r}. Nothing was "
            f"armed."
        )
    # `or` is wrong for both of these: `0` is a legal value (MIN_DEBOUNCE_SECONDS
    # is 0.0, i.e. "act on the transition, do not wait") and `0 or 5.0` is 5.0.
    # A debounce of 0 was silently becoming 5 seconds and the rule fired later
    # than the reply said it would.
    cooldown = arguments.get("cooldown_seconds")
    debounce = arguments.get("debounce_seconds")
    rule, why = build_event_rule(
        name=name, event_type=event_type, source=source, actuator=actuator,
        arguments=checked,
        match_mode=match_mode,
        substring=(arguments.get("substring") or ""),
        keywords=keywords,
        params=arguments.get("params") or {},
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS if cooldown is None else cooldown,
        debounce_seconds=(
            DEFAULT_DEBOUNCE_SECONDS if debounce is None else debounce
        ),
        retry_policy=(arguments.get("retry_policy") or RETRY_RETRYABLE).strip().lower(),
        ask_first=arguments.get("ask_first") is True,
    )
    if rule is None:
        return f"Refusing to arm {name!r}: {why or 'the rule was rejected'}. Nothing was armed."
    # A source the type cannot parse would only ever read UNAVAILABLE - refuse it
    # now, with the forms the reader accepts, so the caller can correct it.
    #
    # This asks the reader and reads its *status*, which is what it uses to mean
    # "I cannot read this source". It used to string-match the reader's prose
    # against a regex of the openings those messages happened to have, which
    # silently accepted every reader that phrased its refusal differently - five
    # verified cases armed fine and then reported SIGNAL_UNAVAILABLE on every
    # poll forever, with `list` still showing them enabled. A status is a
    # contract; English is not.
    #
    # Deliberately *not* refusing on every non-OK status: a source naming a
    # repository or unit that does not exist yet is a legitimate thing to arm
    # before it does, and the readers report that as unavailable too.
    from shani_chronoa.triggers import read_event_signal
    try:
        probe = read_event_signal(rule)
    except Exception:  # noqa: BLE001 - a reader that cannot run now may work at fire time
        probe = None
    detail = getattr(probe, "detail", "") or ""
    status = getattr(probe, "status", None)
    if status == _UNREADABLE_SOURCE and _names_the_source(detail):
        return f"Refusing to arm {name!r}: {detail}. Nothing was armed."

    try:
        store.add(rule)
    except RuleStoreError as exc:
        return f"Refusing to arm {name!r}: {exc} Nothing was armed."

    try:
        denial = EventEngine(store=store)._consent(ChronoaConfig(), rule)
    except RuleStoreError as exc:
        return (
            f"Armed {name!r}: {rule.event_type} {rule.source} -> {rule.actuator}. "
            f"Its firing history could not be opened ({exc}), so I cannot tell "
            f"you whether it would fire right now."
        )

    armed = (
        f"Armed {name!r}: {rule.event_type} on {rule.source} -> {rule.actuator}."
    )
    if denial:
        return (
            f"{armed} It would NOT fire right now: {denial} The rule is stored, "
            f"and stays inert - reporting the state, changing nothing - until "
            f"that is allowed. Use remove with this name to stop it."
        )
    return (
        f"{armed} It polls {rule.source} and acts on a change, at most once "
        f"every {rule.cooldown_seconds:g}s and once per change. Use remove with "
        f"this name to stop it."
    )


SKILLS = [Skill(name="manage_triggers", schema=SCHEMA, run=_run)]
