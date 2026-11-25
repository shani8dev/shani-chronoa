"""Salience scoring, a priority queue and an interrupt policy.

The trigger engine fires every notable event the same way. Attention is a
different question: of the things that happened, which deserves to break into
someone's day *now*, which should wait for a notification, and which can be
held until the next turn? This module answers it with plain data so it can be
tested without a window or an announcement.

Nothing here speaks. `policy` returns a word; the caller decides what a word
means, and the existing do-not-disturb switch and rhythm state are the gates.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: Severity words a percept or event may carry. Unknown words are not severe:
#: a caller cannot invent urgency by passing a string nobody declared.
SEVERITY = {
    "info": 0.0,
    "notice": 1.0,
    "warning": 2.0,
    "critical": 3.0,
}

#: Change is worth up to two points, severity up to three, and a person's own
#: rule is worth five - a rule is an explicit statement of what matters to them.
CHANGE_WEIGHT = 2.0
SEVERITY_WEIGHT = 1.0
RULE_WEIGHT = 5.0

SPEAK_ABOVE = 6.0
NOTIFY_ABOVE = 2.0


@dataclass(order=True)
class Scored:
    """One item in the queue. Higher score comes out first.

    `order` is the tie-break: a stable queue never lets a later event of equal
    score overtake an earlier one, which is how a burst turns into a reordering
    nobody asked for.
    """

    neg_score: float
    order: int
    item: object = field(compare=False)


def score(
    item: object,
    *,
    changed: bool = False,
    severity: str = "info",
    rules: Iterable[str] = (),
) -> float:
    """A number in [0, 10]. `item` is opaque; only its signals are scored.

    A `True` rule match is a person's own priority rule naming this item, so it
    dominates. Unknown severity contributes nothing rather than raising, so a
    malformed percept is quiet rather than loud.
    """
    value = CHANGE_WEIGHT if changed else 0.0
    value += SEVERITY_WEIGHT * SEVERITY.get(severity, 0.0)
    name = getattr(item, "sense", None) or getattr(item, "event", None)
    if name is None and isinstance(item, str):
        name = item
    if isinstance(name, str) and any(rule == name for rule in rules):
        value += RULE_WEIGHT
    return min(10.0, max(0.0, value))


class PriorityQueue:
    """A bounded max-heap of `Scored` items.

    Bounded on purpose: an attention queue that grows without limit keeps a
    stale event forever and eventually speaks about yesterday. Drop the lowest
    item when full, never the newest one - the newest is the one still true.
    """

    def __init__(self, capacity: int = 64):
        self.capacity = max(1, int(capacity))
        self._heap: list[Scored] = []
        self._order = 0

    def __len__(self) -> int:
        return len(self._heap)

    def push(self, item: object, value: float) -> None:
        entry = Scored(-float(value), self._order, item)
        self._order += 1
        if len(self._heap) < self.capacity:
            heapq.heappush(self._heap, entry)
            return
        # The heap root is the *highest*-value item (most negative score), so
        # heapreplace would drop the one we most want to keep. Find the lowest
        # by scanning; the capacity is small and correctness beats a microsecond.
        # neg_score is inverted: the lowest-value item has the largest neg_score.
        lowest = max(range(len(self._heap)), key=lambda i: self._heap[i].neg_score)
        if entry.neg_score < self._heap[lowest].neg_score:
            self._heap[lowest] = entry
            heapq.heapify(self._heap)

    def pop(self) -> Optional[object]:
        if not self._heap:
            return None
        return heapq.heappop(self._heap).item

    def peek(self) -> Optional[object]:
        return self._heap[0].item if self._heap else None


def policy(
    value: float,
    *,
    do_not_disturb: bool = False,
    rhythm_state: Optional[object] = None,
    unattended: bool = False,
    critical: bool = False,
) -> str:
    """`speak`, `notify` or `hold`.

    Do-not-disturb is a person's explicit refusal to be interrupted and always
    wins, including over a critical event. Rhythm is different: it holds only
    the states in which the machine is already doing work the announcement
    would interrupt, and IDLE is deliberately not one of them - a person five
    minutes at a standing desk is not a reason to hold an alert for an hour.
    The vocabulary comes from `rhythm.should_run_rules`, so policy and the
    rule gate cannot disagree about which states are quiet. A critical event
    bypasses rhythm but never DND. `unattended` means no person is at the
    desk, so a high score still only notifies.
    """
    from shani_chronoa import rhythm

    state = getattr(rhythm_state, "state", rhythm_state)
    if isinstance(state, str):
        try:
            state = rhythm.Rhythm(state)
        except ValueError:
            state = None
    quiet = not rhythm.should_run_rules(state)
    if do_not_disturb:
        return "hold"
    if quiet and not critical:
        return "hold"
    if value >= SPEAK_ABOVE and not unattended:
        return "speak"
    if value >= NOTIFY_ABOVE:
        return "notify"
    return "hold"
