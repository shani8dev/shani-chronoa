"""Sandbox execution package for Shani Chronoa.

Provides 5-level sandbox execution (LEVEL_0_NO_EXEC → LEVEL_4_HOST_ROOT)
with privilege escalation blocking, dangerous binary blocklist,
command-level timeouts, and GUI access detection.

Adapted from sayri/adapters/sandbox/executor.py with shani-chronoa paths.
"""

from shani_chronoa.sandbox.executor import SandboxExecutor, SandboxExecutionError
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel

__all__ = ["SandboxExecutor", "SandboxExecutionError", "SandboxConfig", "SandboxLevel"]
