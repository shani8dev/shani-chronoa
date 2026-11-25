"""Awake / active / idle / sleep rhythm for the daemon.

The daemon keeps its rules and senses running in every state. What changes with
the hour and with presence is *how* it runs them: an active machine may act
freely, an idle one holds announcements, and a sleeping one only maintains.
This module is that state machine, as plain data so a test can drive it without
a clock, a display or a window.

It is deliberately not `presence.Presence`. Presence answers "is the model
resident" (a question about memory) and the person's choice to sleep it. Rhythm
answers "what is the machine doing right now", from idle time, the screen lock
and sleep/wake events. Confusing the two is how a chip comes to report "Asleep"
about a machine that is answering questions.

Nothing here acts. `observe` returns a state; `percept` renders it for the
percept store; the daemon decides what to do with it.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Optional

from shani_chronoa.senses import SENSITIVITY_PERSONAL, Percept

#: A person using the machine right now. The window itself treats twenty
#: seconds as a spoken turn; five is the shortest idle reading that still
#: means "somebody is here" rather than "the pointer stopped moving".
ACTIVE_IDLE_SECONDS = 5.0
#: No input for this long means the room is quiet: hold announcements.
IDLE_AFTER_SECONDS = 300.0
#: No input and a locked screen for this long means sleep: maintenance only.
SLEEP_AFTER_SECONDS = 1800.0


class Rhythm(enum.Enum):
    ACTIVE = "active"
    AWAKE = "awake"
    IDLE = "idle"
    SLEEP = "sleep"
    DREAM = "dream"
    MAINTENANCE = "maintenance"


@dataclass(frozen=True)
class RhythmState:
    state: Rhythm
    since: float = 0.0
    idle_seconds: float = 0.0

    def with_state(self, state: Rhythm, now: float) -> "RhythmState":
        return RhythmState(state, now, self.idle_seconds)


def observe(
    previous: RhythmState,
    *,
    idle_seconds: Optional[float] = None,
    locked: bool = False,
    asleep: bool = False,
    dream: bool = False,
    maintenance: bool = False,
    now: Optional[float] = None,
) -> RhythmState:
    """The next rhythm state from the signals that are actually known.

    Precedence is the design, not an `if` chain someone can reorder by accident:

    1. the machine is asleep, or presence says sleep -> SLEEP, whatever else;
    2. a maintenance or dream pass was requested -> that state, but never over
       an asleep machine (a dream is a wake-time activity here);
    3. recent input -> ACTIVE;
    4. a locked screen and long idle -> SLEEP;
    5. long idle -> IDLE;
    6. otherwise AWAKE.

    An unknown idle time is treated as *not idle*: it falls through to AWAKE
    rather than inventing a quiet room the way `0` would invent a person.
    """
    moment = time.time() if now is None else now
    idle = (
        previous.idle_seconds if idle_seconds is None else max(0.0, float(idle_seconds))
    )
    # Keep the previous transition time: an observation that changes nothing
    # must not move "since", or a state that never changes looks freshly entered.
    state = RhythmState(previous.state, previous.since, idle)

    if asleep:
        target = Rhythm.SLEEP
    elif maintenance:
        target = Rhythm.MAINTENANCE
    elif dream:
        target = Rhythm.DREAM
    elif idle <= ACTIVE_IDLE_SECONDS:
        target = Rhythm.ACTIVE
    elif locked and idle >= SLEEP_AFTER_SECONDS:
        target = Rhythm.SLEEP
    elif idle >= IDLE_AFTER_SECONDS:
        target = Rhythm.IDLE
    else:
        target = Rhythm.AWAKE
    if target == state.state:
        return state
    return state.with_state(target, moment)


def should_run_rules(state: Rhythm) -> bool:
    """Always True. The daemon never stops its rules to save a state.

    A state that stopped the engine would make an armed rule silently missing
    every time the machine happened to be idle - which is exactly when most
    background rules are meant to fire. Interrupt policy may *hold an
    announcement*; it may not stop a rule.
    """
    return True


#: The last state the owner of the monitor observed. Plain data, not a lock:
#: unattended announcement policy reads it from another thread and a stale
#: reading can only hold an announcement for one tick, never grant one.
_CURRENT_STATE = Rhythm.AWAKE


def current_state() -> Rhythm:
    """The last observed rhythm state, for interrupt policy. Never raises."""
    return _CURRENT_STATE


def note_state(state) -> None:
    """Record an observed state. Accepts a `Rhythm` or a `RhythmState`."""
    global _CURRENT_STATE
    value = getattr(state, "state", state)
    if isinstance(value, Rhythm):
        _CURRENT_STATE = value


def percept(state: RhythmState, now: Optional[float] = None) -> Percept:
    """Render a state as a transient percept for the context builder."""
    moment = time.time() if now is None else now
    return Percept(
        sense="rhythm",
        kind="state",
        content=f"rhythm: {state.state.value}",
        created_at=moment,
        ttl_seconds=60.0,
        source="rhythm.observe",
        sensitivity=SENSITIVITY_PERSONAL,
        metadata={"since": state.since, "idle_seconds": state.idle_seconds},
    )


class RhythmMonitor:
    """The daemon's small holder of one state and one percept store.

    `tick` is the only method the daemon calls. It reads the idle clock through
    `read_idle` (injectable so a test needs no X display), observes the next
    state, and publishes a percept only on a transition - a percept deposited
    every second would push the real percepts out of the transient window.
    """

    def __init__(self, store=None, read_idle=None):
        self.store = store
        self.read_idle = read_idle
        # An unknown clock is not "somebody just touched the machine": with 0
        # the first tick would declare ACTIVE on a headless daemon. Start just
        # outside the active window so an unreadable idle reading stays AWAKE.
        self.state = RhythmState(Rhythm.AWAKE, time.time(), ACTIVE_IDLE_SECONDS + 1.0)

    def tick(self, **signals) -> RhythmState:
        if "idle_seconds" not in signals and self.read_idle is not None:
            try:
                idle = self.read_idle()
            except Exception:  # noqa: BLE001
                idle = None
            if idle is not None:
                signals["idle_seconds"] = idle
        before = self.state.state
        self.state = observe(self.state, **signals)
        note_state(self.state)
        if self.store is not None and self.state.state is not before:
            try:
                self.store.add(percept(self.state))
            except Exception:  # noqa: BLE001
                # A percept that cannot be stored must not stop the daemon.
                pass
        return self.state
