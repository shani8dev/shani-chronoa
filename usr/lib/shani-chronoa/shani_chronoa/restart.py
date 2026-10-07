"""Bounded restart decisions for supervised children.

Chronoa starts its own long-lived helpers (llama-server, the parakeet STT
server, pw-record/pw-play through `child_supervisor.py`), and nothing today
owns "should this one be started again, and how hard do we push on it?".
assistd's supervised-child policy (`assistd-utils/src/backoff.rs:46-97`) is
the reference: restart delays twin a doubling schedule, a session that lives
at least briefly clears the consecutive-failure counter, and a rolling window
caps how many restarts are allowed before the correct answer is *stop*, both
answering restart *storms* that a plain backoff cannot - a failure every
minute is not a 1-2-4-8-16 problem, it is a service that can never stay up.

A small pure decision function, no threads: the caller wires it to whatever
restarts the child. process is not required, and a decision's reason is
carried for the log line so a refusal is never silent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, NamedTuple, Optional

#: Backoff schedule per consecutive failure, in seconds. The last one repeats.
CHILD_BACKOFFS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0)
#: A run lasting at least this long means the failure is new information,
#: not a crash loop: starting the counter over at zero is what separates
#: "this service is broken" from "a thing failed yesterday".
_MIN_HEALTHY_SECONDS = 30.0
#: At most this many restarts inside the rolling window before the policy
#: refuses outright rather than pacing another one.
_WINDOW_SECONDS = 300.0
_WINDOW_MAX_RESTARTS = 5
_MAX_CONSECUTIVE = 7


class RestartDecision(NamedTuple):
    """What to do about one failed child.

    `delay` is how long to wait before starting it again; `refusal` is the
    honest reason to leave it down instead. One of the two is meaningful.
    """

    delay: Optional[float]
    refusal: Optional[str]


@dataclass
class RestartPolicy:
    """Tracks recent restarts and consecutive failures for one child."""

    backoffs: tuple = CHILD_BACKOFFS
    window_seconds: float = _WINDOW_SECONDS
    window_max: int = _WINDOW_MAX_RESTARTS
    max_consecutive: int = _MAX_CONSECUTIVE
    min_healthy: float = _MIN_HEALTHY_SECONDS
    _consecutive_failures: int = 0
    _healthy_since: Optional[float] = None
    _restarts: List[float] = field(default_factory=list)

    def note_started(self, now: Optional[float] = None) -> None:
        """The child is up; the clock it might fail later is stamped now."""
        self._healthy_since = now if now is not None else time.monotonic()

    def note_exit(self, now: Optional[float] = None) -> RestartDecision:
        """One exit. `failed` means the exit was not a clean shutdown."""
        now = now if now is not None else time.monotonic()
        self._restarts = [t for t in self._restarts
                          if now - t < self.window_seconds]
        # A long enough healthy run clears the consecutive counter: one bad
        # exit after hours is a new incident, not a loop.
        if self._healthy_since is not None and now - self._healthy_since >= self.min_healthy:
            self._consecutive_failures = 0
        self._consecutive_failures += 1
        self._healthy_since = None

        if self._consecutive_failures > self.max_consecutive:
            return RestartDecision(None,
                                   f"consecutive failure cap {self.max_consecutive} reached")
        if len(self._restarts) >= self.window_max:
            return RestartDecision(None,
                                   f"more than {self.window_max} restarts inside "
                                   f"{self.window_seconds:.0f}s - not a one-off crash")
        self._restarts.append(now)
        delay = self.backoffs[min(self._consecutive_failures - 1, len(self.backoffs) - 1)]
        return RestartDecision(delay, None)
