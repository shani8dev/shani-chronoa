"""Bubblewrap (bwrap) and Host Sandbox Executor for Shani Chronoa.

Adapted from sayri/adapters/sandbox/executor.py with shani-chronoa paths.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Tuple

from shani_chronoa import files
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel
from shani_chronoa.sandbox.profiles import AgentProfile, profile_for_origin
from shani_chronoa.sandbox import seccomp as _seccomp
from shani_chronoa.tool_tracking import ORIGIN_USER
from shani_chronoa.redaction import redactor
from .commands import (
    _program,
    _name_matches,
    _blocked_binary,
    _destructive_pattern,
    _shell_script,
    _unresolvable_script,
    _risky_builtin,
    _sensitive_pattern,
    _risky_builtin_name,
)
from .child import (
    _EXPECTED_PARENT,
    _harden_child,
)
from .limits import (
    _PENDING_SECCOMP,
    _seccomp_requested,
    _seccomp_refusal,
    _seccomp_child_failure,
    _PENDING_CEILING,
    _resolve_ceiling,
)

logger = logging.getLogger(__name__)


def _child_pythonpath() -> str:
    """`PYTHONPATH` that lets a child process import `shani_chronoa`.

    Every skill is invoked as `python3 -c "from shani_chronoa.skills.X import
    Y"`, in a *fresh* interpreter that inherits the environment but not the
    parent's `sys.path`. The package installs to `/usr/lib/shani-chronoa/`,
    which is not a default `site-packages` entry, so without this the child
    dies with `ModuleNotFoundError: No module named 'shani_chronoa'` before the
    skill runs at all.

    This was invisible for the life of the project for two compounding reasons.
    The launcher scripts fix `sys.path` with `sys.path.insert`, which affects
    only the server process and is never exported; and
    `redactor.child_env()` copies the parent environment, so a
    developer who happened to have `PYTHONPATH` set saw every call succeed
    while a real `.desktop` launch saw every call fail. The whole MCP surface
    was affected - `tools/list` answered with all 27 tools and every single
    `tools/call` failed.

    Computed in one place because the confined and host paths each had their own
    copy, and the host one was missing. An existing `PYTHONPATH` is appended to
    rather than replaced, so a caller's own entries still resolve.
    """
    package = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    existing = os.environ.get("PYTHONPATH", "")
    return f"{package}{os.pathsep}{existing}" if existing else package



def _bwrap_usable() -> bool:
    """Whether bubblewrap can actually create the namespaces it needs.

    Presence is not capability. A host can have bwrap installed and still be
    unable to create a user namespace - a hardened kernel, most container
    runtimes, and this development box, where it dies with "setting up uid map"
    or "loopback: Failed RTM_NEWADDR". Gating on shutil.which() alone therefore
    made every confined command fail on exactly the machines Landlock exists to
    serve. Probed once, then cached: the answer cannot change mid-process.
    """
    global _BWRAP_USABLE
    if _BWRAP_USABLE is not None:
        return _BWRAP_USABLE
    if not shutil.which("bwrap"):
        _BWRAP_USABLE = False
        return False
    try:
        probe = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc", "--", "true"],
            capture_output=True, timeout=10,
        )
        _BWRAP_USABLE = probe.returncode == 0
    except Exception:  # noqa: BLE001 - unusable is the answer, not an error
        _BWRAP_USABLE = False
    return _BWRAP_USABLE


_BWRAP_USABLE = None


def _landlock_abi() -> int:
    """Landlock ABI version, or 0 when the kernel has no Landlock.

    Resolved per call rather than cached: the answer depends on the kernel,
    and a cached 0 would keep reporting 'unavailable' on a host that has it.
    """
    try:
        from shani_chronoa.sandbox.landlock import abi_version

        return abi_version()
    except Exception:  # noqa: BLE001 - absence is the answer, not an error
        return 0

DANGEROUS_BINARIES = ("mkfs", "dd", "shutdown", "reboot", "mount", "umount")

#: Launchers that return immediately instead of waiting for the program.
_BACKGROUND_PROGRAMS = ("gtk-launch", "xdg-open")

#: Chronoa's own management tools, which an isolated sandbox may not reach.
#: Named once because guard 3 and guard 6 both apply it and the two must not
#: drift apart - that is how the shell opt-in came to bypass the escalation
#: check in the first place.
_INTERNAL_BINARIES = ("shani-skills", "shani-plugins", "shani-settings",
                      "shani", "pkill", "killall")


class SandboxExecutionError(Exception):
    pass

#: The kernel's signal for a soft `RLIMIT_CPU` breach. Its default action is to
#: terminate, so a child that outruns a profile's CPU budget dies here rather
#: than being reported as an ordinary non-zero exit.
_SIGXCPU = 24

#: `LEVEL_4_HOST_ROOT` is not a level a filter can be applied to, and saying so
#: loudly matters more than applying it would. `PR_SET_NO_NEW_PRIVS` - which
#: seccomp cannot be installed without - makes the kernel refuse to honour
#: `setuid`, and `setuid` is exactly how `pkexec` elevates. A filter installed
#: before it would leave a level whose entire purpose is privilege elevation
#: silently broken, and the failure would surface as a polkit prompt that never
#: appears. So the filter is skipped there and the skip is named, once, rather
#: than being applied or ignored.
_SECCOMP_SKIPPED_LEVELS: "set" = set()

#: Said once when the filter is withheld from a bubblewrap child. Separate from
#: `_SECCOMP_SKIPPED_LEVELS` because the reason is the opposite shape: the level
#: is not being skipped, and the command *is* confined - by bubblewrap's own
#: namespace, which is the stronger mechanism. What is lost is the second layer.
_SECCOMP_SKIPPED_FOR_BWRAP: "set" = set()


def _seccomp_conflicts_with_bwrap(level: SandboxLevel, use_bwrap: bool) -> bool:
    """Whether a filter in this child would stop bubblewrap from starting.

    **This is a measured conflict, not a theoretical one.** The filter denies
    `mount(2)` - correctly, since Landlock has no mount right in any ABI and
    mounting over a path is invisible to it - and `_harden_child` runs as
    `preexec_fn` on the **bwrap process itself**. So the filter is installed
    before bwrap builds the namespace it exists to build, and bwrap dies:

        filter ON  -> bwrap rc=1  bwrap: Failed to make / slave: Operation not permitted
        filter OFF -> bwrap rc=0

    Measured on this machine by installing the real filter and then running the
    real bwrap, and it is why `test_sandbox_seccomp.py::
    test_the_landlock_wrapper_path_also_survives_the_filter` fails: with the
    gate on, **every** `LEVEL_1`/`LEVEL_2` call on this host returned that
    bwrap error, which is a confinement mechanism refusing to confine anything.

    The filter is withheld rather than narrowed. Narrowing it would mean leaving
    `mount` out of the deny list, and `mount` is the whole reason that list has a
    filesystem section - it is the one syscall in it that a sandbox exists to
    deny. bwrap's namespace *is* the confinement for this level and it is
    enforced by the kernel in a way a seccomp list cannot improve on, so the
    trade is one layer lost, loudly named, rather than a hole left open quietly.

    Only `use_bwrap` matters, not the level: `_run_landlock` falls back to
    Landlock-only when bubblewrap is unusable, and in that path the filter is
    perfectly compatible - which is why this takes the decision the spawn path
    already made rather than re-deriving it from the level.
    """
    return use_bwrap


#: `network_access=False` is declared by every restricted profile and enforced by
#: none of them - see the module docstring of `profiles.py` for why no path in
#: this executor creates a network namespace. Said once per (profile, level) so
#: a long-lived assistant does not fill its own log with the same warning every
#: turn, while still naming the profile on the call where it matters.
_NETWORK_WARNING_EMITTED: "set" = set()


class SandboxExecutor:
    """Executes commands adhering to fine-grained SandboxLevel configurations."""

    def __init__(self, sandboxes_root: str | None = None) -> None:
        self.sandboxes_root = sandboxes_root or files.data_home() / "shani-chronoa" / "sandboxes"
        # mkdir then chmod, not mkdir(mode=...): the mode argument is masked by
        # the process umask and lands permissive without error. Measured at umask
        # 002 this was 0775, putting the exec'd Landlock wrapper within reach of
        # another local account. Reasoning in `files.ensure_private_dir`.
        files.ensure_private_dir(self.sandboxes_root)
        self.bwrap_available = bool(shutil.which("bwrap"))

    def execute(
        self,
        argv: "list[str]",
        config: SandboxConfig,
        agent_id: str = "default",
        *,
        origin: str = ORIGIN_USER,
        tool_name: "str | None" = None,
        profile: "AgentProfile | None" = None,
    ) -> Tuple[int, str, float]:
        """Executes a command under the specified sandbox level.

        `argv` is a list of arguments, never a command string. Every policy guard
        below is a property of the program the kernel will exec, which is a
        field of argv rather than something recovered by parsing text - so a
        check can no longer be defeated by writing the same name in a form a
        shell would have expanded.

        A string is refused rather than quietly shelled for exactly that reason:
        all four guards used to read a shell string, and every one of them could
        be walked past (`$(echo sudo) reboot` cleared the escalation check;
        `$(echo dd) status=...` ran the real `dd`).

        The one remaining way to lose them is to hand `execute()` an argv whose
        program cannot be read, so that is refused too rather than run blind: a
        malformed or unreadable `env` prefix returns None from `_program()`, and
        no guard can check a program nobody can name.

        `origin` selects the profile layer (`profiles.profile_for_origin`), and
        `tool_name` is the skill being run, which is what the profile's allowlist
        is written against. Neither can be recovered from `argv`: the argv is
        `python3 -c "from shani_chronoa.skills.clock import get_datetime; ..."`,
        so the tool's identity is a property of the *call*, not of the command,
        and re-deriving it by parsing the program text would rebuild exactly the
        parallel copy of the transport that the argv migration removed.
        `profile` overrides the origin's choice outright, for a caller that wants
        a different ceiling deliberately.

        All three default to today's behaviour - `ORIGIN_USER`, no tool, and the
        profile that origin maps to - so a caller that passes nothing gets the
        permissive profile and the resource ceilings it declares. That is the
        reason `memory_limit` and `cpu_limit` are enforced without any change
        outside this package: `PROFILE_DEFAULT` applies to every skill call that
        exists today. The allowlist does not, because an empty one is not a
        restriction, and `ORIGIN_UNATTENDED` is the only origin that has one.

        Returns: (exit_code, output, duration_ms)
        """
        start_time = time.monotonic()

        if isinstance(argv, str):
            return (
                126,
                "Security error: execute() takes an argv list, not a command string. "
                "A shell string is the shape the policy guards could not read; pass "
                "the program and its arguments separately.",
                0.0,
            )
        argv = [str(argument) for argument in argv]
        if not argv:
            return (126, "Security error: an empty argv has no program to execute.", 0.0)
        program = _program(argv)
        if program is None:
            return (
                126,
                "Security error: the program this argv would run cannot be read "
                "from the argv itself - a malformed `env` invocation, or an "
                "`env` option this executor does not know. Every policy guard "
                "below is a statement about the program that runs, so an argv "
                "whose program cannot be named is refused rather than run "
                "unchecked.",
                0.0,
            )

        # 0. The profile layer, before anything is spawned. Placed here so a
        # refusal is a refusal and not a command that ran and reported failure
        # afterwards, and before the argv guards so that a tool the profile does
        # not allow is never evaluated against a blocklist it has no business
        # reaching.
        active = profile if profile is not None else profile_for_origin(origin)
        refusal = active.refusal_for(tool_name)
        if refusal is not None:
            logger.warning("Refusing %r under profile %s", tool_name, active.name)
            return (126, refusal, 0.0)
        if not active.network_access:
            key = (active.name, config.level.value)
            if key not in _NETWORK_WARNING_EMITTED:
                _NETWORK_WARNING_EMITTED.add(key)
                logger.warning(
                    "Profile '%s' declares network_access=False and this is NOT "
                    "enforced on the %s path: no sandbox level here creates a "
                    "network namespace (only _run_bwrap's --unshare-net would, and "
                    "it has no callers). The tool allowlist is what holds this "
                    "profile's line today. See profiles.py for why.", active.name,
                    config.level.value)

        # 1. Level 0: Total Prohibition
        if config.level == SandboxLevel.LEVEL_0_NO_EXEC:
            return (
                126,
                "Security error (LEVEL_0_NO_EXEC): This sub-agent has the LEVEL_0_NO_EXEC level configured. "
                "It has no permissions to execute commands on the system.",
                0.0,
            )

        # 2. Prevent Privilege Escalation for non-Level 4
        if _name_matches(program, ("sudo", "pkexec", "su")) \
                and config.level != SandboxLevel.LEVEL_4_HOST_ROOT:
            return (
                126,
                f"Security error: Privilege escalation attempt blocked. "
                f"The sandbox level '{config.level.value}' does not allow administrative elevation (sudo/pkexec).",
                0.0,
            )

        # 2.5 Block self-management binaries except at Level 4 (host root)
        # This prevents skills from calling shani-deploy, shani-install-media, etc.
        if _name_matches(program, _INTERNAL_BINARIES) \
                and config.level != SandboxLevel.LEVEL_4_HOST_ROOT:
            return (
                126,
                f"Security error: Self-management tool '{os.path.basename(program)}' blocked. "
                f"The sandbox level '{config.level.value}' does not allow system management operations.",
                0.0,
            )

        # 3. Block internal manager binaries in isolated sandboxes
        is_isolated = config.level in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV)
        if is_isolated:
            if _name_matches(program, _INTERNAL_BINARIES):
                return (
                    126,
                    f"Security error: The internal management tool '{os.path.basename(program)}' "
                    f"is blocked in the sandbox '{config.level.value}'.",
                    0.0,
                )

        # 4. Explicit blocked binaries check
        blocked = _blocked_binary(argv, config.blocked_binaries)
        if blocked is not None:
            return (
                126,
                f"Security error: The command '{blocked}' is explicitly blocked in the agent's policy.",
                0.0,
            )

        # 5. Dangerous binary blocklist
        blocked = _blocked_binary(argv, DANGEROUS_BINARIES)
        if blocked is not None:
            return (
                126,
                f"Security error: The command '{blocked}' is blocked by the sandbox policy.",
                0.0,
            )

        # 5.5 Destructive argument shapes. A blocklist names
        # programs; it cannot see that an allowed program was
        # told to do something destructive, so the argv (and
        # any explicit shell script below) is matched against
        # high-confidence destructive patterns before anything
        # runs. Deterministic, like the blocklist: nothing
        # between this check and the exec can be talked out
        # of it.
        destructive = _destructive_pattern(" ".join(argv))
        if destructive is not None:
            return (
                126,
                f"Security error: refused - this command would {destructive}.",
                0.0,
            )
        sensitive = _sensitive_pattern(" ".join(argv))
        if sensitive is not None:
            return (
                126,
                f"Security error: refused - this command points at {sensitive!r}, which holds "
                "credentials or private keys.",
                0.0,
            )

        # 6. The explicit shell opt-in. Guard #5 above read argv[0], which for
        # `sh -c "..."` is just `sh` - so without this the opt-in would undo the
        # whole migration: `sh -c "mkfs.ext4 /dev/sda"` names the binary in plain
        # text and used to be refused by the old string filter. A script whose
        # program cannot be read at all is refused outright; one that can be read
        # is held to exactly the same blocklists as a direct execution.
        script = _shell_script(argv)
        if script is not None:
            unresolvable = _unresolvable_script(script)
            if unresolvable is not None:
                return (126, f"Security error: {unresolvable}.", 0.0)
            script_blocked = list(config.blocked_binaries) + list(DANGEROUS_BINARIES)
            if config.level != SandboxLevel.LEVEL_4_HOST_ROOT:
                script_blocked += ["sudo", "pkexec", "su"]
            if is_isolated:
                script_blocked += list(_INTERNAL_BINARIES)
            for word in script.split():
                if _name_matches(word, script_blocked):
                    return (
                        126,
                        f"Security error: The command '{os.path.basename(word)}' inside an "
                        f"explicit shell script is blocked by the sandbox policy.",
                        0.0,
                    )
            destructive = _destructive_pattern(script)
            if destructive is not None:
                return (
                    126,
                    f"Security error: refused - this script would {destructive}.",
                    0.0,
                )
            sensitive = _sensitive_pattern(script)
            if sensitive is not None:
                return (
                    126,
                    f"Security error: refused - this script points at {sensitive!r}, "
                    "which holds credentials or private keys.",
                    0.0,
                )
            # assistd `policy/review.rs:10-112`'s RISKY_BUILTINS, applied to the
            # one shape a builtin can arrive in: `sh -c '...'`. A builtin is not
            # a binary on disk, so the name blocklists above cannot see it, and an
            # eval/source inside a script is the same ask in a trench coat. Only
            # word positions where a command starts are considered, so the word
            # `set` inside `echo set` is prose, not a builtin call.
            for i, word in enumerate(script.split()):
                starts_command = i == 0 or script.split()[i - 1] in (";", "&&", "||", "|")
                if starts_command and _risky_builtin_name(word) is not None:
                    return (
                        126,
                        f"Security error: the shell builtin '{os.path.basename(word)}' "
                        "is not a program this sandbox will run inside a script.",
                        0.0,
                    )

        # 5.7 The same question, for a direct argv. A bare `eval` as argv[0] is
        # not a binary that exists, but refusing it with a reason beats failing
        # with ENOENT and leaves no ambiguity about intent.
        risky = _risky_builtin(argv)
        if risky is not None:
            return (
                126,
                f"Security error: the shell builtin '{risky}' is not a program this "
                "sandbox will run. Ask for what it was trying to do by name.",
                0.0,
            )

        # A level that promises isolation must never degrade to plain host
        # execution. Landlock counts: it is unprivileged and needs no user
        # namespaces, so confinement survives on hosts where bubblewrap cannot
        # create them. Only when NEITHER mechanism exists do we refuse - and we
        # refuse loudly rather than running unconfined while reporting success.
        if (
            config.level
            in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV)
            and not self.bwrap_available
            and not _landlock_abi() >= 1
        ):
            return (
                126,
                "Security error: this sandbox level needs bubblewrap or Landlock "
                "and neither is available. Install 'bubblewrap' or run on a "
                "kernel with Landlock (5.13+); refusing to run the command "
                "unconfined rather than pretending it was confined.",
                0.0,
            )

        # Resolved here, in the parent, so the decision about whether this profile
        # can be honoured is made before a child exists. A profile may only
        # tighten the caller's timeout - see `AgentProfile.ceiling`.
        ceiling, limits, ceiling_refusal = _resolve_ceiling(
            active, config.timeout_seconds)
        if ceiling_refusal is not None:
            return (126, f"Security error: {ceiling_refusal}", 0.0)
        timeout = ceiling.timeout_seconds

        # The seccomp gate, decided in the parent for the same reason as the
        # ceiling above: a setting that says "on" and then quietly runs the
        # command unfiltered is the defect this whole layer exists to prevent,
        # so when it is on and the filter cannot be built, the call is refused
        # before a child exists. Refusing *every* call is the point, not a
        # side effect - a partially filtered machine is the state to avoid.
        seccomp_wanted = _seccomp_requested()
        seccomp_applies = seccomp_wanted
        if seccomp_wanted and config.level == SandboxLevel.LEVEL_4_HOST_ROOT:
            seccomp_applies = False
            if config.level.value not in _SECCOMP_SKIPPED_LEVELS:
                _SECCOMP_SKIPPED_LEVELS.add(config.level.value)
                logger.warning(
                    "The '%s' setting is on but this call is at %s, where the "
                    "filter is deliberately not installed: seccomp requires "
                    "PR_SET_NO_NEW_PRIVS, which makes the kernel refuse setuid, "
                    "and setuid is how pkexec elevates. Installing it would "
                    "break this level silently. The command runs unfiltered.",
                    _seccomp.SECCOMP_SETTING, config.level.value)
        if seccomp_applies:
            reason = _seccomp.unavailable_reason()
            if reason is not None:
                return (
                    126,
                    f"Security error: the '{_seccomp.SECCOMP_SETTING}' setting "
                    f"is on, so this command must run under a seccomp filter, and "
                    f"one cannot be provided: {reason}. The command was not run. "
                    f"Turn the setting off to run commands without the filter.",
                    0.0,
                )
        logger.debug(
            "sandbox: profile=%s origin=%s level=%s tool=%r timeout=%ds "
            "(caller asked %ds, profile ceiling %ds, memory=%dMiB cpu=%ds)",
            active.name, origin, config.level.value, tool_name, timeout,
            config.timeout_seconds, active.timeout_seconds,
            active.memory_limit, ceiling.cpu_seconds)

        # 7. Level 4: Elevated Host with pkexec
        _PENDING_CEILING["limits"] = limits
        _PENDING_CEILING["profile"] = active.name
        _PENDING_CEILING["cpu_seconds"] = ceiling.cpu_seconds
        _PENDING_SECCOMP["enabled"] = seccomp_applies
        _report = None
        if seccomp_applies:
            _fd, _report = tempfile.mkstemp(prefix="chronoa-seccomp-", suffix=".refusal")
            os.close(_fd)
            _PENDING_SECCOMP["report"] = _report
        try:
            if config.level == SandboxLevel.LEVEL_4_HOST_ROOT:
                # `sudo X` becomes `pkexec X`; a program that is already pkexec is
                # left alone rather than wrapped twice.
                if os.path.basename(program) == "sudo":
                    argv = ["pkexec"] + argv[argv.index(program) + 1:]
                elif os.path.basename(program) != "pkexec":
                    argv = ["pkexec"] + argv
                return self._run_host(argv, timeout, start_time, elevated=True)

            # 8. Level 3: Host as Current User
            if config.level == SandboxLevel.LEVEL_3_HOST_USER:
                return self._run_host(argv, timeout, start_time, elevated=False)

            # Levels 1 and 2 exist to be isolated. If bubblewrap is missing they
            # cannot be, and running them anyway is the worst outcome available:
            # the caller asked for a sandbox, the sandbox silently did not exist,
            # and the only symptom is that the command worked. Refuse instead, and
            # name the package. LEVEL_3 is deliberately exempt - it means "host as
            # this user" with no isolation promised, because skills legitimately
            # need the session bus and display to reach `speak` or `open_application`.

            # 8. Level 1 & 2: confined.
            #
            # Landlock first, and it is not a fallback: it is unprivileged and needs
            # no user namespaces, so it confines on machines where bubblewrap cannot
            # run at all (hardened kernels, most container runtimes, and this
            # development container, where bwrap dies with
            # "loopback: Failed RTM_NEWADDR: Operation not permitted"). Requiring
            # bubblewrap for the isolated levels meant the levels were simply
            # unavailable wherever namespaces are restricted - the opposite of what
            # a confinement level is for.
            return self._run_landlock(
                argv, config, agent_id, timeout, start_time, use_bwrap=self.bwrap_available
            )
        finally:
            # Cleared here rather than inside the spawn helpers so that
            # `_run_host` can still read `_PENDING_CEILING` while it runs, which
            # is how a SIGXCPU gets a sentence naming the budget that caused it.
            # `_PENDING_SECCOMP` goes in the same breath: a stale `True` left
            # behind would filter the *next* command, which did not ask for it,
            # and a filter cannot be removed once installed.
            _PENDING_CEILING.clear()
            _PENDING_SECCOMP.clear()
            if _report:
                # Unlinked on both paths: the spawn may have failed, in which
                # case `_seccomp_child_failure` already read and removed it, or
                # it may have succeeded, in which case the empty file is ours to
                # clean up. A refusal reason left on disk would outlive the
                # command and read as a pending failure on the next one.
                try:
                    os.unlink(_report)
                except OSError:
                    pass

    def _run_landlock(
        self,
        argv: "list[str]",
        config: SandboxConfig,
        agent_id: str,
        timeout: int,
        start_time: float,
        use_bwrap: bool = False,
    ) -> Tuple[int, str, float]:
        """Confine via a Landlock wrapper that restricts, then execs the command.

        The wrapper is a separate program precisely so `landlock_restrict_self`
        runs in the child: it cannot be undone for the life of a process, so
        applying it here would permanently strip the app of filesystem access.
        """
        workspace = config.isolated_dir or os.path.join(self.sandboxes_root, agent_id)
        files.ensure_private_dir(workspace)

        from shani_chronoa.sandbox import landlock as _landlock

        paths = _landlock.get_default_allowed_paths(workspace)
        wrapper = os.path.join(workspace, ".chronoa-landlock-wrapper.py")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(_landlock.get_landlock_wrapper())
        # This is the sandbox enforcer, not a cache: it applies the filesystem
        # allowlist and then execs the command. At the umask it was created 0664,
        # so another local account could rewrite the confinement between this
        # write and the exec below - an integrity hole, not a disclosure one.
        # chmod after the write completes, never before: the handle is still open
        # here. 0700 rather than 0600 keeps the file executable as well as
        # private, so it survives being run directly if that ever happens - which
        # today it cannot be, since `inner_argv` puts `sys.executable` in argv[0]
        # and 0600 measured fine (see tests/test_state_file_permissions.py).
        try:
            os.chmod(wrapper, 0o700)
        except OSError as exc:
            logger.warning("Could not restrict permissions on %s: %s", wrapper, exc)

        # The wrapper ends in `os.execvp(sys.argv[1], sys.argv[1:])`, so the
        # command's own argv reaches the kernel untouched. This used to build a
        # shell string and run it with `shell=True`, wrapping a `/bin/sh -c` in
        # another shell - two parse steps between the policy check and the exec,
        # which is exactly where a string check stops meaning anything.
        inner_argv = [sys.executable, wrapper, *argv]
        use_bwrap = use_bwrap and _bwrap_usable()
        if use_bwrap:
            inner_argv = [
                "bwrap",
                "--ro-bind", "/", "/",
                "--bind", workspace, workspace,
                "--dev-bind", "/dev", "/dev",
                "--proc", "/proc",
                "--die-with-parent",
                "--",
                *inner_argv,
            ]
            # **Here, and not where `_PENDING_SECCOMP` is set.** That assignment
            # happens before this function is reached, and whether bubblewrap
            # will actually wrap the command is only known here - so deciding it
            # there would have had to re-derive the `_bwrap_usable()` answer.
            # The filter goes into the bwrap process's own `preexec_fn`, so it
            # would be installed before bwrap's `mount` calls; see
            # `_seccomp_conflicts_with_bwrap` for the measurement.
            if _PENDING_SECCOMP.get("enabled"):
                _PENDING_SECCOMP["enabled"] = False
                if config.level.value not in _SECCOMP_SKIPPED_FOR_BWRAP:
                    _SECCOMP_SKIPPED_FOR_BWRAP.add(config.level.value)
                    logger.warning(
                        "The '%s' setting is on but this call is confined by "
                        "bubblewrap, whose own setup needs mount(2) - a syscall "
                        "the filter denies, because Landlock has no mount right "
                        "in any ABI. Installing it would leave bubblewrap unable "
                        "to start ('Failed to make / slave'), so no confinement "
                        "ran at all. The command is still confined by the "
                        "namespace bubblewrap built; what is missing is the "
                        "second layer.",
                        _seccomp.SECCOMP_SETTING)

        env = redactor.child_env()
        env.update(
            {
                "LANLOCK_ALLOWED_PATHS": json.dumps([list(p) for p in paths]),
                "PYTHONPATH": _child_pythonpath(),
            }
        )
        for key in ("HOME", "USER", "LANG", "LC_ALL"):
            if key in os.environ and key not in env:
                env[key] = os.environ[key]

        try:
            _EXPECTED_PARENT["pid"] = os.getpid()
            try:
                # The Landlock wrapper is a Python interpreter that parses the
                # allowlist and holds the injected secrets before it execs
                # anything, so it needs the same hardening the host path gets.
                # Without a preexec_fn here it was the one spawn in this module
                # that left a child's dumps enabled.
                proc = subprocess.run(
                    inner_argv, env=env, capture_output=True, text=True,
                    timeout=timeout, preexec_fn=_harden_child,
                )
            finally:
                _EXPECTED_PARENT.clear()
        except subprocess.TimeoutExpired:
            return (124, f"Error: Command timed out after {timeout}s and was terminated.", 0.0)
        except Exception as exc:  # noqa: BLE001
            child = _seccomp_child_failure()
            if child is not None:
                return _seccomp_refusal(child)
            return (1, f"Execution error in the confined scope: {exc}", 0.0)

        duration = (time.monotonic() - start_time) * 1000.0
        out = proc.stdout or ""
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            detail = err[-1] if err else f"exit status {proc.returncode}"
            out = out or f"ERROR(exit={proc.returncode}): {detail}"
        return (proc.returncode, out, duration)

    def _run_host(
        self, argv: "list[str]", timeout: int, start_time: float, elevated: bool = False
    ) -> Tuple[int, str, float]:
        env = redactor.child_env()
        # Set outright, not added to the passthrough loop below. That loop only
        # copies a key when it is absent, and `inject_environment` has already
        # put the parent's PYTHONPATH there - so a passthrough entry for it
        # would silently do nothing, which is the shape of the bug this fixes.
        env["PYTHONPATH"] = _child_pythonpath()
        # GSETTINGS_* travel with the child because a skill's own consent gate
        # runs in that child, reading the same schema this process reads. Left
        # out, a run with a non-default schema directory - a build tree, or a
        # system that keeps its schemas somewhere unusual - gave the parent one
        # set of answers and the child another, so a gated skill refused for a
        # reason the parent could not reproduce. Same reasoning as PYTHONPATH
        # above: the child must import and read what the parent reads.
        # The XDG_* directories travel too, for the same reason as GSETTINGS_*:
        # a caller that redirects them - a test isolating its own writes, or a
        # run with a relocated data directory - got a child that ignored the
        # redirection and wrote to the real ~/.local/share instead. That made
        # the suite non-hermetic: a dispatched screenshot landed in the
        # developer's own home rather than the fixture directory.
        # XDG_SESSION_TYPE and the desktop names travel because the window
        # skills choose their backend from them (`shani_chronoa.windows.detect`):
        # without them a Plasma child could not tell it was on Plasma.
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
                  "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP",
                  "XDG_SESSION_TYPE", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION",
                  "KDE_FULL_SESSION",
                  "GSETTINGS_SCHEMA_DIR", "GSETTINGS_BACKEND",
                  "GSETTINGS_BACKEND_MEMORY", "GSETTINGS_KEYFILE_BACKEND",
                  "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                  "HOME", "USER"):
            if k in os.environ and k not in env:
                env[k] = os.environ[k]

        # A trailing `&` cannot appear in an argv - it is a shell operator, and
        # there is no shell here to interpret it. A caller that genuinely wants
        # background semantics now says so explicitly with `["sh", "-c", "... &"]`,
        # which is visible in the argv instead of hidden in a string. The two
        # launchers that return immediately are recognised by program.
        program = _program(argv)
        is_bg = bool(program) and os.path.basename(program) in _BACKGROUND_PROGRAMS
        if not is_bg:
            # A trailing `&` inside an explicit shell script still means
            # background, and dropping that would make the assistant *wait* for a
            # command it used to return from at once. It stays visible in the
            # argv rather than hiding in a command string.
            script = _shell_script(argv)
            is_bg = script is not None and script.rstrip().endswith("&")
        wait_limit = min(timeout, 2.5) if is_bg else timeout

        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as tmp_out, \
             tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as tmp_err:
            try:
                _EXPECTED_PARENT["pid"] = os.getpid()
                try:
                    proc = subprocess.Popen(
                        argv, env=env,
                        stdout=tmp_out, stderr=tmp_err,
                        start_new_session=True,
                        preexec_fn=_harden_child,
                    )
                finally:
                    _EXPECTED_PARENT.clear()

                poll_start = time.monotonic()
                while time.monotonic() - poll_start < wait_limit:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.08)

                duration = (time.monotonic() - start_time) * 1000.0
                retcode = proc.poll()

                if retcode is not None:
                    tmp_out.seek(0)
                    tmp_err.seek(0)
                    out_text = tmp_out.read().strip()
                    err_text = tmp_err.read().strip()
                    combined = (out_text + "\n" + err_text).strip()

                    if retcode in (126, 127) and elevated:
                        combined = "The user cancelled or denied the graphical administrator authorization (Polkit)."
                    elif retcode < 0 and -retcode == _SIGXCPU:
                        # The kernel reports a soft `RLIMIT_CPU` breach as
                        # `SIGXCPU`, whose default action terminates. Left
                        # untranslated the caller sees exit -24, which is not
                        # distinguishable from the child having been signalled
                        # by something else - so the ceiling is named, and
                        # with it the fact that this was a policy decision.
                        budget = _PENDING_CEILING.get("cpu_seconds")
                        ceiling_note = (
                            f" after {budget} CPU-seconds"
                            if budget is not None else ""
                        )
                        combined = (
                            f"Killed by the sandbox profile's CPU-time ceiling"
                            f"{ceiling_note} (SIGXCPU), not by the command's own "
                            f"{timeout}s timeout. The command used all the CPU "
                            f"that profile allows."
                        )
                    elif retcode != 0:
                        if not combined:
                            combined = f"(Process exited with error code {retcode})"
                    elif not combined:
                        combined = "(Command completed with exit code 0)"

                    return (retcode, combined, duration)

                if is_bg:
                    tmp_out.seek(0)
                    tmp_err.seek(0)
                    partial_out = tmp_out.read().strip()
                    partial_err = tmp_err.read().strip()
                    combined_partial = (partial_out + "\n" + partial_err).strip()

                    msg = f"Command started and continues running in the background (PID: {proc.pid})."
                    if combined_partial:
                        msg += f"\nOutput:\n{combined_partial}"

                    return (0, msg, duration)

                # Foreground command exceeded its timeout: kill the whole process
                # group, not just the direct child, or its own children survive
                # proc.kill() - rather than silently reporting success while it
                # keeps running unbounded. `start_new_session=True` puts the
                # child in its own group, which is what makes the group reachable.
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

                duration = (time.monotonic() - start_time) * 1000.0
                return (124, f"Error: Command timed out after {timeout}s and was terminated.", duration)

            except Exception as exc:
                duration = (time.monotonic() - start_time) * 1000.0
                child = _seccomp_child_failure()
                if child is not None:
                    return (*_seccomp_refusal(child)[:2], duration)
                return (1, f"Execution error on the host: {exc}", duration)

    def _run_bwrap(
        self,
        argv: "list[str]",
        config: SandboxConfig,
        agent_id: str,
        timeout: int,
        start_time: float,
    ) -> Tuple[int, str, float]:
        workspace = config.isolated_dir or os.path.join(self.sandboxes_root, agent_id)
        files.ensure_private_dir(workspace)

        bwrap_args = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--tmpfs", "/run",
            "--bind", workspace, workspace,
            "--chdir", workspace,
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
        ]

        if not config.allow_network:
            bwrap_args.append("--unshare-net")

        # Straight to the program. This used to append `["--", "bash", "-c", cmd]`,
        # which put a shell back between the policy check and the exec and made
        # this the one path the argv migration would have quietly left open.
        bwrap_args.extend(["--", *argv])

        try:
            res = subprocess.run(
                bwrap_args, capture_output=True, text=True,
                timeout=timeout,
                env=redactor.child_env(),
            )
            out = (res.stdout + "\n" + res.stderr).strip()
            retcode = res.returncode

            # Check for graphical display connection failures
            display_error_indicators = (
                "Failed to open display", "Cannot open display",
                "Unable to init server", "Authorization required, but no authorization protocol specified",
                "could not connect to display", "No protocol specified",
            )
            if any(ind in out for ind in display_error_indicators):
                retcode = 126
                out = (
                    f"Security/Sandbox error ({config.level.value}): Cannot open a graphical window "
                    f"from this isolated container (no access to Wayland/X11).\n"
                    f"To open desktop applications on the user's screen, LEVEL_3_HOST_USER is required.\n"
                    f"Technical detail: {out}"
                )
            elif not out:
                out = f"(Sandbox bwrap finished with exit code {retcode})"

            duration = (time.monotonic() - start_time) * 1000.0
            return (retcode, out, duration)
        except subprocess.TimeoutExpired:
            duration = (time.monotonic() - start_time) * 1000.0
            return (124, f"Error: Sandbox time limit ({timeout}s) exceeded.", duration)
        except Exception as exc:
            duration = (time.monotonic() - start_time) * 1000.0
            return (1, f"bwrap sandbox error: {exc}", duration)
