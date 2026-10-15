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
    #: Folders the confined command may write, **in place**: bound read-write
    #: by bubblewrap and granted by Landlock, but never chmod-ed and never
    #: given the wrapper script - unlike `isolated_dir`, which is the sandbox's
    #: own private workspace. A project being built is one of these.
    writable_dirs: List[str] = field(default_factory=list)
    #: Folders it may read but not write (a toolchain in home, the settings
    #: database). Landlock reads nothing outside the system dirs by default.
    readable_dirs: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.allow_network is None:
            self.allow_network = self.level in (
                SandboxLevel.LEVEL_3_HOST_USER,
                SandboxLevel.LEVEL_4_HOST_ROOT,
            )
