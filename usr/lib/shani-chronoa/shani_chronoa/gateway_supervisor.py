"""Gateway Supervisor Architecture for shani-chronoa.

Two halves, and only one of them runs. Which is which is said here rather than left to
be inferred from import counts, because this file spent its entire life unreferenced and
`AGENTS.md` records four modules in this repo that were fully built, fully unit-tested,
and wired to nothing.

**Multi-agent: not wired, deliberately kept.** `register_agent`, `unregister_agent`,
`get_agent_status` and `broadcast_message` model external agents reaching Chronoa over
a channel. Chronoa has no such channel - it is one process, one microphone and one user,
and `broadcast_message` returns a count of recipients for a message it never delivers,
so a caller trusting it would be told "sent to 2 agents" about a message that went
nowhere. It is kept because being unreferenced is not by itself a reason to delete a
module, and because this is where that model is written down. Wiring it means inventing
an agent to receive a message, which is a design decision rather than a mechanical
change; do not do it to make this file look used.

**Supervised children: live, through `audio.py`.** `track_child`, `forget_child` and
`check_heartbeats` now watch the two subprocesses Chronoa genuinely runs for minutes at
a time - the `pw-record` capture and the `pw-play` playback. Both were unwatched, and
the failure is silent in a way that costs the user the feature: a capture whose child
wedges leaves `_auto_stop_loop` blocked inside `read()` forever, because that loop only
checks its deadline *between* reads, so the orb stays "listening" and every later
attempt to start a turn is refused - with nothing anywhere saying why. That is the
untracked-subprocess-lifetime defect class `AGENTS.md` records twice in this repo, once
in the player and once in the sandbox executor.

Heartbeats are the same mechanism for both kinds (`update_heartbeat`) and the states
are one enum (`AgentState`), on purpose. Two heartbeat implementations side by side is
the shape that lets a caller update the one nobody is watching, and this repo has
already shipped that shape once - `SandboxExecutor` reporting success while the real
process ran on untracked.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class AgentState(Enum):
    """Possible states for an agent.

    `STARTING` and `EXITED` were added when supervised children arrived. The enum had
    no way to say "running but has not said anything yet" (it stamped
    `last_heartbeat` at construction, so every agent looked immediately healthy) and no
    way to say "has stopped existing" (an agent has no process to ask, but a child
    does). Those are different faults - one is patience, one is death - and a state
    machine that cannot name them makes its caller guess, which is what a boolean does.
    """

    CONNECTED = "connected"
    IDLE = "idle"
    BUSY = "busy"
    DISCONNECTED = "disconnected"
    STARTING = "starting"
    EXITED = "exited"


class AgentInfo:
    """Information about a registered agent."""

    def __init__(self, agent_id: str, name: str):
        self.agent_id = agent_id
        self.name = name
        self.state = AgentState.CONNECTED
        self.last_heartbeat = datetime.now(timezone.utc)
        self.metadata: dict[str, Any] = {}


@dataclass(frozen=True)
class HeartbeatStatus:
    """What is actually true of one supervised thing right now.

    `state` is a single `AgentState`, never a boolean, because the callers that need
    this cannot act on a boolean: releasing a wedged capture is safe, waiting longer
    for a hung one is pointless, and neither is right for a child that has not spoken
    yet. `age` is `None` exactly when nothing has ever reported, which is the only
    honest way to say "no heartbeat yet" - a freshly registered thing that stamps its
    own clock looks identical to a live one forever after.
    """

    name: str
    state: AgentState
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


class GatewaySupervisor:
    """Manages agent gateways with heartbeat and state tracking."""

    def __init__(self, heartbeat_interval: int = 30):
        self.heartbeat_interval = heartbeat_interval
        self._agents: dict[str, AgentInfo] = {}
        self._children: dict[str, _Child] = {}
        logger.info("GatewaySupervisor initialized with %ds interval", heartbeat_interval)

    def register_agent(self, agent_id: str, name: str) -> AgentInfo:
        """Register a new agent."""
        info = AgentInfo(agent_id, name)
        self._agents[agent_id] = info
        logger.info("Registered agent: %s (%s)", name, agent_id)
        return info

    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            logger.info("Unregistered agent: %s", agent_id)

    def get_agent_status(self, agent_id: str) -> Optional[AgentInfo]:
        """Get the status of an agent."""
        return self._agents.get(agent_id)

    def broadcast_message(self, message: dict[str, Any]) -> int:
        """Broadcast a message to all connected agents."""
        count = 0
        for agent_id, agent in self._agents.items():
            if agent.state != AgentState.DISCONNECTED:
                count += 1
                logger.debug("Broadcast to %s: %s", agent_id, message)
        logger.info("Broadcast sent to %d agents", count)
        return count

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
        and this module never needs to know it is supervising processes rather than
        agents.

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
        """Record proof of life for a registered agent or a tracked child.

        One entry point for both on purpose; see the module docstring. Returns False
        for a name that was never registered, and says so, because the previous
        silent no-op made an unwatched child indistinguishable from a watched one -
        the exact shape of the defect that let `_run_host` report success over a
        process nobody was tracking.

        Agents keep their wall-clock stamp and children keep a monotonic one: elapsed
        time is what both are compared against, and a clock that can jump is the wrong
        instrument for it. The agent half is left on `datetime` rather than migrated,
        because it has no caller to break and changing it would be churn.
        """
        agent = self._agents.get(name)
        if agent is not None:
            agent.last_heartbeat = datetime.now(timezone.utc)
            agent.state = AgentState.IDLE
            return True
        child = self._children.get(name)
        if child is not None:
            child.heartbeat = time.monotonic()
            return True
        logger.warning(
            "Heartbeat for %r, which is neither a registered agent nor a tracked child", name
        )
        return False

    def check_heartbeats(self) -> list[HeartbeatStatus]:
        """Every registered agent and tracked child, with the state it is in.

        One status per supervised thing, *including the healthy ones*. Reporting only
        the sick ones would make "nothing is wrong" indistinguishable from "nothing is
        being watched" - and a supervisor that has silently stopped watching is the one
        failure this whole class of module exists to prevent.

        The four states a child can be in are four different faults, and the caller
        acts differently on each:

        - `STARTING` - alive, never reported. Expected for a moment after a spawn;
          not a fault and must not be treated as one.
        - `IDLE` - reported within its silence budget. This is the only healthy state.
        - `DISCONNECTED` - alive but past the budget, i.e. hung. Waiting longer will
          not help; something has to be given up or terminated.
        - `EXITED` - the process is gone, with its status. Waiting can never help, and
          the status says whether it was asked to leave or died on its own.

        Collapsing these is the mistake worth naming: a boolean "is it healthy" answers
        "keep waiting" for a child that will never speak again, which is how a
        supervision bug turns into a retry loop instead of a parked, reported fault.
        """
        checked_at = datetime.now(timezone.utc)
        monotonic_now = time.monotonic()
        statuses: list[HeartbeatStatus] = []

        for agent_id, agent in self._agents.items():
            age = (checked_at - agent.last_heartbeat).total_seconds()
            if age > self.heartbeat_interval * 3:
                agent.state = AgentState.DISCONNECTED
                logger.warning("Agent %s heartbeat expired", agent_id)
                state = AgentState.DISCONNECTED
            else:
                state = AgentState.IDLE if agent.state is AgentState.IDLE else AgentState.CONNECTED
            statuses.append(
                HeartbeatStatus(
                    name=agent_id,
                    state=state,
                    age=age,
                    pid=None,
                    exit_status=None,
                    detail=(
                        f"agent {agent_id} ({agent.name}) last reported {age:.1f}s ago"
                        if state is not AgentState.DISCONNECTED
                        else f"agent {agent_id} ({agent.name}) has not reported for {age:.1f}s"
                    ),
                )
            )

        for name, child in self._children.items():
            status = child.probe()
            age = None if child.heartbeat is None else monotonic_now - child.heartbeat
            # Time the budget is measured against: the last report, or the moment the
            # child was tracked if it never reported one. See `_Child`.
            quiet_for = monotonic_now - max(
                child.registered_at, child.heartbeat or child.registered_at
            )
            if status is not None:
                state = AgentState.EXITED
                detail = f"{name} (pid {child.pid}) exited with status {status}"
            elif child.stall_after is not None and quiet_for > child.stall_after:
                state = AgentState.DISCONNECTED
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
                state = AgentState.STARTING
                detail = f"{name} (pid {child.pid}) is running but has not reported anything yet"
            else:
                state = AgentState.IDLE
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
