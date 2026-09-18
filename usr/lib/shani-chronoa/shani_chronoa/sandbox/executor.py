"""Bubblewrap (bwrap) and Host Sandbox Executor for Shani Chronoa.

Adapted from sayri/adapters/sandbox/executor.py with shani-chronoa paths.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Tuple

from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel
from shani_chronoa.secrets_manager import secrets_manager


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
        for blocked in config.blocked_binaries:
            if blocked in raw_cmd.split():
                return (
                    126,
                    f"Security error: The command '{blocked}' is explicitly blocked in the agent's policy.",
                    0.0,
                )

        # 5. Dangerous binary blocklist
        DANGEROUS_BINARIES = ("mkfs", "dd", "shutdown", "reboot", "mount", "umount")
        for blocked in DANGEROUS_BINARIES:
            if blocked in raw_cmd.split():
                return (
                    126,
                    f"Security error: The command '{blocked}' is blocked by the sandbox policy.",
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
        if config.level == SandboxLevel.LEVEL_3_HOST_USER or not self.bwrap_available:
            return self._run_host(raw_cmd, timeout, start_time, elevated=False)

        # 8. Level 1 & 2: Sandboxed with Bubblewrap
        return self._run_bwrap(raw_cmd, config, agent_id, timeout, start_time)

    def _run_host(
        self, command: str, timeout: int, start_time: float, elevated: bool = False
    ) -> Tuple[int, str, float]:
        env = secrets_manager.inject_environment()
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
