"""Skill: stop a running process.

The second most destructive action in the project after deleting files, and
gated for the same reason: a wrong pid takes down unsaved work, and nothing here
can tell a saved document from an unsaved one.

`SIGTERM` by default, not `SIGKILL`. A process asked to terminate gets the
chance to flush what it is holding, which is the difference between closing a
document and losing it. `SIGKILL` is available but has to be asked for by name,
so it is never the default a model reaches for by omission.

Consent is checked before the pid is looked at, so a refusal does not leak
whether a process exists.

Honesty rules: a process that has already exited is reported as gone, not as a
failure. A permission refusal names the fact that the process exists and belongs
to someone else, because that is the useful answer.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "process-kill-enabled"
_VALID_SIGNALS = ("TERM", "KILL", "INT", "HUP")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "kill_process",
        "description": (
            "Stop a running process by pid, asking it to exit cleanly. Use "
            "list_processes to find the pid first. Requires the "
            "'process-kill-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "The process id to stop."},
                "signal_name": {
                    "type": "string",
                    "description": (
                        "Which signal: TERM (default, clean), INT, HUP, or KILL "
                        "(forces, no chance to save). "
                        f"Valid: {', '.join(_VALID_SIGNALS)}."
                    ),
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"stopping processes is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Listing processes needs no such permission, and neither "
            f"does reading files or changing the volume."
        )
    return True, ""


def _identity(pid: str) -> str:
    try:
        comm = (Path("/proc") / pid / "comm").read_text().strip()
    except OSError:
        return "unknown"
    return comm or "unknown"


def _is_running(pid: str) -> bool:
    """Whether the pid is a *live* process, not merely an entry in /proc.

    The obvious check - does `/proc/<pid>` exist - is wrong, and wrong in the
    direction that matters here. A process that has been killed but not yet
    reaped by its parent keeps a `/proc/<pid>` entry with state `Z`, so
    existence says "alive" for a process that is already dead. Reporting that
    as "still running, try KILL" sends the caller after a pid that is finished
    and will never respond.

    Verified by running it: `subprocess.Popen(["sleep", "300"])` followed by
    `os.kill(pid, SIGTERM)` left `Popen.poll()` at -15 while
    `os.path.exists("/proc/<pid>")` still returned True.
    """
    stat = Path("/proc", pid, "stat")
    try:
        raw = stat.read_text()
    except OSError:
        return False
    # comm can contain spaces and parentheses, so split after the final ')'
    tail = raw.rsplit(")", 1)[-1].split()
    if not tail:
        return False
    return tail[0] != "Z"


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to stop the process: {reason}"

    raw_pid = arguments.get("pid")
    try:
        pid = int(raw_pid)
    except (TypeError, ValueError):
        return f"Pid must be a whole number, not {raw_pid!r}. Use list_processes."
    if pid <= 0:
        return f"Pid {pid} is not a valid process id."
    name = (arguments.get("signal_name") or "TERM").strip().upper()
    if name not in _VALID_SIGNALS:
        return f"Signal must be one of {', '.join(_VALID_SIGNALS)}, not {name!r}."

    if not _is_running(str(pid)):
        return f"No process with pid {pid} is running; it may have already exited."

    what = f"pid {pid} ({_identity(str(pid))})"
    if pid == os.getpid():
        return f"Refusing to stop {what}: that is Chronoa itself."

    try:
        os.kill(pid, getattr(signal, f"SIG{name}"))
    except ProcessLookupError:
        return f"{what} had already exited when the signal was sent."
    except PermissionError:
        return (
            f"Could not signal {what}: permission denied. It is running, but it "
            f"belongs to another user. Stopping it needs their privileges."
        )
    except OSError as exc:
        return f"Could not signal {what}: {exc}"

    if name == "KILL":
        return f"Sent SIGKILL to {what}: it had no chance to clean up."
    deadline = time.time() + 2.0
    while time.time() < deadline and _is_running(str(pid)):
        time.sleep(0.05)
    if _is_running(str(pid)):
        return (
            f"Sent SIG{name} to {what} and it is still running after 2s. A clean "
            f"shutdown can take longer than that, so this is not yet a failure - "
            f"but use signal_name KILL to force it if it will not go."
        )
    return f"Sent SIG{name} to {what} and it has exited."


SKILLS = [Skill(name="kill_process", schema=SCHEMA, run=_run)]
