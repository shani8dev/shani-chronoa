"""Watch the long-running subprocesses Chronoa starts, and say exactly what state each is in.

Live through `audio.py`: the `pw-record` capture and the `pw-play` playback,
the two children Chronoa runs for minutes at a time. Both were unwatched once,
and the failure is silent in a way that costs the user the feature: a capture
whose child wedges leaves the capture loop blocked inside `read()` for ever -
it only checks its deadline *between* reads - so the orb stays "listening" and
every later turn is refused with nothing saying why. That is the
untracked-subprocess-lifetime defect class AGENTS.md records twice (the player
and the sandbox executor).

This module was `gateway_supervisor.py`, which also modelled "external agents"
reaching Chronoa over a channel Chronoa does not have: `register_agent`,
`broadcast_message` and friends had no caller, and `broadcast_message` reported
deliveries for messages that went nowhere. That half was removed in the
2026-10-02 structure review (CHRONOA-HARVEST.md Part 8); what remains is the
part that runs.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ChildState(Enum):
    """The four things a supervised child can be - four different faults, never a boolean."""

    STARTING = "starting"          # alive, has not reported yet
    IDLE = "idle"                  # reported within its silence budget: healthy
    DISCONNECTED = "disconnected"  # alive but silent past the budget: hung
    EXITED = "exited"              # gone, with its exit status


@dataclass(frozen=True)
class HeartbeatStatus:
    """What is actually true of one supervised thing right now.

    `state` is a single `ChildState`, never a boolean, because the callers that need
    this cannot act on a boolean: releasing a wedged capture is safe, waiting longer
    for a hung one is pointless, and neither is right for a child that has not spoken
    yet. `age` is `None` exactly when nothing has ever reported, which is the only
    honest way to say "no heartbeat yet" - a freshly registered thing that stamps its
    own clock looks identical to a live one forever after.
    """

    name: str
    state: ChildState
    age: Optional[float]
    pid: Optional[int]
    exit_status: Optional[int]
    detail: str


class _Child:
    """A tracked subprocess: how to ask whether it is alive, and when it last spoke.

    `registered_at` is not cosmetic. A child that wedges *before its first byte* has no
    heartbeat to age, so a supervisor that only ever compares against the last one calls
    that state `STARTING` for ever - the one failure it exists to catch, reported as
    patience. The budget is therefore measured from whichever is later: the first
    heartbeat, or the moment the child was tracked.
    """

    def __init__(
        self,
        probe: Callable[[], Optional[int]],
        pid: Optional[int],
        stall_after: Optional[float],
    ) -> None:
        self.probe = probe
        self.pid = pid
        self.stall_after = stall_after
        self.registered_at = time.monotonic()
        self.heartbeat: Optional[float] = None


class ChildSupervisor:
    """Tracks named subprocesses: their liveness probe, last heartbeat and silence budget."""

    def __init__(self) -> None:
        self._children: dict[str, _Child] = {}

    def track_child(
        self,
        name: str,
        probe: Callable[[], Optional[int]],
        *,
        pid: Optional[int] = None,
        stall_after: Optional[float],
    ) -> None:
        """Start watching a subprocess under `name`.

        `probe` returns `None` while the child is running and its exit status once it
        is not - which is `subprocess.Popen.poll` exactly, so a caller passes `proc.poll`
        and this module never needs to know the child is a process.

        `stall_after` is required, and deliberately not defaulted, because the two
        kinds of child need opposite answers and getting it wrong fails in the
        expensive direction. A number is a silence budget: no bytes for this long while
        the child is alive is a hang. `None` means there is no byte stream to starve -
        liveness is settled by the exit status alone, which is the honest reading for a
        child like `pw-play` that this codebase waits on with one bounded `wait()`.
        Defaulting it would mean one of the two is silent about its own failure.

        Re-tracking a name that is still tracked logs the pid being displaced. That is
        the untracked-subprocess bug in one line: a caller who starts a second child
        under a name it forgot to release has just lost the ability to reach the first.
        """
        displaced = self._children.get(name)
        if displaced is not None:
            logger.warning(
                "Tracking %r over a child that was still registered (pid %s); "
                "anything that could still reach it no longer can",
                name,
                displaced.pid,
            )
        self._children[name] = _Child(probe, pid, stall_after)

    def forget_child(self, name: str) -> None:
        """Stop watching a child, because its life ended the way it was meant to.

        Only for an orderly end. A child that failed is left tracked on purpose, so
        `check_heartbeats` can still be asked what happened to it after the owner has
        moved on.
        """
        if self._children.pop(name, None) is not None:
            logger.debug("Stopped tracking %r", name)

    def update_heartbeat(self, name: str) -> bool:
        """Record proof of life for a tracked child; False (and a warning) for a name never tracked.

        A silent no-op here once made an unwatched child indistinguishable from a
        watched one - the shape of the defect that let `_run_host` report success
        over a process nobody was tracking.
        """
        child = self._children.get(name)
        if child is not None:
            child.heartbeat = time.monotonic()
            return True
        logger.warning("Heartbeat for %r, which is not a tracked child", name)
        return False

    def check_heartbeats(self) -> list[HeartbeatStatus]:
        """Every tracked child with the state it is in - including the healthy ones.

        Reporting only the sick ones would make "nothing is wrong" look like
        "nothing is being watched". STARTING is patience, IDLE is health,
        DISCONNECTED is a hang (waiting will not help), EXITED is death (with
        its status) - and the caller acts differently on each.
        """
        monotonic_now = time.monotonic()
        statuses: list[HeartbeatStatus] = []

        for name, child in self._children.items():
            status = child.probe()
            age = None if child.heartbeat is None else monotonic_now - child.heartbeat
            # Time the budget is measured against: the last report, or the moment the
            # child was tracked if it never reported one. See `_Child`.
            quiet_for = monotonic_now - max(
                child.registered_at, child.heartbeat or child.registered_at
            )
            if status is not None:
                state = ChildState.EXITED
                detail = f"{name} (pid {child.pid}) exited with status {status}"
            elif child.stall_after is not None and quiet_for > child.stall_after:
                state = ChildState.DISCONNECTED
                if age is None:
                    detail = (
                        f"{name} (pid {child.pid}) has never reported anything in the "
                        f"{quiet_for:.1f}s since it was started (budget "
                        f"{child.stall_after:.1f}s)"
                    )
                else:
                    detail = (
                        f"{name} (pid {child.pid}) has produced nothing for {age:.1f}s, "
                        f"past its {child.stall_after:.1f}s budget"
                    )
            elif age is None:
                state = ChildState.STARTING
                detail = f"{name} (pid {child.pid}) is running but has not reported anything yet"
            else:
                state = ChildState.IDLE
                detail = f"{name} (pid {child.pid}) reported {age:.1f}s ago"
            statuses.append(
                HeartbeatStatus(
                    name=name,
                    state=state,
                    age=age,
                    pid=child.pid,
                    exit_status=status,
                    detail=detail,
                )
            )

        return statuses
