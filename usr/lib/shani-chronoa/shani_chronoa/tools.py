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

Arguments reach the child two ways. Small, JSON-representable ones are
JSON-interpolated into the `python3 -c` program source below, which is what
every built-in skill's arguments look like. A `bytes` argument cannot be
carried that way at all, so it is passed by reference instead: the value goes
to a private temp file, the child is handed the path as a `FileRef`, and it
runs under a fixed program with no interpolation of anything at all. A
caller can ask for the same treatment of a merely oversized argument with
`by_reference=True`; nothing else changes transport, so no existing skill
moves (`argfile.py` documents both, and the ceilings there were measured
against real subprocesses, not assumed).
"""

import importlib
import json
import logging
import shlex
import sys

from shani_chronoa import argfile, verification
from shani_chronoa.sandbox import SandboxConfig, SandboxExecutor, SandboxLevel
from shani_chronoa.skills import discover_skills
from shani_chronoa.tool_tracking import ToolTracker, ORIGIN_USER
from typing import NamedTuple

logger = logging.getLogger(__name__)

TOOLS: list[dict]
_HANDLER_FNS: dict
TOOLS, _HANDLER_FNS = discover_skills()

# Module-level sandbox executor singleton
_SANDBOX = SandboxExecutor()

# Module-level tool-call audit trail singleton (last 100 calls in memory,
# full history appended to ~/.local/share/shani-chronoa/logs/tool_calls.log)
_TRACKER = ToolTracker()

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


def _json_safe(value):
    """Coerce a decoded argument tree to types `repr` renders as Python source.

    Arguments arrive from JSON, so they are already only str/int/float/bool/
    None/list/dict - except that `default=str` used to be passed to
    `json.dumps`, which implies non-JSON types can reach here, and a skill
    argument is untrusted input from a model. Anything else becomes its `str`
    so the child program still compiles.
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def execute_tool(name: str, arguments: dict, by_reference: bool = False, origin: str = ORIGIN_USER) -> str:
    """Execute a named tool with the given arguments, returning a short result string.

    All execution is sandboxed according to the tool's configured SandboxLevel.
    Privilege escalation (sudo/pkexec/su) is blocked for all levels except
    LEVEL_4_HOST_ROOT. Dangerous binaries (mkfs, dd, shutdown, reboot) are
    blocked by the sandbox policy. GUI access is blocked from sandboxed levels.

    `by_reference=True` passes every argument to the skill as a `FileRef` (a
    `str` path into a private temp file) instead of inlining the call's JSON
    into the child's program source. Needed for an argument the inline
    transport cannot carry: a `bytes` value always takes this path on its own,
    and this flag is how a caller asks for it deliberately rather than by
    accident. See `argfile.py`.

    `origin` records *who asked for the call* on the existing audit trail -
    `tools.tool_tracking.ORIGIN_USER` (the default, every shipped caller) or
    `ORIGIN_UNATTENDED` (an armed trigger rule that fired unprompted). It is
    one field on the record `ToolTracker` already writes; there is no second
    log. An unattended actuation that does not set this is indistinguishable
    from one the user asked for, which defeats the audit trail's purpose for
    exactly the behaviour that erodes trust.
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

    # A skill may opt into the by-reference transport for its own oversized
    # arguments (see `skills/speak.py:wants_by_reference`). Ask the module
    # before dispatch rather than guessing: the caller's `by_reference` flag
    # is for callers that know their argument is binary, and a skill that
    # declares its own ceiling gets to enforce it without every caller
    # remembering to pass the flag.
    if not by_reference:
        try:
            module = importlib.import_module(handler_module)
            wants = getattr(module, "wants_by_reference", None)
            if callable(wants) and wants(arguments):
                by_reference = True
        except Exception as e:  # noqa: BLE001 - a broken probe must not kill the call
            logger.debug(f"wants_by_reference probe for '{name}' failed: {e}")

    # Asked first because it is the only transport that can carry a bytes
    # argument; it returns None whenever the inline path below suffices.
    payload = argfile.reference_command(handler_module, handler_func, arguments, by_reference)

    if payload is not None:
        cmd = payload.command
    else:
        # Build a command string that invokes the handler in a subprocess.
        # This allows SandboxExecutor to wrap it with bwrap for sandboxed levels.
        #
        # The program is quoted with shlex, not wrapped in hand-written double
        # quotes: the JSON arguments contain double quotes of their own, which
        # the shell stripped, so every call with a dict argument reached the
        # child as `_run({query: foo})` and died with `NameError: name 'query'
        # is not defined`. Only zero-argument skills worked. shlex.quote
        # escapes the whole program correctly whatever the LLM put in the
        # arguments.
        # A *Python* literal, not JSON. `json.dumps` writes `true`, `false`
        # and `null`, which are not Python: the child program still compiles,
        # because `true` parses as a name, and then dies at runtime with
        # `NameError: name 'true' is not defined`. So every skill called with a
        # boolean or null argument - `dry_run`, `overwrite`, `failed_only`,
        # `include_hidden` - failed over MCP while working perfectly in the
        # unit tests, which call the handler in-process and never serialise.
        #
        # `repr` is the right conversion for the other direction: arguments
        # arrive as JSON, so they are only ever str/int/float/bool/None/list/
        # dict, and `repr` renders every one of those as valid Python source.
        args_literal = repr(_json_safe(arguments))
        program = (
            f"from {handler_module} import {handler_func}; "
            f"import sys; "
            f"result = {handler_func}({args_literal}); "
            f"sys.stdout.write(str(result))"
        )
        cmd = f"python3 -c {shlex.quote(program)}"

    try:
        exit_code, output, duration_ms = _SANDBOX.execute(cmd, config)
        result = output if exit_code == 0 else f"ERROR(exit={exit_code}): {output}"
        if exit_code != 0:
            _TRACKER.record_call(name, arguments, result, duration_ms, origin=origin)
            logger.error(f"Tool '{name}' exited with code {exit_code}: {output}")
            return output or f"Tool '{name}' failed with exit code {exit_code}"
        # The skill's own string is its account of what it did, not evidence
        # that it happened. A module that declares POST_CONDITION gets that
        # checked against real state here, and an action with no post-condition
        # says so rather than letting the caller infer success. See
        # verification.py for why this is not optional politeness.
        try:
            checked = verification.verify(handler_module, arguments)
        except Exception as e:  # noqa: BLE001 - verification must never kill the action
            logger.warning(f"Post-condition for '{name}' raised: {e}")
            checked = verification.Result(verification.Verdict.UNVERIFIED, str(e))
        # Recorded after verification, not before: a record written on the way
        # out cannot carry a verdict that does not exist yet, which left a
        # failed action in the audit log looking exactly like a successful one.
        # Do not move this call back up.
        _TRACKER.record_call(
            name, arguments, result, duration_ms,
            origin=origin, verdict=checked.verdict.value,
            evidence=checked.evidence,
        )
        if checked.verdict is verification.Verdict.FAILED:
            logger.error(f"Tool '{name}' reported success but verification failed: {checked.evidence}")
        return output + checked.suffix
    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}")
        _TRACKER.record_call(name, arguments, f"EXCEPTION: {e}", 0.0, origin=origin)
        return f"Tool '{name}' failed: {e}"
    finally:
        # The payload files hold the argument values themselves, so they go
        # away whatever the child did. cleanup() is idempotent, so this also
        # covers a payload that was built but never dispatched.
        if payload is not None:
            payload.cleanup()


class ToolOutcome(NamedTuple):
    """A dispatch result with the verdict attached, for callers that act on it.

    `execute_tool` returns a bare string because that is what an LLM reads, and
    prose is the right shape for that. It is the wrong shape for a caller
    deciding whether to *record a success* - the verdict is only recoverable
    from the string by matching the marker text, and a caller that forgets is
    indistinguishable from one that never checked.
    """

    text: str
    verdict: verification.Verdict
    evidence: str = ""


def execute_tool_outcome(
    name: str,
    arguments: dict,
    by_reference: bool = False,
    origin: str = ORIGIN_USER,
) -> ToolOutcome:
    """`execute_tool`, with the verification verdict as data rather than prose.

    The verdict is recovered from the returned string via
    `verification.verdict_from_text`, which matches the marker
    `verification.Result.suffix` emits - so this stays correct if that suffix
    changes, rather than hardcoding a second copy of the string here.
    """
    text = execute_tool(name, arguments, by_reference=by_reference, origin=origin)
    verdict = verification.verdict_from_text(text)
    evidence = ""
    if verdict is verification.Verdict.FAILED:
        evidence = text.split(verification.FAILED_MARKER, 1)[-1].lstrip(": ").rstrip(")")
    return ToolOutcome(text=text, verdict=verdict, evidence=evidence)
