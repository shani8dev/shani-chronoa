"""Reward from outcomes: a record, never an authority.

The learning layer already records what happened to a tool call - its
post-condition verdict, whether it was approved or denied, and (elsewhere) a
person undoing it or correcting it. What it did not have was a single number
that says "this outcome went well". A reward is that number.

It is deliberately advisory. Nothing here grants consent, reorders a whitelist
or decides a tool may run: the standing rule is that authority stays with the
consent keys. `reward_for` reads an outcome dict and returns a float in
[-1, 1]; `summary` aggregates a history without hiding the unmeasured cases.
"""

from __future__ import annotations

from typing import Iterable

#: The weights are a policy, written in one table so a caller cannot disagree
#: with itself about what a denial is worth.
WEIGHTS = {
    "verified": 1.0,
    "failed": -1.0,
    "unverified": 0.0,
    "impossible": 0.0,
    "approved": 0.5,
    "denied": -0.5,
    "undone": -1.0,
    "corrected": -0.5,
}


def _flag(outcome: dict, key: str) -> bool:
    value = outcome.get(key)
    return value is True or (
        isinstance(value, str) and value.lower() in ("yes", "true")
    )


def reward_for(outcome: dict) -> float:
    """One number for one recorded outcome, clamped to [-1, 1].

    A *verified* effect is the only positive evidence that a change held;
    an unverified run is neutral rather than a small success, because most
    calls on a real machine are unverified and counting them as wins would
    make the signal meaningless. An undo or a correction is the strongest
    negative - it is a person saying the result was not what they wanted.

    An outcome with no verdict at all is 0.0: "nothing recorded" must not read
    as either success or failure.
    """
    if not isinstance(outcome, dict):
        return 0.0
    value = WEIGHTS.get(str(outcome.get("verdict", "")).lower(), 0.0)
    if _flag(outcome, "approved"):
        value += WEIGHTS["approved"]
    if _flag(outcome, "denied"):
        value += WEIGHTS["denied"]
    if _flag(outcome, "undone"):
        value += WEIGHTS["undone"]
    if _flag(outcome, "corrected"):
        value += WEIGHTS["corrected"]
    return min(1.0, max(-1.0, value))


def summary(outcomes: Iterable[dict]) -> dict:
    """Aggregate without turning an unmeasured history into a confident mean.

    `measured` counts only outcomes that carry a verdict; the mean is over
    those. `total` is kept beside it so a history of nine unknowns and one
    verified call cannot read as a perfect score.
    """
    values = []
    measured = 0
    total = 0
    for outcome in outcomes or ():
        total += 1
        if isinstance(outcome, dict) and outcome.get("verdict"):
            measured += 1
            values.append(reward_for(outcome))
    return {
        "total": total,
        "measured": measured,
        "unmeasured": total - measured,
        "mean": (sum(values) / len(values)) if values else None,
    }
