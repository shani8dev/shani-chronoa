"""Session permissions: answers scoped to one action, one path, or one turn.

Chronoa's consent keys are global and persistent. That is a real safety
property - a grant survives a restart, so nothing quietly expires - but it is
only one shape of answer, and it leaves two things the user cannot express:

- **"Yes, this once."** The alternative today is to switch a capability on
  permanently to do one thing, or to refuse and do it by hand.
- **"Not that one."** A boolean is all-or-nothing, so a user who wants
  Chronoa to delete a file in `~/Downloads` has no way to say so while keeping
  it away from `/etc`.

So there is a second layer, in front of the booleans rather than replacing them.
A grant recorded here lasts for this session; the booleans still decide
everything durable. The existing per-skill `_consent()` checks are untouched and
still run - this only ever adds a scoped decision on top.

## The rule shape, and why it is two-dimensional

    (action, pattern) -> decision

`action` is the kind of thing (`delete_file`, `control_service`), `pattern` is
the resource it would touch, wildcard-matched. Both matter: "delete" alone is too
coarse to be useful and too dangerous to grant, and `~/Downloads/*` alone says
nothing about what may be done to it.

Last match wins, and a session grant is a later ruleset than the standing one,
so approving something for this turn overrides the standing answer without
editing it. With no matching rule the answer is `FALL_THROUGH` - not allow, and
not deny. Denying by default here would be a second, invisible permission
system; allowing by default would defeat the booleans. Falling through means
this layer can only *add* a scoped decision, and the real authorisation stays
where it already was.

## Why `CANCEL` is not `DENY_ONCE`

Refusing one call and abandoning the turn are different acts. A denial says "not
that"; a cancel says "stop, this is going the wrong way". A model that has been
refused once and then tries three more variants of the same thing has not been
told anything it was not already told, and conflating the two is why agents
loop. So they are separate, and cancel is the one that ends the turn.

The decision is a plain function of the rule list, so it is testable without a
model, a sandbox, or a window.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass
from typing import List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)


class Decision:
    """What to do about one (action, pattern) request."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY_ONCE = "deny_once"
    DENY_SESSION = "deny_session"
    CANCEL = "cancel"
    #: No rule matched. Defer to whatever already decides - the consent keys.
    FALL_THROUGH = "fall_through"


#: Decisions that persist for the rest of the session when granted, as opposed
#: to applying to the one call.
_SESSION_SCOPED = frozenset({Decision.ALLOW_SESSION, Decision.DENY_SESSION})

_lock = threading.Lock()
#: Standing rules, narrowest last. Empty in practice until a caller registers
#: one; the point is that the mechanism exists and is exercised.
_standing: List[Tuple[str, str, str]] = []
#: Answers the user gave during this session. Later and therefore higher
#: precedence, which is what makes "yes, this once" work.
_grants: List[Tuple[str, str, str]] = []


def _matches(pattern: str, candidate: str) -> bool:
    """Wildcard match, with `*` also crossing `/`.

    fnmatch's `*` stops at a path separator, which would make `~/Downloads/*`
    fail to match a file two levels down - the opposite of what a user writing
    that pattern means.
    """
    if pattern in ("*", "**"):
        return True
    return fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(
        candidate, pattern.replace("/*", "/**"))


def add_rule(action: str, pattern: str, decision: str,
             session_only: bool = False) -> None:
    """Record a rule. Later rules win, so register specific before general.

    **An identical rule already in this bucket is not appended again.** Measured
    on the inbound gateway: a channel with a session grant appends one rule per
    approved message, so `_grants` grew by one entry per message for the whole
    session - and because `evaluate()` walks the whole list on every call, the
    cost of that growth is paid on every permission check, not just the ones
    that recorded it. Three calls on two channels produced two identical `phone`
    rows.

    Replacing rather than appending keeps this a no-op for the "last match wins"
    rule: an identical triple contributes the same answer wherever it sits in the
    list, so leaving the original where it is preserves order exactly.
    """
    with _lock:
        bucket = _grants if session_only else _standing
        entry = (action, pattern, decision)
        if entry not in bucket:
            bucket.append(entry)
            logger.info("Permission rule: %s %s -> %s%s", action, pattern,
                        decision, " (this session)" if session_only else "")
        else:
            logger.debug("Permission rule already recorded: %s %s -> %s", action,
                         pattern, decision)


def clear(session_only: bool = False) -> None:
    with _lock:
        if session_only:
            _grants.clear()
        else:
            _standing.clear()
            _grants.clear()
        _reasons.clear()


def reasons() -> dict:
    """The reasons the user gave this session, keyed (action, pattern)."""
    with _lock:
        return dict(_reasons)


def rejection_reason(action: str, resource: Optional[str]) -> str:
    """Why the user refused this, if they said."""
    with _lock:
        return _reasons.get((action, resource or "*"), "")


def rules() -> List[Tuple[str, str, str]]:
    with _lock:
        return list(_standing) + list(_grants)


def evaluate(action: str, resource: str) -> str:
    """The decision for touching `resource` with `action`.

    No match is `FALL_THROUGH`, not a yes: this layer is a scoped addition to
    the consent keys, not a replacement for them.
    """
    decision = Decision.FALL_THROUGH
    for rule_action, pattern, rule_decision in rules():
        if rule_action not in (action, "*"):
            continue
        if not _matches(pattern, resource):
            continue
        decision = rule_decision
    return decision


def permits(action: str, resource: str) -> Tuple[bool, Optional[str]]:
    """(allowed, refusal). A fall-through allows, because the booleans decide.

    Returning True here does not mean the action is authorised - it means this
    layer has no objection. The caller still runs the skill's own gate, so a
    session grant can only ever *widen* what the user has already agreed to
    this session, never bypass a setting they have left off.

    The refusal text carries the user's own reason when there is one, so a
    declined call tells the model *why* and it can do something else instead of
    re-proposing the same thing.
    """
    decision = evaluate(action, resource)
    if decision in (Decision.ALLOW_ONCE, Decision.ALLOW_SESSION):
        return True, None
    if decision in (Decision.DENY_ONCE, Decision.DENY_SESSION, Decision.CANCEL):
        scope = ("for the rest of this session" if decision in _SESSION_SCOPED
                 else "this time")
        refused = decision == Decision.CANCEL
        said = rejection_reason(action, resource)
        return False, (
            f"Permission {'cancelled' if refused else 'denied'} {scope}: a rule "
            f"for {action} on {resource} says {decision}. "
            + (f"The user said: {said} " if said else "")
            + ("Do not try a variant of it." if refused else
               "Do something else instead, or ask the user what to do instead.")
        )
    return True, None


def session_grant(action: str, resource: str) -> Optional[str]:
    """A grant the *user gave this session* that covers this request, if any.

    Deliberately reads only the `_grants` bucket. A standing rule can narrow
    what is allowed and must never be able to re-open a door the user closed -
    config saying "allow" is not the user saying yes. Only an answer collected
    during this session counts as consent here.

    A one-shot grant is consumed by being returned, so "yes, this once" means
    once: the next identical call finds nothing and asks again.
    """
    with _lock:
        decision = Decision.FALL_THROUGH
        index = -1
        for i, (rule_action, pattern, rule_decision) in enumerate(_grants):
            if rule_action not in (action, "*"):
                continue
            if not _matches(pattern, resource):
                continue
            decision, index = rule_decision, i
        if decision not in (Decision.ALLOW_ONCE, Decision.ALLOW_SESSION):
            return None
        if decision == Decision.ALLOW_ONCE:
            # Drop it now, not after the call returns, so a concurrent second
            # call cannot also see it.
            del _grants[index]
    return decision


#: The three answers, in the order they are offered. The last is the safe
#: default and is also what a dismissal, a timeout or no presenter resolves to.
ALLOW_ONCE_CHOICE = "Allow this once"
ALLOW_SESSION_CHOICE = "Allow for this session"
DENY_CHOICE = "No, don't allow"

#: codex's `Cancel`/`Abort`, which is a different act from `DENY_CHOICE`: a
#: denial says "not that" and the turn carries on so the model can adapt, this
#: ends the turn. `cancel_requested()` tells them apart afterwards, and without
#: this answer nothing can ever write a `Decision.CANCEL`.
CANCEL_CHOICE = "No, and stop this turn"

#: How long to wait for the user's answer before treating it as a refusal.
DECISION_TIMEOUT_SECONDS = 120.0

#: Separates a rejection's free-text reason from the choice it accompanies. The
#: answer channel is a single string, so the reason travels inside it; a newline
#: is unambiguous because no choice constant contains one.
REASON_SEPARATOR = "\n"

#: The three stages, named after opencode's `permission.tsx`. Each exists
#: because the stage before it leaves something specific unsaid: what is about to
#: happen, exactly what "always" would persist and for how long, and the fact
#: that declining can carry a reason the model can act on.
STAGE_PERMISSION = "permission"
STAGE_ALWAYS = "always"
STAGE_REJECT = "reject"


def encode_rejection(message: str = "") -> str:
    """The string a presenter resolves with for "no", carrying the reason.

    Optional by construction: an empty message is exactly `DENY_CHOICE`, so a
    presenter with nowhere to type has nothing to do differently.
    """
    reason = (message or "").strip()
    return f"{DENY_CHOICE}{REASON_SEPARATOR}{reason}" if reason else DENY_CHOICE


class Reply(NamedTuple):
    """What the user actually said, separated into a decision and a reason.

    opencode sends `{reply: "reject", message}`; this is the same pair. Keeping
    the reason beside the decision rather than inside it is what lets a decline
    carry "use the trash instead" without the refusal machinery having to parse
    prose back out of a label.
    """

    reply: str
    message: str = ""

    @property
    def is_refusal(self) -> bool:
        return self.reply in (Decision.DENY_ONCE, Decision.DENY_SESSION,
                              Decision.CANCEL)


def parse_reply(answer: str) -> Reply:
    """Turn whatever the presenter handed back into a decision and a reason.

    Anything unrecognised is a rejection - including the empty string, which is
    what a dismissal, a timeout and a missing presenter all resolve to. Escape
    therefore takes the same path as pressing "no", and this is the only place
    that mapping exists: "is there a path where an unknown answer becomes an
    allow" has to have one answer.
    """
    text = answer if isinstance(answer, str) else ""
    head, separator, message = text.partition(REASON_SEPARATOR)
    head, message = head.strip(), message.strip()
    if head == ALLOW_ONCE_CHOICE:
        return Reply(Decision.ALLOW_ONCE, message)
    if head == ALLOW_SESSION_CHOICE:
        return Reply(Decision.ALLOW_SESSION, message)
    if head == CANCEL_CHOICE:
        return Reply(Decision.CANCEL, message)
    # DENY_CHOICE and every non-answer land here together, on purpose.
    return Reply(Decision.DENY_SESSION, message)


def always_patterns(action: str, resource: Optional[str]) -> List[Tuple[str, str]]:
    """The exact (action, pattern) pairs an "allow for this session" would write.

    Returned rather than described in prose so what the prompt claims and what
    `add_rule()` records cannot drift.
    """
    return [(action, resource or "*")]


def scope_is_narrow(action: str, resource: Optional[str]) -> bool:
    """Whether a session grant would cover exactly one target rather than all.

    False is the case that matters. Nine of the twelve consent-gated tools name
    no path, unit or device, so their grant is written against `*` and covers
    *every* call to that tool for the session - a much larger permission than
    the button implies.
    """
    return bool(resource) and not any(c in resource for c in "*?[")


@dataclass(frozen=True)
class ApprovalRequest:
    """One permission question, composed as its three stages.

    Built here and rendered wherever a presenter lives, because what must not be
    dropped - the specific target, the exact patterns, the lifetime, the fact
    that Escape means no - is an obligation on the *content*, not on a dialog
    implementation. A presenter showing `question` and `options` cannot omit them.
    """

    action: str
    resource: Optional[str]
    consent_key: str
    describe: str = ""

    @property
    def subject(self) -> str:
        """What is about to happen, in enough detail to judge it.

        Says plainly when there is no specific target, because a sentence that
        simply omits the object reads as "nothing in particular" rather than
        "everything it can reach".
        """
        action = self.describe or self.action
        if self.resource:
            return f"{action}: {self.resource}"
        return (f"{action} - no specific target, this action is decided by what "
                "it does when it runs")

    @property
    def narrow(self) -> bool:
        return scope_is_narrow(self.action, self.resource)

    @property
    def always_lines(self) -> List[str]:
        """The scope of a session grant, enumerated rather than implied."""
        patterns = always_patterns(self.action, self.resource)
        if self.narrow:
            return [f"Allowing for this session covers {self.action} on "
                    f"{self.resource} only - no other target, no other action."]
        return [f"Allowing for this session covers EVERY {self.action} for the "
                f"rest of the session ({', '.join(p for _, p in patterns)} "
                "matches any target), because this action names no specific one."]

    @property
    def lifetime_line(self) -> str:
        return ("It lasts until Chronoa quits. Nothing is written to disk and "
                "nothing survives a restart - deliberately narrower than a "
                "permanent setting, and the way to make it permanent is the "
                f"'{self.consent_key}' switch in Settings.")

    @property
    def reject_line(self) -> str:
        return ("Saying no tells Chronoa why if you add a reason, so it can do "
                "something else instead. It does not end the conversation.")

    @property
    def escape_line(self) -> str:
        return ("Closing this question without answering counts as no, never as "
                "yes.")

    @property
    def options(self) -> List[str]:
        """The three answers, refusal last - ordering carries meaning, and a
        value matching none of them is a refusal, so nothing is granted by
        default."""
        return [ALLOW_ONCE_CHOICE, ALLOW_SESSION_CHOICE, DENY_CHOICE]

    def options_with_cancel(self) -> List[str]:
        """The three plus codex's Cancel, for a presenter offering the split."""
        return [ALLOW_ONCE_CHOICE, ALLOW_SESSION_CHOICE, DENY_CHOICE,
                CANCEL_CHOICE]

    @property
    def stages(self) -> List[Tuple[str, str]]:
        """The three stages as addressable units, in the order the user meets them.

        A presenter that lays the prompt out as separate widgets reads this; one
        that wants a single block reads `question`, which is composed from these
        bodies and so cannot say anything the stages do not.
        """
        return [
            (STAGE_PERMISSION, "\n".join([
                f"Shani wants to {self.subject}.",
                "",
                f"That needs the '{self.consent_key}' permission, which is "
                f"currently off. Allow it?",
            ])),
            (STAGE_ALWAYS, "\n".join([
                f"If you allow this once - {self.action} runs this time, and the "
                "next one asks again.",
                *self.always_lines,
                self.lifetime_line,
            ])),
            (STAGE_REJECT, "\n".join([self.reject_line, self.escape_line])),
        ]

    @property
    def question(self) -> str:
        """The composed prompt: all three stages, in order."""
        return "\n\n".join(body for _stage, body in self.stages)


def approval_request(action: str, resource: Optional[str], consent_key: str,
                     describe: str = "") -> ApprovalRequest:
    """The question `decide()` will put, as data rather than as a string."""
    return ApprovalRequest(action=action, resource=resource,
                           consent_key=consent_key, describe=describe)


#: Why the user said no, per (action, pattern). Cleared with the rules it
#: belongs to, so a reason cannot outlive the refusal that produced it.
_reasons: dict = {}


def can_ask() -> bool:
    """Whether there is anybody available to answer a permission question.

    Exposed so the dispatcher can fall through and let the skill produce its own
    refusal - which names the consent key the user could turn on - instead of
    replacing a useful message with "the user did not allow", when there was no
    user to allow anything.
    """
    from shani_chronoa import ask_bridge
    return ask_bridge.has_presenter()


def decide(action: str, resource: "str | None", consent_key: str,
           describe: str = "", offer_cancel: bool = False) -> "str | None":
    """Ask the user whether to allow one gated call, and record what they said.

    Returns the granted decision, or None when the answer was a refusal or
    nobody answered. None is the only value a caller may treat as "no", and it
    is also the result of a timeout, a dismissed prompt and a headless run -
    all four mean the same thing, which is that nobody said yes. The mapping is
    `parse_reply()`'s, so Escape, a timeout and a nonsense answer all take the
    refusal path rather than falling through to an allow by omission.

    A refusal is recorded as a *session* denial rather than simply returning
    None. Otherwise the model could retry, be refused, and be asked again on
    the next attempt - the same question four times inside one turn is how a
    prompt becomes something people click through without reading. The reason the
    user gave rides along in `reasons()` so the model can adapt instead of
    retrying the thing they objected to.

    `offer_cancel` adds codex's Cancel as a fourth answer, which is what writes
    a `Decision.CANCEL` that `cancel_requested()` can then report. It is opt-in
    because a caller that never showed the option cannot have declined to pick
    it, and a choice the user was not offered is not a choice they made.
    """
    from shani_chronoa import ask_bridge

    # An answer already on record settles it. Without this the denial written
    # at the end of this function is never read, so the same question is put
    # again on the next attempt - and, because the tool loop retries, up to
    # four times inside a single turn. A prompt people click through without
    # reading is worse than no prompt.
    on_record = evaluate(action, resource or "*")
    if on_record in (Decision.DENY_ONCE, Decision.DENY_SESSION, Decision.CANCEL):
        logger.info("Permission for %s %s already refused this session",
                    action, resource)
        return None
    # **`ALLOW_SESSION` was missing here, and the option's label is a promise.**
    # The tuple above covered only the three refusals, so a session *grant* was
    # written to `_grants` and then never read: choosing "Allow for this
    # session" asked again on the very next call. Measured, with a presenter
    # that always picks the session option - three calls, three prompts, two of
    # them on the same channel:
    #
    #     rules recorded: [('submit_from_gateway', 'phone', 'allow_session'),
    #                      ('submit_from_gateway', 'phone', 'allow_session'), ...]
    #
    # So the choice existed, was offered, was recorded, and did nothing. A user
    # who picks "for this session" and is asked again immediately learns that
    # the option is a lie, which is worse than not offering it - and it is the
    # same shape as the gateway grant being a label.
    #
    # `ALLOW_ONCE` is deliberately **not** in this set: "this once" means once,
    # so the next call must ask again. That is the whole difference between the
    # two options, and it is only meaningful because the other one now works.
    if on_record == Decision.ALLOW_SESSION:
        logger.info("Permission for %s %s already granted this session",
                    action, resource)
        return Decision.ALLOW_SESSION

    if not ask_bridge.has_presenter():
        # Nobody to ask. Refusing is the only honest answer, and asking a
        # question that cannot be answered would hang the turn.
        return None

    request = approval_request(action, resource, consent_key, describe)
    reply = parse_reply(ask_bridge.ask(
        request.question,
        request.options_with_cancel() if offer_cancel else request.options,
        timeout=DECISION_TIMEOUT_SECONDS,
    ))

    if reply.reply in (Decision.ALLOW_ONCE, Decision.ALLOW_SESSION):
        add_rule(action, resource or "*", reply.reply, session_only=True)
        return reply.reply

    pattern = resource or "*"
    if reply.reply == Decision.CANCEL:
        add_rule(action, pattern, Decision.CANCEL, session_only=True)
    else:
        add_rule(action, pattern, Decision.DENY_SESSION, session_only=True)
    if reply.message:
        with _lock:
            _reasons[(action, pattern)] = reply.message
    logger.info("Permission for %s %s refused by the user: %s",
                action, resource, reply.message or "(no reason given)")
    return None


def cancel_requested(action: str, resource: str) -> bool:
    """Whether the user asked to stop the turn rather than just refuse this call."""
    return evaluate(action, resource) == Decision.CANCEL


def turn_cancelled() -> bool:
    """Whether *any* Cancel was chosen this session, for a caller that only has
    the turn and not the (action, resource) pair.

    **Wired 2026-10-06; `cancel_requested` had zero callers.** So "No, and stop
    this turn" was offered, recorded as `Decision.CANCEL`, and then **nothing
    stopped the turn** - the model saw a refusal and carried on. That is the same
    shape as `ALLOW_SESSION` being recorded and ignored, one level up: an option
    whose label promises something the program does not do.

    Session-scoped rather than per-call on purpose. The caller that needs this is
    the tool loop, which has the turn but not the `(action, resource)` key the
    decision was filed under, and "did the user say stop" is a question about the
    turn rather than about one call. Read-only: this reports, it does not clear,
    so a later tool in the same turn cannot un-cancel the turn. `clear()` is the
    only thing that forgets it.
    """
    with _lock:
        return any(decision == Decision.CANCEL
                   for _a, _p, decision in list(_standing) + list(_grants))
