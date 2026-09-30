"""Bubblewrap (bwrap) and Host Sandbox Executor for Shani Chronoa.

Adapted from sayri/adapters/sandbox/executor.py with shani-chronoa paths.
"""

from __future__ import annotations

import json
import ctypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Tuple

from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel
from shani_chronoa.secrets_manager import secrets_manager


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
    `secrets_manager.inject_environment()` copies the parent environment, so a
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


#: A leading `VAR=value` word, as in `FOO=bar python3 ...`.
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Programs whose meaning depends on a script string this module cannot resolve.
_SHELL_PROGRAMS = ("sh", "bash", "dash", "zsh", "ksh")

#: Constructs that hide the real program from any static reading of a script.
_EXPANSION = ("$(", "`", "${")

#: Launchers that return immediately instead of waiting for the program.
_BACKGROUND_PROGRAMS = ("gtk-launch", "xdg-open")

#: Chronoa's own management tools, which an isolated sandbox may not reach.
#: Named once because guard 3 and guard 6 both apply it and the two must not
#: drift apart - that is how the shell opt-in came to bypass the escalation
#: check in the first place.
_INTERNAL_BINARIES = ("shani-skills", "shani-plugins", "shani-settings",
                      "shani", "pkill", "killall")


def _program(argv: "list[str]") -> str:
    """The program this argv will actually exec.

    `env FOO=bar python3 ...` and `FOO=bar python3 ...` both run python3, so a
    guard that read only argv[0] would see `env`, match nothing, and wave the
    real program straight through. Leading assignments and one leading `env` are
    therefore skipped to find the program the kernel will exec.
    """
    index = 0
    while index < len(argv):
        word = argv[index]
        if index == 0 and os.path.basename(word) == "env":
            index += 1
            continue
        if _ENV_ASSIGNMENT.match(word):
            index += 1
            continue
        break
    return argv[index] if index < len(argv) else ""


def _name_matches(name: str, blocked_names) -> bool:
    """Whether `name` is one of `blocked_names`.

    Basename first, so `/sbin/dd` is `dd`. Still no substring matching, so `add`
    and `ddrescue` are not `dd`; and a dotted variant (`mkfs.ext4`,
    `mount.fuse`) counts as the binary it is named after, which is the form
    anyone actually types.
    """
    base = os.path.basename(name)
    return any(base == blocked or base.startswith(blocked + ".") for blocked in blocked_names)


def _blocked_binary(argv: "list[str]", blocked_names) -> "str | None":
    r"""The blocklisted program this argv would run, if any.

    argv[0] and nothing else. The previous version split a *shell string* on
    whitespace and compared tokens, which meant the check was defeated by
    writing the name in any form a shell would expand: measured against the real
    executor, `dd status=...` was refused with 126 while `$(echo dd) status=...`
    and `d\d status=...` both reached the real system `dd`, which then complained
    about its own arguments. There is no spelling of `dd` that reaches argv[0]
    here without being the program that runs.
    """
    program = _program(argv)
    if program and _name_matches(program, blocked_names):
        return os.path.basename(program)
    return None


def _shell_script(argv: "list[str]") -> "str | None":
    """The script of an explicit `sh -c ...` argv, or None if this is not one.

    The shell stays reachable on purpose, because a blocklist never could police
    a shell string - so the honest move is to make the shell *visible* in the argv
    rather than keep a filter that appears to work. Everything below exists to
    stop that visibility from being a hole.
    """
    program = _program(argv)
    if os.path.basename(program) not in _SHELL_PROGRAMS:
        return None
    try:
        return argv[argv.index("-c") + 1]
    except (ValueError, IndexError):
        return None


def _unresolvable_script(script: str) -> "str | None":
    """Why a literal shell script cannot be policed, or None if it can be.

    An expansion construct makes the program unknowable by reading the text: that
    is precisely how the previous string filter was walked past, since
    `$(echo dd) ...` names no blocked binary anywhere in the string. Rather than
    guess, the script is refused. The cost is real and deliberate - `sh -c "echo
    $(date)"` is refused too - and the alternative is a check that silently fails
    on the input it exists to catch.
    """
    for construct in _EXPANSION:
        if construct in script:
            return (
                f"an explicit shell script contains {construct!r}, so the program it "
                "would run is not knowable from the argv; pass the program directly "
                "instead of a shell string"
            )
    return None


class SandboxExecutionError(Exception):
    pass


#: `prctl(2)` option, and the signal it takes. `PR_SET_PDEATHSIG` makes the kernel
#: deliver a signal to this process when its parent dies. Linux-only, and not in POSIX.
_PR_SET_PDEATHSIG = 1
_SIGTERM = 15

#: The parent pid a freshly forked child should still see. `preexec_fn` is handed no
#: arguments, so the executor records the parent immediately before spawning and the
#: child compares against it. Cleared as soon as `Popen` returns, because after that
#: the value is meaningless and a recycled dict would only invite confusion.
_EXPECTED_PARENT: "dict" = {}


def _die_with_parent() -> None:
    """Ask the kernel to signal this child if its parent dies.

    `start_new_session=True` is what makes the timeout fix work - it gives the child its
    own process group, so `os.killpg` reaches a whole `sh -c` tree rather than hitting
    Chronoa's own group. It also detaches the child from that group, so killing Chronoa
    does not reach it, and a backgrounded command is the reachable case: the executor
    returns at once, reports "continues running in the background", and keeps no record
    beyond that string. Measured on this machine - a backgrounded `sleep 4; touch MARKER`
    still wrote its marker long after the executor returned.

    The race is the part that is easy to get wrong. If the parent dies between `fork` and
    this call, the signal is never armed and nothing else would ever notice, so the check
    below compares `getppid()` against the pid recorded before the spawn and exits at
    once if they differ. Without it the fix covers the common case and silently misses
    the one where it matters most.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl.restype = ctypes.c_int
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.c_ulong, ctypes.c_ulong]
    except OSError:
        return  # no libc to ask; the child is no worse off than it was before
    try:
        if libc.prctl(_PR_SET_PDEATHSIG, _SIGTERM, 0, 0, 0) != 0:
            return  # unsupported or not permitted; not worth failing the command over
        expected = _EXPECTED_PARENT.get("pid")
        if expected is not None and os.getppid() != expected:
            os._exit(0)  # the parent died before the arm took effect
    except OSError:
        return


class SandboxExecutor:
    """Executes commands adhering to fine-grained SandboxLevel configurations."""

    def __init__(self, sandboxes_root: str | None = None) -> None:
        self.sandboxes_root = sandboxes_root or os.path.expanduser("~/.local/share/shani-chronoa/sandboxes")
        os.makedirs(self.sandboxes_root, exist_ok=True)
        self.bwrap_available = bool(shutil.which("bwrap"))

    def execute(
        self,
        argv: "list[str]",
        config: SandboxConfig,
        agent_id: str = "default",
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

        timeout = max(1, config.timeout_seconds)

        # 7. Level 4: Elevated Host with pkexec
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
        os.makedirs(workspace, exist_ok=True)

        from shani_chronoa.sandbox import landlock as _landlock

        paths = _landlock.get_default_allowed_paths(workspace)
        wrapper = os.path.join(workspace, ".chronoa-landlock-wrapper.py")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(_landlock.get_landlock_wrapper())

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

        env = secrets_manager.inject_environment()
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
            proc = subprocess.run(
                inner_argv, env=env, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return (124, f"Error: Command timed out after {timeout}s and was terminated.", 0.0)
        except Exception as exc:  # noqa: BLE001
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
        env = secrets_manager.inject_environment()
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
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
                  "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP",
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
        is_bg = os.path.basename(_program(argv)) in _BACKGROUND_PROGRAMS
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
                        preexec_fn=_die_with_parent,
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
                    elif retcode != 0:
                        if not combined:
                            combined = f"(Process exited with error code {retcode})"
                    elif not combined:
                        combined = f"(Command completed with exit code 0)"

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
        os.makedirs(workspace, exist_ok=True)

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
                env=secrets_manager.inject_environment(allowed_keys=[]),
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
        except subprocess.TimeoutExpired as exc:
            duration = (time.monotonic() - start_time) * 1000.0
            return (124, f"Error: Sandbox time limit ({timeout}s) exceeded.", duration)
        except Exception as exc:
            duration = (time.monotonic() - start_time) * 1000.0
            return (1, f"bwrap sandbox error: {exc}", duration)
