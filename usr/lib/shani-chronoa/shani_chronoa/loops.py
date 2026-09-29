"""A turn that keeps doing the same thing is not a turn that is working.

`MAX_TOOL_ROUNDS` bounds how many times the model may act, and `MAX_TURN_SECONDS` bounds
how long those may take. Neither bounds the *work inside one round*: the tool loop runs
`for call in tool_calls`, and a backend may return as many calls as it likes there. A model
that is stuck - retrying a call whose result it does not like, or asking for the same thing
over and over because the answer never matches what it wanted - spends a full 300-second
budget doing identical work before anything notices.

This notices first, and notices cheaply.

Adopted from qwen-code's `services/loopDetectionService.ts`, with one deliberate change to
its shape. qwen-code also runs an always-on guard that *refuses* an individual repeated
call. That was tried here once and removed: refusing the second identical call broke 13
tests and real behaviour, because legitimate repeats exist. Reading a file twice after
editing it, re-checking a value that may have changed, retrying a sense that has not
settled yet - none of those are loops, and a rule that cannot tell them apart from a stuck
turn is a rule that fires on people.

So the threshold does the work instead. Repeats are *tolerated* up to
`LOOP_THRESHOLD`, and only the run itself is stopped. A model may repeat the same call five
times and every one of them executes; the sixth is where the turn is ended, with a message
saying what happened. That keeps the common cases working and still bounds the pathological
one, which is the only case worth bounding.

Why identical arguments are the signal: qwen-code's argument is the one that is actually
true regardless of harness. Repeating a call with the same name and the same arguments
returns the same result by construction, so the only reason for the fifth one is that
something upstream is wrong. Repeating a call with *different* arguments is not a loop - it
is a model narrowing a search, which is the opposite of being stuck.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: Consecutive identical calls tolerated before the run is called a loop. qwen-code uses 5
#: as well, chosen to sit below the point at which a hosted backend rejects the whole
#: conversation for repetitive calls. Five also leaves room for the legitimate repeats
#: above: a read-after-write, a re-check, a retry of something that had not settled.
LOOP_THRESHOLD = 5

#: The subject of the loop, for the message. Only the tool name, never the arguments -
#: they can be a whole file's worth of text, and this string goes on screen.
_MAX_SUBJECT_CHARS = 60


def call_key(name: str, arguments: object) -> str:
    """A stable identity for one tool call: its name and its arguments.

    Arguments are canonicalised by sorting keys, so `{"b":1,"a":2}` and `{"a":2,"b":1}` are
    the same call - a backend that reorders keys between turns has not made a new request.
    Anything unserialisable falls back to `repr`, which is stable within a process and
    still distinguishes two different calls rather than collapsing them into one key.

    Deliberately *not* part of the key: the tool call id. Every call has a unique one, so
    including it would make every call unique and detect nothing.
    """
    try:
        canonical = json.dumps(arguments, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        canonical = repr(arguments)
    return f"{name}\x00{canonical}"


class LoopDetector:
    """Counts *consecutive* identical tool calls within one turn.

    Consecutive is the whole point. `a, b, a, b` repeats each call twice but is not a loop,
    and a detector counting occurrences rather than runs would stop it and be wrong. The
    run resets on any different call.

    One detector per turn: state is deliberately not carried across turns, because a
    conversation may legitimately make the same call at the start of one turn and the start
    of the next, and only a run *within* a turn means the turn is not making progress.
    """

    def __init__(self, threshold: int = LOOP_THRESHOLD):
        self._threshold = threshold
        self._key: "str | None" = None
        self._name = ""
        self._run = 0
        self._stopped = False

    @property
    def stopped(self) -> bool:
        """True once the run has reached the threshold. Latched, so a caller that checks
        after executing a batch cannot see it flip back to False."""
        return self._stopped

    @property
    def run_length(self) -> int:
        """How many identical calls in a row the current run holds."""
        return self._run

    def repeat(self, name: str, arguments: object) -> bool:
        """Record one call. Returns whether it is an identical repeat of the one before.

        Returns the *fact*, not a verdict. A caller still executes the call; the point is
        that the run is counted so something can stop the turn when it is long enough.
        """
        key = call_key(name, arguments)
        if key == self._key:
            self._run += 1
        else:
            self._key = key
            self._name = name if isinstance(name, str) else ""
            self._run = 1
        if self._run >= self._threshold:
            if not self._stopped:
                logger.warning(
                    "Turn made %d consecutive identical '%s' calls; stopping the "
                    "turn rather than repeating the same work", self._run, self._name)
            self._stopped = True
            return True
        return self._run > 1

    def explain(self) -> str:
        """An honest account of why the turn stopped, or an empty string if it has not."""
        if not self._stopped:
            return ""
        subject = self._name[:_MAX_SUBJECT_CHARS]
        return (
            f"This turn stopped because it made {self._run} calls to `{subject}` in a "
            f"row with identical arguments, and an identical call cannot return a "
            f"different result. Repeats are allowed up to {self._threshold} because "
            f"re-reading a file you just changed, or re-checking a value that may have "
            f"moved, is normal; this was past that. Nothing further was run. If the call "
            f"was meant to change each time, vary its arguments - or if something it "
            f"reads is not what you expect, ask about that instead of retrying."
        )
