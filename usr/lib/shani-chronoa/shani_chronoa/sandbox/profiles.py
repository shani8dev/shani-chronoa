"""Per-Agent Sandbox Profiles for shani-chronoa."""

from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class AgentProfile:
    """Defines sandbox constraints for an agent."""

    name: str
    memory_limit: int  # in MB
    cpu_limit: int  # in shares
    network_access: bool
    allowed_tools: list[str] = field(default_factory=list)
    timeout_seconds: int = 300

    def validate_against_profile(self, call: dict) -> bool:
        """Check if a tool call is allowed by this profile."""
        tool_name = call.get("tool_name", "")
        if self.allowed_tools and tool_name not in self.allowed_tools:
            logger.warning("Tool %s not allowed by profile %s", tool_name, self.name)
            return False
        return True


PROFILE_DEFAULT = AgentProfile(
    name="default",
    memory_limit=512,
    cpu_limit=1024,
    network_access=True,
    allowed_tools=[],
    timeout_seconds=300,
)

PROFILE_RESTRICTED = AgentProfile(
    name="restricted",
    memory_limit=256,
    cpu_limit=512,
    network_access=False,
    allowed_tools=["read_only_tool"],
    timeout_seconds=60,
)

PROFILE_FULL_ACCESS = AgentProfile(
    name="full-access",
    memory_limit=2048,
    cpu_limit=4096,
    network_access=True,
    allowed_tools=[],
    timeout_seconds=600,
)

PROFILE_READ_ONLY = AgentProfile(
    name="read-only",
    memory_limit=128,
    cpu_limit=256,
    network_access=False,
    allowed_tools=["read_only_tool"],
    timeout_seconds=30,
)


def get_profile(name: str) -> AgentProfile:
    """Get a profile by name."""
    profiles = {
        "default": PROFILE_DEFAULT,
        "restricted": PROFILE_RESTRICTED,
        "full-access": PROFILE_FULL_ACCESS,
        "read-only": PROFILE_READ_ONLY,
    }
    if name not in profiles:
        logger.warning("Unknown profile %s, falling back to default", name)
        return PROFILE_DEFAULT
    return profiles[name]
