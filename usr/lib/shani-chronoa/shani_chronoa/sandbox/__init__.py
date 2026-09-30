"""Sandbox execution package for Shani Chronoa.

Provides 5-level sandbox execution (LEVEL_0_NO_EXEC → LEVEL_4_HOST_ROOT)
with privilege escalation blocking, dangerous binary blocklist,
command-level timeouts, and GUI access detection.

Adapted from sayri/adapters/sandbox/executor.py with shani-chronoa paths.

Two layers, and they are not interchangeable. The **level** says how a command
runs - which namespaces, whether the filesystem is confined, whether privilege
is available. The **profile** (`sandbox.profiles`) says what it may do and what
ceiling it runs under, and is selected by the call's *origin*: an unattended
trigger rule gets a stricter profile than one the user asked for. Both are
applied by `SandboxExecutor.execute`, the ceilings in the child between `fork`
and `execve`.

A third layer, `sandbox.seccomp`, is off by default and is a *syscall* filter
rather than a policy: it denies the calls that escape the other two (mount,
memfd_create, bpf, unshare, and the rest) and denies `seccomp(2)` itself so the
command cannot widen it. It is deliberately not imported here, because importing
it would put a seccomp module in the import path of every process that touches
this package for a feature that ships switched off.
"""

from shani_chronoa.sandbox.executor import (
    ProfileLimitError,
    SandboxExecutor,
    SandboxExecutionError,
)
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel
from shani_chronoa.sandbox.profiles import (
    AgentProfile,
    PROFILE_DEFAULT,
    PROFILE_FULL_ACCESS,
    PROFILE_READ_ONLY,
    PROFILE_RESTRICTED,
    get_profile,
    profile_for_origin,
)
from shani_chronoa.sandbox.seccomp import SECCOMP_SETTING, SeccompError

__all__ = [
    "SandboxExecutor",
    "SandboxExecutionError",
    "SandboxConfig",
    "SandboxLevel",
    "ProfileLimitError",
    "AgentProfile",
    "PROFILE_DEFAULT",
    "PROFILE_RESTRICTED",
    "PROFILE_FULL_ACCESS",
    "PROFILE_READ_ONLY",
    "get_profile",
    "profile_for_origin",
    "SECCOMP_SETTING",
    "SeccompError",
]
