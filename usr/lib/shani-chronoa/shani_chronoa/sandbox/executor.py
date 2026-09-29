"""Bubblewrap (bwrap) and Host Sandbox Executor for Shani Chronoa.

Adapted from sayri/adapters/sandbox/executor.py with shani-chronoa paths.
"""

from __future__ import annotations

import json
import os
import shlex
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


def _first_blocked_binary(command: str, blocked_names) -> "str | None":
    """The first blocklisted binary `command` would actually run, if any.

    Matching on `blocked in command.split()` looks right and is not. It
    compares whole tokens, so it blocks the bare word `mkfs` and sails
    straight past `mkfs.ext4` - which is how mkfs is invoked in every real
    command, and the only form anyone types. The same gap let `/sbin/dd` and
    `/bin/mount` through. Verified rather than assumed: with exact-token
    matching, `mkfs.ext4 /dev/sda` reached the executor and ran.

    So compare the *basename* of each token, and treat a dotted variant
    (`mkfs.ext4`, `mount.fuse`) as the same binary it is named after. Still no
    substring matching: `add` and `ddrescue` are not `dd`.
    """
    for token in command.split():
        name = token.rsplit("/", 1)[-1]
        for blocked in blocked_names:
            if name == blocked or name.startswith(blocked + "."):
                return name
    return None


class SandboxExecutionError(Exception):
    pass


class SandboxExecutor:
    """Executes commands adhering to fine-grained SandboxLevel configurations."""

    def __init__(self, sandboxes_root: str | None = None) -> None:
        self.sandboxes_root = sandboxes_root or os.path.expanduser("~/.local/share/shani-chronoa/sandboxes")
        os.makedirs(self.sandboxes_root, exist_ok=True)
        self.bwrap_available = bool(shutil.which("bwrap"))

    def execute(
        self,
        command: str,
        config: SandboxConfig,
        agent_id: str = "default",
    ) -> Tuple[int, str, float]:
        """Executes a command under the specified sandbox level.

        Returns: (exit_code, output, duration_ms)
        """
        start_time = time.monotonic()
        raw_cmd = command.strip()

        # 1. Level 0: Total Prohibition
        if config.level == SandboxLevel.LEVEL_0_NO_EXEC:
            return (
                126,
                "Security error (LEVEL_0_NO_EXEC): This sub-agent has the LEVEL_0_NO_EXEC level configured. "
                "It has no permissions to execute commands on the system.",
                0.0,
            )

        # 2. Prevent Privilege Escalation for non-Level 4
        if ("sudo " in raw_cmd or "pkexec " in raw_cmd or raw_cmd.startswith("su ") or " su " in raw_cmd) \
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
            internal_blocked = ("shani-skills", "shani-plugins", "shani-settings", "shani", "pkill", "killall")
            cmd_words = set(raw_cmd.split())
            for b in internal_blocked:
                if b in cmd_words or f"/{b}" in raw_cmd:
                    return (
                        126,
                        f"Security error: The internal management tool '{b}' is blocked in the sandbox '{config.level.value}'.",
                        0.0,
                    )

        # 4. Explicit blocked binaries check
        blocked = _first_blocked_binary(raw_cmd, config.blocked_binaries)
        if blocked is not None:
            return (
                126,
                f"Security error: The command '{blocked}' is explicitly blocked in the agent's policy.",
                0.0,
            )

        # 5. Dangerous binary blocklist
        blocked = _first_blocked_binary(raw_cmd, DANGEROUS_BINARIES)
        if blocked is not None:
            return (
                126,
                f"Security error: The command '{blocked}' is blocked by the sandbox policy.",
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

        # 6. Level 4: Elevated Host with pkexec
        if config.level == SandboxLevel.LEVEL_4_HOST_ROOT:
            if raw_cmd.startswith("sudo "):
                raw_cmd = "pkexec " + raw_cmd[5:]
            elif not raw_cmd.startswith("pkexec "):
                raw_cmd = "pkexec " + raw_cmd
            return self._run_host(raw_cmd, timeout, start_time, elevated=True)

        # 7. Level 3: Host as Current User
        if config.level == SandboxLevel.LEVEL_3_HOST_USER:
            return self._run_host(raw_cmd, timeout, start_time, elevated=False)

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
            raw_cmd, config, agent_id, timeout, start_time, use_bwrap=self.bwrap_available
        )

    def _run_landlock(
        self,
        command: str,
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

        # exec a shell rather than the command itself: `command` is a shell
        # string, and the wrapper takes argv. The ruleset is already in force by
        # the time /bin/sh runs.
        inner = f"{shlex.quote(sys.executable)} {shlex.quote(wrapper)} /bin/sh -c {shlex.quote(command)}"
        use_bwrap = use_bwrap and _bwrap_usable()
        if use_bwrap:
            inner = (
                "bwrap --ro-bind / / --bind " + shlex.quote(workspace) + " "
                + workspace + " --dev-bind /dev /dev --proc /proc --die-with-parent -- "
                + inner
            )

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
                inner, shell=True, env=env, capture_output=True, text=True, timeout=timeout
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
        self, command: str, timeout: int, start_time: float, elevated: bool = False
    ) -> Tuple[int, str, float]:
        env = secrets_manager.inject_environment()
        # Set outright, not added to the passthrough loop below. That loop only
        # copies a key when it is absent, and `inject_environment` has already
        # put the parent's PYTHONPATH there - so a passthrough entry for it
        # would silently do nothing, which is the shape of the bug this fixes.
        env["PYTHONPATH"] = _child_pythonpath()
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
                  "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP",
                  "HOME", "USER"):
            if k in os.environ and k not in env:
                env[k] = os.environ[k]

        is_bg = command.rstrip().endswith("&") or command.startswith("gtk-launch ") or command.startswith("xdg-open ")
        wait_limit = min(timeout, 2.5) if is_bg else timeout

        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as tmp_out, \
             tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as tmp_err:
            try:
                proc = subprocess.Popen(
                    command, shell=True, env=env,
                    stdout=tmp_out, stderr=tmp_err,
                    start_new_session=True,
                )

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

                # Foreground command exceeded its timeout: kill the whole
                # process group (shell=True spawns a shell whose children
                # would otherwise survive proc.kill()) rather than silently
                # reporting success while it keeps running unbounded.
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
        command: str,
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

        sync_command = command.strip()
        if sync_command.endswith("&"):
            sync_command = sync_command[:-1].strip()

        bwrap_args.extend(["--", "bash", "-c", sync_command])

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
