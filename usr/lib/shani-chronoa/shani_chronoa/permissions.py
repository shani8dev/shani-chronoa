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
from typing import List, Optional, Tuple

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
    """Record a rule. Later rules win, so register specific before general."""
    with _lock:
        bucket = _grants if session_only else _standing
        bucket.append((action, pattern, decision))
        logger.info("Permission rule: %s %s -> %s%s", action, pattern, decision,
                    " (this session)" if session_only else "")


def clear(session_only: bool = False) -> None:
    with _lock:
        if session_only:
            _grants.clear()
        else:
            _standing.clear()
            _grants.clear()


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
    """
    decision = evaluate(action, resource)
    if decision in (Decision.ALLOW_ONCE, Decision.ALLOW_SESSION):
        return True, None
    if decision in (Decision.DENY_ONCE, Decision.DENY_SESSION, Decision.CANCEL):
        scope = ("for the rest of this session" if decision in _SESSION_SCOPED
                 else "this time")
        return False, (
            f"Permission denied {scope}: a rule for {action} on {resource} says "
            f"{decision}. Ask the user whether to allow it, or do something else."
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


def cancel_requested(action: str, resource: str) -> bool:
    """Whether the user asked to stop the turn rather than just refuse this call."""
    return evaluate(action, resource) == Decision.CANCEL
