"""Sandbox configuration models for Shani Chronoa."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import List, Optional


class SandboxLevel(enum.Enum):
    """5-level sandbox state machine for command execution."""

    LEVEL_0_NO_EXEC = 0
    LEVEL_1_READONLY = 1
    LEVEL_2_ISOLATED_DEV = 2
    LEVEL_3_HOST_USER = 3
    LEVEL_4_HOST_ROOT = 4

    def __str__(self) -> str:
        return self.name


@dataclass
class SandboxConfig:
    """Configuration for a sandboxed command execution."""

    level: SandboxLevel = SandboxLevel.LEVEL_3_HOST_USER
    timeout_seconds: int = 30
    blocked_binaries: List[str] = field(default_factory=list)
    allow_network: Optional[bool] = None
    isolated_dir: Optional[str] = None

    def __post_init__(self) -> None:
        if self.allow_network is None:
            self.allow_network = self.level in (
                SandboxLevel.LEVEL_3_HOST_USER,
                SandboxLevel.LEVEL_4_HOST_ROOT,
            )
