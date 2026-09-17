"""Gateway Supervisor Architecture for shani-chronoa."""

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AgentState(Enum):
    """Possible states for an agent."""
    CONNECTED = "connected"
    IDLE = "idle"
    BUSY = "busy"
    DISCONNECTED = "disconnected"


class AgentInfo:
    """Information about a registered agent."""

    def __init__(self, agent_id: str, name: str):
        self.agent_id = agent_id
        self.name = name
        self.state = AgentState.CONNECTED
        self.last_heartbeat = datetime.now(timezone.utc)
        self.metadata: dict[str, Any] = {}


class GatewaySupervisor:
    """Manages agent gateways with heartbeat and state tracking."""

    def __init__(self, heartbeat_interval: int = 30):
        self.heartbeat_interval = heartbeat_interval
        self._agents: dict[str, AgentInfo] = {}
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

    def update_heartbeat(self, agent_id: str) -> None:
        """Update the heartbeat for an agent."""
        if agent_id in self._agents:
            self._agents[agent_id].last_heartbeat = datetime.now(timezone.utc)
            self._agents[agent_id].state = AgentState.IDLE

    def check_heartbeats(self) -> list[str]:
        """Check all agent heartbeats and return disconnected ones."""
        now = datetime.now(timezone.utc)
        disconnected = []
        for agent_id, agent in self._agents.items():
            age = (now - agent.last_heartbeat).total_seconds()
            if age > self.heartbeat_interval * 3:
                agent.state = AgentState.DISCONNECTED
                disconnected.append(agent_id)
                logger.warning("Agent %s heartbeat expired", agent_id)
        return disconnected
