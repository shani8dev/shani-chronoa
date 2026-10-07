"""The body: one register of what Chronoa is doing to the world, right now.

Every part of this program that touches the world does it through one of eight
organs, and the naming is the one `ARCHITECTURE-TARGET.md` already uses:

===========  =========================================================
organ        what it covers
===========  =========================================================
``ears``     the microphone, and anything derived from it
``eyes``     the camera, the screen, and anything read off either
``mouth``    anything Chronoa plays out loud
``skin``     the network - every request to anything that is not this machine
``nose``     the machine-state senses: what the body can feel about itself
``memory``   a percept being written, or a fact recalled
``hands``    an actuator: anything that changes the world
``brain``    a model call
===========  =========================================================

**Why a register at all, when there is already an egress log, a tool tracker
and a notification flow?** Because those answer "what happened" after the fact,
and the question this answers is "what is happening *now*" — asked by the person
sitting in front of the machine, who cannot see a log file and should not have
to. An assistant that quietly opens the camera is a different thing from one
that says so on screen while it does.

Three properties this has to have, and each is a bug that has already been
avoided here rather than designed around:

- **It cannot lie by omission.** Everything that reaches the outside world goes
  through one of these organs, so a new feature that adds a capability without
  naming its organ is visible as *that organ idle while something happens* —
  and the strip can be built to assert exactly that.
- **It cannot get stuck on.** Every entry carries a deadline. A crashed turn, a
  killed thread or a skill that never returns leaves the light on forever, and a
  permanently-lit "ears" indicator is how a privacy indicator stops being
  believed. `snapshot()` expires anything older than its deadline, so the honest
  answer after a crash is "idle", not "still listening".
- **It cannot block the thing it is watching.** This is called from audio
  callbacks, from a child's exit path and from a skill's own thread, so it takes
  a lock for microseconds, never does I/O, and never raises into its caller.

Nothing here knows about GTK. The indicator strip
(`gui/organs.py`) subscribes; a skill that has no window open still records, and
the daemon's own surface can show the same thing later.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time

from . import organism
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

#: The organs, in the order a person reads them: senses first, then output, then
#: the parts that change something. Ears before eyes because a microphone being
#: on is the one people care about most.
ORGANS = ("ears", "eyes", "mouth", "skin", "nose", "memory", "hands", "brain")

#: A noun for each, used in the indicator's own words and in the tooltip.
#: Derived from `organism.py` rather than written out here, because these two
#: were the same list twice and one of the copies would have gone stale: an
#: organ named in the inventory and missing from the strip's own words is an
#: indicator that cannot describe itself.
ORGAN_NOUNS = {
    organ.indicator: organ.gerund
    for organ in organism.INVENTORY
    if organ.indicator and organ.gerund
}

#: Where each organ's activity is drawn from. Not decoration: a strip whose
#: sources were guesses would be a lie in the same way an unlabelled light is.
ORGAN_SOURCE = {
    organ.indicator: organ.source
    for organ in organism.INVENTORY
    if organ.indicator and organ.source
}

#: How long a pulse stays lit. Long enough to be seen on a strip next to a
#: composer, short enough that a burst of requests does not leave a solid bar.
PULSE_SECONDS = 0.6

#: Default deadlines, in seconds. A capture that takes longer than this stays
#: lit because the caller passes its own; these are the ones a caller did not
#: think about. All are deliberately longer than the operation they cover, and
#: shorter than "a person stops believing the light".
DEFAULT_DEADLINE = {
    "ears": 30.0,
    "eyes": 30.0,
    "mouth": 60.0,
    "skin": 30.0,
    "nose": 20.0,
    "memory": 15.0,
    "hands": 300.0,
    "brain": 300.0,
}


@dataclass
class Activity:
    """One thing an organ is doing right now."""

    organ: str
    what: str
    detail: str = ""
    started: float = field(default_factory=time.monotonic)
    #: Seconds from `started`, not an absolute time - see `expired`.
    deadline: float = 0.0
    #: True for a *pulse*: something that had already finished by the time it was
    #: reported, flashed so it is visible at all. A pulse is never "in progress",
    #: so it can never be left hanging, and callers must not try to close it.
    pulse: bool = False
    origin: str = "user"

    def expired(self, now: Optional[float] = None) -> bool:
        """Past its deadline, so it can be dropped rather than shown forever.

        `deadline` is a *duration*, and is compared against `started` - both from
        `time.monotonic()`. Comparing a duration against the monotonic clock
        directly was the first version, and it expires everything instantly:
        monotonic has been running for a few hundred thousand seconds on any
        machine that has been up for a day, so every deadline looked long past.
        """
        if not self.deadline:
            return False
        return (now if now is not None else time.monotonic()) >= self.started + self.deadline

    def age(self) -> float:
        return max(0.0, time.monotonic() - self.started)


class Body:
    """The register. One per process; `body` below is the instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._activities: List[Activity] = []
        self._listeners: List[Callable[["Body"], None]] = []
        self._audible: Optional[Callable[[str, str], None]] = None

    # -- recording ------------------------------------------------------

    def use(self, organ: str, what: str, detail: str = "", deadline: float = 0.0,
            origin: str = "user", pulse: bool = False) -> Optional[Activity]:
        """Say that `organ` has started doing `what`.

        Returns the activity, so the caller can pass it to `done()`. An unknown
        organ is refused rather than invented: a light with no name is worse than
        no light, because there is nothing to tell the user it was.
        """
        if organ not in ORGANS:
            logger.warning("body.use(%r): not an organ; nothing recorded", organ)
            return None
        activity = Activity(organ=organ, what=what, detail=detail, pulse=pulse,
                            deadline=deadline or DEFAULT_DEADLINE.get(organ, 30.0),
                            origin=origin)
        with self._lock:
            self._prune_locked()
            self._activities.append(activity)
        self._notify()
        return activity

    def pulse(self, organ: str, what: str, detail: str = "",
              origin: str = "user") -> "Activity":
        """Report something that has *already finished*, as a brief flash.

        The distinction is not cosmetic. `use` opens an activity that a caller
        must close, and forgetting to is a light that stays on - the one failure
        an indicator must not have. A request that has already been sent, or
        speech that has already been synthesised, has no "during" to cover: the
        honest thing is a flash that expires on its own, and a separate method is
        what keeps the two from being confused. It is also why a pulse cannot be
        left behind: there is nothing to leave behind.
        """
        return self.use(organ, what, detail, deadline=PULSE_SECONDS,
                        origin=origin, pulse=True)

    def done(self, activity: Optional[Activity]) -> None:
        """Say that whatever `activity` was doing has finished."""
        if activity is None:
            return
        with self._lock:
            self._prune_locked()
            self._activities = [a for a in self._activities if a is not activity]
        self._notify()

    def done_all(self, organ: str) -> int:
        """Clear one organ - used when a stream ends without a token, e.g. the mic
        being released by a path that never called `done`."""
        with self._lock:
            self._prune_locked()
            before = len(self._activities)
            self._activities = [a for a in self._activities if a.organ != organ]
            removed = before - len(self._activities)
        if removed:
            self._notify()
        return removed

    # -- reading --------------------------------------------------------

    def snapshot(self) -> List[Activity]:
        """What is happening now, newest first. Expired entries are not included."""
        with self._lock:
            self._prune_locked()
            return sorted(self._activities, key=lambda a: a.started, reverse=True)

    def busy(self, organ: str) -> bool:
        return any(a.organ == organ for a in self.snapshot())

    def busy_organs(self) -> List[str]:
        """Which organs are lit, in `ORGANS` order."""
        lit = {a.organ for a in self.snapshot()}
        return [organ for organ in ORGANS if organ in lit]

    def describe(self, organ: str) -> str:
        """What that organ is doing, in one sentence, or that it is idle."""
        busy = [a for a in self.snapshot() if a.organ == organ]
        if not busy:
            return f"{ORGAN_NOUNS.get(organ, organ)}: idle"
        parts = [f"{a.what}{(' - ' + a.detail) if a.detail else ''}" for a in busy]
        return f"{ORGAN_NOUNS.get(organ, organ)}: " + "; ".join(parts)

    def _prune_locked(self) -> None:
        """Drop expired entries. Called with the lock held."""
        now = time.monotonic()
        self._activities = [a for a in self._activities if not a.expired(now)]

    # -- telling others -------------------------------------------------

    def subscribe(self, listener: Callable[["Body"], None]) -> Callable[[], None]:
        """Call `listener` whenever something changes. Returns an unsubscribe.

        A listener that raises is logged and ignored: this is called from audio
        callbacks and from child-process exit paths, and an indicator that takes
        the microphone down because a widget had an error is worse than an
        indicator that missed one update.
        """
        with self._lock:
            self._listeners.append(listener)

        def _remove() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return _remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener(self)
            except Exception:                          # noqa: BLE001 - see the docstring
                logger.debug("a body listener failed", exc_info=True)

    def set_audible(self, callback: Optional[Callable[[str, str], None]]) -> None:
        """Install the sound hook, or None to switch sounds off.

        Nothing here plays anything. The sound belongs to the presentation layer
        (`gui/organs.py`) because whether a sound is appropriate is a question
        about the window and the user's attention, not about the register - and
        because a background daemon must stay silent.
        """
        self._audible = callback

    def announce(self, organ: str, what: str) -> None:
        """Ask for a sound, if one is installed and the organ allows it."""
        if self._audible is not None:
            try:
                self._audible(organ, what)
            except Exception:                          # noqa: BLE001
                logger.debug("the audible hook failed", exc_info=True)


#: The one register this process uses.
body = Body()

@contextlib.contextmanager
def lit(organ: str, what: str, detail: str = "", deadline: float = 0.0):
    """Light `organ` for the duration of a `with` block, and always put it out.

    For capture paths that hold a device open themselves rather than through
    `screengrab` or `audio`: webcam recording, the scanner, the wake word's own
    microphone stream. Each of those ran with its light dark - the camera
    recording while "looking" read idle - which is the one failure an indicator
    must not have. Never raises: a light that cannot be lit must not stop the
    capture it describes, and a closed device must not stay lit.
    """
    activity = None
    try:
        activity = body.use(organ, what, detail[:80], deadline=deadline)
    except Exception:                                   # noqa: BLE001
        logger.debug("body.lit could not light %s", organ, exc_info=True)
    try:
        yield activity
    finally:
        try:
            body.done(activity)
        except Exception:                               # noqa: BLE001
            logger.debug("body.lit could not put out %s", organ, exc_info=True)
