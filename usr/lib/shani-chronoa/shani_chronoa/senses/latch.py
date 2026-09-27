"""Change detection for senses that are polled on a schedule.

A sense with a `poll_interval` runs on a timer, which creates a problem no
one-shot sense has: what do you do when the answer has not changed since the
last thirty seconds? The two obvious answers are both wrong. Emitting every
poll fills the percept store with dozens of identical entries and drowns
everything else in the context budget. Emitting only on change means a state
that stays wrong forever is reported exactly once and then goes silent, which
is the failure mode that matters most - "the camera is still in use" is
precisely the fact a user needs re-reminded of.

This module is the third option: latch on change, and re-arm after a quiet
window so a persistent condition re-alerts without turning into a spam loop.
The window is what distinguishes "worth repeating" from "noise", and it is
exposed per-latch rather than as a global so a genuinely persistent
condition and a merely chatty one can be tuned independently.

State is per-process and in-memory. That is a deliberate limit rather than an
oversight: a latch is a de-duplication device for one run of the poller, and
persisting it would mean deciding what "unchanged since" means across a
reboot, which is a different and much harder question. A durable record of
observations is the percept store's job, and that already exists.
"""

import threading
import time
from typing import Any, Optional, Tuple


class Latch:
    """Emit on change, and re-emit after `rearm_seconds` of quiet.

    `should_emit(value)` returns True the first time it sees a value, again
    whenever the value differs from the last one, and again once the value has
    held unchanged for longer than the re-arm window.
    """

    def __init__(self, rearm_seconds: float = 900.0, clock=time.monotonic) -> None:
        if rearm_seconds <= 0:
            raise ValueError("rearm_seconds must be positive")
        self._rearm = float(rearm_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._last_value: Optional[Any] = None
        self._emitted_at: Optional[float] = None
        self._emitted = False

    def should_emit(self, value: Any) -> bool:
        """Whether this observation warrants a fresh percept."""
        with self._lock:
            now = self._clock()
            if not self._emitted:
                changed = True
            elif not _same(value, self._last_value):
                changed = True
            else:
                # Measured from the last time this actually *emitted*, not from
                # the last time it was looked at. Measuring from the last
                # observation means any poll more frequent than the window
                # resets the countdown every single time, so the re-arm can
                # never arrive - which is the normal case, since a sense polled
                # every 60s with a 900s window would reset forever and the
                # steady fact would be stated exactly once, ever.
                changed = (self._emitted_at is not None) and (
                    now - self._emitted_at >= self._rearm
                )
            if changed:
                self._emitted = True
                self._emitted_at = now
            self._last_value = value
            return changed

    def reset(self) -> None:
        """Forget the observation, so the next call emits unconditionally."""
        with self._lock:
            self._emitted = False
            self._last_value = None
            self._emitted_at = None


def _same(a: Any, b: Any) -> bool:
    """Equality that never raises, whatever a sense decided to return.

    A sense returning something exotic - a numpy-ish object, a value with a
    broken `__eq__` - must not be able to crash the poller that called it.
    """
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:  # noqa: BLE001 - any broken __eq__ means "not known equal"
        return False


class LatchRegistry:
    """Named latches, so several senses can share one re-arm policy."""

    def __init__(self, rearm_seconds: float = 900.0) -> None:
        self._rearm = rearm_seconds
        self._lock = threading.Lock()
        self._latches: dict = {}

    def should_emit(self, name: str, value: Any) -> bool:
        with self._lock:
            latch = self._latches.get(name)
            if latch is None:
                latch = Latch(rearm_seconds=self._rearm)
                self._latches[name] = latch
        return latch.should_emit(value)

    def reset(self, name: Optional[str] = None) -> None:
        with self._lock:
            if name is None:
                self._latches.clear()
                return
            latch = self._latches.get(name)
            if latch is not None:
                latch.reset()


def fingerprint(value: Any) -> Tuple:
    """A hashable, order-stable stand-in for an observation.

    Senses return lists and dicts, which cannot be stored as "the last value"
    and compared usefully, so a latch that takes a fingerprint will not
    silently never fire on change.
    """
    if isinstance(value, dict):
        return ("d", tuple(sorted((k, fingerprint(v)) for k, v in value.items())))
    if isinstance(value, (list, tuple)):
        return ("l", tuple(fingerprint(v) for v in value))
    if isinstance(value, set):
        return ("s", tuple(sorted(str(v) for v in value)))
    try:
        hash(value)
    except TypeError:
        return ("r", repr(value))
    return ("v", value)
