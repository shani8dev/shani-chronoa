"""Entry point for the assistant's tool-calling pipeline.

Loads local system "skills" the LLM can invoke to actually get things done
(open an app, adjust volume, set a timer, check the battery, search the web)
from `shani_chronoa/skills/` - deliberately a fixed, whitelisted set of
actions rather than a generic shell-command tool, since that would let the
LLM run anything. Built-in skills live in `shani_chronoa/skills/`; a user can
add their own by dropping a module with the same contract (see
`skills/__init__.py`) into `~/.config/shani-chronoa/skills/`.

All tool execution is routed through the 5-level SandboxExecutor
(LEVEL_0_NO_EXEC → LEVEL_4_HOST_ROOT), blocking privilege escalation,
dangerous binaries, and GUI access from sandboxed levels — adapting the
pattern from sayri/adapters/sandbox/executor.py.
"""

import json
import logging
import sys

from shani_chronoa.sandbox import SandboxConfig, SandboxExecutor, SandboxLevel
from shani_chronoa.skills import discover_skills

logger = logging.getLogger(__name__)

TOOLS: list[dict]
_HANDLER_FNS: dict
TOOLS, _HANDLER_FNS = discover_skills()

# Module-level sandbox executor singleton
_SANDBOX = SandboxExecutor()

logger.info(f"Loaded {len(_HANDLER_FNS)} skill(s): {', '.join(sorted(_HANDLER_FNS)) or '(none)'}")


def _get_sandbox_config(tool_name: str) -> SandboxConfig:
    """Determines the sandbox level for a given tool.

    Uses LEVEL_3_HOST_USER as default. Level 0 (no exec) is reserved
    for tools that are explicitly prohibited. Level 4 is only used
    when the tool name explicitly requires elevation.
    """
    # Tools that must never execute (LEVEL_0)
    blocked_tools: frozenset[str] = frozenset()

    # Tools that require host-root elevation (LEVEL_4)
    elevated_tools: frozenset[str] = frozenset()

    if tool_name in blocked_tools:
        return SandboxConfig(level=SandboxLevel.LEVEL_0_NO_EXEC, timeout_seconds=5)
    if tool_name in elevated_tools:
        return SandboxConfig(level=SandboxLevel.LEVEL_4_HOST_ROOT, timeout_seconds=60)
    return SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER, timeout_seconds=30)


def execute_tool(name: str, arguments: dict) -> str:
    """Execute a named tool with the given arguments, returning a short result string.

    All execution is sandboxed according to the tool's configured SandboxLevel.
    Privilege escalation (sudo/pkexec/su) is blocked for all levels except
    LEVEL_4_HOST_ROOT. Dangerous binaries (mkfs, dd, shutdown, reboot) are
    blocked by the sandbox policy. GUI access is blocked from sandboxed levels.
    """
    handler = _HANDLER_FNS.get(name)
    if handler is None:
        return f"Unknown tool: {name}"
    if not isinstance(arguments, dict):
        # Arguments come from the LLM and are untrusted; a non-dict value
        # must not reach a skill handler (which would crash on .get()).
        logger.warning(f"Tool '{name}' called with non-dict arguments; ignoring them")
        arguments = {}

    config = _get_sandbox_config(name)
    handler_module = handler.__module__
    handler_func = handler.__name__

    # Build a command string that invokes the handler in a subprocess.
    # This allows SandboxExecutor to wrap it with bwrap for sandboxed levels.
    args_json = json.dumps(arguments, default=str)
    cmd = (
        f"python3 -c \""
        f"from {handler_module} import {handler_func}; "
        f"import sys; "
        f"result = {handler_func}({args_json}); "
        f"sys.stdout.write(str(result))\""
    )

    try:
        exit_code, output, _duration = _SANDBOX.execute(cmd, config)
        if exit_code != 0:
            logger.error(f"Tool '{name}' exited with code {exit_code}: {output}")
            return output or f"Tool '{name}' failed with exit code {exit_code}"
        return output
    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}")
        return f"Tool '{name}' failed: {e}"
