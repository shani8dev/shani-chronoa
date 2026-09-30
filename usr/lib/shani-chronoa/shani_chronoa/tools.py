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
import sys

from shani_chronoa import (argfile, capabilities, config as config_mod,
                              guardrail, permissions, planmode,
                              verification)
from shani_chronoa.sandbox import SandboxConfig, SandboxExecutor, SandboxLevel
from shani_chronoa.skills import discover_skills
from shani_chronoa.tool_tracking import ToolTracker, ORIGIN_USER
from typing import NamedTuple

logger = logging.getLogger(__name__)

TOOLS: list[dict]
_HANDLER_FNS: dict
TOOLS, _HANDLER_FNS = discover_skills()

#: Tools that run in this process instead of the sandbox subprocess, because
#: what they do cannot cross that boundary. `ask_user` has to put a question in
#: the window the user is looking at and block until they answer.
#:
#: This is a privilege list. Anything named here is unsandboxed, so it stays a
#: short explicit set rather than anything derived - a rule that decided this
#: automatically would widen the exemption without anyone reviewing it.
_LOCAL_TOOLS = frozenset({"ask_user"})


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


class DispatchResult(NamedTuple):
    """What a dispatch actually did, as data rather than as prose.

    `ran` is the field that was missing. Everything Chronoa returned was a
    string, so "the tool ran and the answer is X" and "the tool never ran" were
    the same shape, and a caller - or the model - had to infer which from
    wording. goose carries this as `is_error` on the tool response for the same
    reason.

    Read with `verdict`, it is two bits rather than one:

    - `ran=True,  VERIFIED`   - ran, and the post-condition held
    - `ran=True,  UNVERIFIED` - ran, and there is nothing to check it against
    - `ran=True,  FAILED`     - ran, and the post-condition did not hold
    - `ran=False, UNVERIFIED` - **never executed**: unknown tool, refused by
      plan mode, non-zero exit, or an exception

    The last row is the one that was previously indistinguishable from the
    second. A consent refusal still lands in row two, because the refusal
    happens inside the skill and the dispatch only sees exit 0 - making that
    structural means changing all 69 skills, so it is noted rather than faked.
    """

    text: str
    verdict: verification.Verdict
    ran: bool
    evidence: str = ""

    @property
    def is_error(self) -> bool:
        """goose's spelling: did this fail to produce an answer."""
        return not self.ran



#: Which argument names the resource a skill would touch, so a permission rule
#: can be written against a path or a unit rather than against the tool alone.
#: Skills not listed fall through with `None` and are unaffected by rules.
_RESOURCE_ARGUMENT = {
    "delete_file": "path",
    "trash_file": "path",
    "move_or_copy_file": "source",
    "write_text_file": "path",
    "read_text_file": "path",
    "find_files": "root",
    "search_file_contents": "root",
    "find_and_replace": "path",
    "extract_archive": "archive",
    "list_directory": "path",
    "manage_mount": "device",
    "control_service": "unit",
    "print_file": "path",
    "open_file": "path",
}


def _resource_for(name: str, arguments: dict):
    """The path or unit this call would touch, or None if it is not scoped."""
    if not isinstance(arguments, dict):
        return None
    key = _RESOURCE_ARGUMENT.get(name)
    if not key:
        return None
    value = arguments.get(key)
    return str(value) if value else None


def _schema_for(name: str) -> "dict | None":
    """The declared parameter schema for a tool, or None if it declares none."""
    for entry in TOOLS:
        function = entry.get("function") or {}
        if function.get("name") == name:
            return function.get("parameters") or None
    return None

def _consent_key_for(name: str) -> "str | None":
    """The consent key gating this tool, or None if it is not gated.

    Read from the live tool schema rather than a second hand-kept list, so a
    skill that starts declaring a consent key is honoured the moment its
    description says so - the same reason `capabilities.gated_by()` treats the
    description as the more current of its two sources.
    """
    for entry in TOOLS:
        function = entry.get("function") or {}
        if function.get("name") == name:
            return capabilities.gated_by(name, function.get("description", ""))
    return None


def _dispatch(name: str, arguments: dict, by_reference: bool = False,
             origin: str = ORIGIN_USER) -> DispatchResult:
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
        return DispatchResult(f"Unknown tool: {name}", verification.Verdict.UNVERIFIED, False)
    if not isinstance(arguments, dict):
        # Arguments come from the LLM and are untrusted; a non-dict value
        # must not reach a skill handler (which would crash on .get()).
        logger.warning(f"Tool '{name}' called with non-dict arguments; ignoring them")
        arguments = {}

        config = _get_sandbox_config(name)
        handler_module = handler.__module__
        handler_func = handler.__name__

    # Plan mode is checked here, in the dispatch path, rather than asked for in
    # the system prompt. A prompt is a promise the model may or may not keep;
    # this is a fact, which is the only reason the mode is worth having.
    refusal = planmode.blocked_reason(name)
    if refusal:
        return DispatchResult(refusal, verification.Verdict.UNVERIFIED, False)

    # Scoped deny rules are a pre-filter here, for the same reason: a rule the
    # user set for this session should hold even if the skill forgets to look.
    # Only *deny* is enforced centrally. An allow is deliberately not - granting
    # from here would bypass the consent key the user has switched off, and the
    # whole point of those keys is that a skill cannot talk its way past them.
    # Grants are consulted by each skill's own gate instead.
    # Before any policy question is even asked. A malformed call is not a
    # permission problem, and letting one through means the failure surfaces as
    # a traceback inside a subprocess - which tells the model nothing it can
    # act on, where the reason here names the argument and what was expected.
    malformed = guardrail.check(name, arguments, _schema_for(name) or {})
    if malformed:
        return DispatchResult(malformed, verification.Verdict.UNVERIFIED, False)

    scoped_resource = _resource_for(name, arguments)
    if scoped_resource is not None:
        allowed, why = permissions.permits(name, scoped_resource)
        if not allowed:
            return DispatchResult(why, verification.Verdict.UNVERIFIED, False)

    # A grant the user gave this session opens the tool's own consent key for
    # this one call, so the skill's internal gate agrees with the dispatch.
    #
    # Deliberately outside the branch above. Only 14 of the 69 tools name a
    # path or unit at all - `screenshot`, `set_wallpaper` and `web_search` have
    # no such argument - so nesting this where the scoped check lives left the
    # grant unreachable for precisely the consent-gated tools it exists to
    # serve. The first version of this had that bug and a test caught it.
    #
    # An unscoped tool is matched against "*" as its resource. A grant written
    # as `("*", allow_once)` covers every tool; a grant naming a concrete path
    # does not, which is the conservative direction - a permission to delete
    # `/home/me/Downloads` should not also open a screenshot.
    #
    # Hoisted because it is read again at execution time, and a tool with no
    # scoped resource never reaches a branch that would assign it.
    granted_key = None
    grant_target = scoped_resource if scoped_resource is not None else "*"
    if permissions.session_grant(name, grant_target):
        granted_key = _consent_key_for(name)
    else:
        # Nothing on record, so the tool's own consent key decides. If it is
        # shut, ask the user rather than returning a refusal they cannot act
        # on - the whole point of the grant path is that a "no" is the user's
        # to give, not only the app's.
        #
        # Only asked when the key is *definitively* off. `get_bool` says
        # nothing about the other half of `sense_allowed()` - privacy mode - so
        # a key that reads true while privacy mode blocks the call must not
        # prompt. Asking there would put a question to the user whose "yes"
        # could not possibly work.
        consent_key = _consent_key_for(name)
        if (consent_key is not None
                and permissions.can_ask()
                and not config_mod.ChronoaConfig().get_bool(consent_key, False)):
            title = capabilities.tool_title(name) or name
            granted = permissions.decide(name, scoped_resource, consent_key,
                                         describe=title.lower())
            if granted is None:
                return DispatchResult(
                    f"The user did not allow {name}. Nothing was done.",
                    verification.Verdict.UNVERIFIED, False)
            granted_key = consent_key
            # Consume the record `decide()` just wrote, so "allow this once"
            # means once. Without this the grant sat on the books and the *next*
            # call found it - so allowing once allowed twice, which is the exact
            # opposite of what the button said and the worst kind of consent bug
            # to ship silently. `session_grant` deletes a one-shot and leaves a
            # session grant standing, so one call covers both cases.
            permissions.session_grant(name, grant_target)

    config = _get_sandbox_config(name)
    handler_module = handler.__module__
    handler_func = handler.__name__

    # Named, not inferred: this list runs unsandboxed, which is a privilege,
    # and a heuristic deciding it would widen without anyone reviewing it.
    if name in _LOCAL_TOOLS:
        return DispatchResult(handler(arguments), verification.Verdict.UNVERIFIED, True)

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
        argv = payload.argv
    else:
        # Build a command string that invokes the handler in a subprocess.
        # This allows SandboxExecutor to wrap it with bwrap for sandboxed levels.
        #
        # Kept because the bug was real, not because the fix is still needed.
        # This used to be `shlex.quote`d into a command string, and the JSON
        # arguments contain double quotes of their own which the shell stripped,
        # so every call with a dict argument reached the child as
        # `_run({query: foo})` and died with `NameError: name 'query' is not
        # defined`. Only zero-argument skills worked. There is no shell between
        # here and the exec now, so each element of the argv carries its own
        # bytes and that class of stripping is structurally impossible - the
        # bug cannot come back in this form, but a new transport could
        # reintroduce the same mistake somewhere else.
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
        argv = ["python3", "-c", program]

    if granted_key:
        # Set on the child, not on this process. The dispatcher runs many calls
        # in one process, so a value set here would be whichever call happened
        # to be in flight - exactly the class of bug where a grant meant for
        # one tool silently opens another.
        argv = ["env", f"{config_mod.CONSENT_GRANT_ENV}={granted_key}", *argv]

    try:
        exit_code, output, duration_ms = _SANDBOX.execute(argv, config)
        result = output if exit_code == 0 else f"ERROR(exit={exit_code}): {output}"
        if exit_code != 0:
            _TRACKER.record_call(name, arguments, result, duration_ms, origin=origin)
            logger.error(f"Tool '{name}' exited with code {exit_code}: {output}")
            return DispatchResult(output or f"Tool '{name}' failed with exit code {exit_code}",
                               verification.Verdict.UNVERIFIED, False)
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
        return DispatchResult(output + checked.suffix, checked.verdict, True,
                             checked.evidence)
    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}")
        _TRACKER.record_call(name, arguments, f"EXCEPTION: {e}", 0.0, origin=origin)
        return DispatchResult(f"Tool '{name}' failed: {e}", verification.Verdict.UNVERIFIED, False)
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
    ran: bool = True

    @property
    def is_error(self) -> bool:
        """goose's spelling: did this fail to produce an answer."""
        return not self.ran


def execute_tool(name: str, arguments: dict, by_reference: bool = False,
                 origin: str = ORIGIN_USER) -> str:
    """Execute a named tool, returning the short result string a model reads.

    Prose is the right shape for that - the model reads it. It is the wrong
    shape for a caller deciding whether the tool ran, which is why `_dispatch`
    exists and why `execute_tool_outcome` uses it instead of re-reading the
    string this returns.
    """
    return _dispatch(name, arguments, by_reference=by_reference,
                     origin=origin).text


def execute_tool_outcome(
    name: str,
    arguments: dict,
    by_reference: bool = False,
    origin: str = ORIGIN_USER,
) -> ToolOutcome:
    """`execute_tool`, with the verdict and whether it ran as data.

    The verdict used to be recovered from the returned string by matching a
    marker, even though the dispatch had computed it exactly one line earlier
    and thrown it away. Re-deriving it meant a change to the marker's wording
    silently turned every verdict into UNVERIFIED - which is the failure this
    class's own docstring warns about, since a caller that forgets to check
    is then indistinguishable from one that never could.
    """
    result = _dispatch(name, arguments, by_reference=by_reference, origin=origin)
    return ToolOutcome(text=result.text, verdict=result.verdict,
                       evidence=result.evidence, ran=result.ran)
