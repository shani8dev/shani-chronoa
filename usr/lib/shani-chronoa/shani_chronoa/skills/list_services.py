"""Skill: list the system services and their state.

The `services` *sense* is polled and deposited on a timer, and reports only what
has failed - a failure list is what belongs in context unasked, because a list
of 200 healthy services is not something to spend anyone's attention on. This
answers a direct question and returns the whole list, including the healthy ones.

Honesty rules: no systemd is UNKNOWN rather than an empty list, and a unit whose
state cannot be read is shown as unknown rather than assumed inactive.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 20
_MAX = 80

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_services",
        "description": (
            "List system services with their load and active state, optionally "
            "filtered by name, newest state first for anything running. "
            "Reports UNKNOWN on a machine that is not systemd-managed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name_filter": {
                    "type": "string",
                    "description": "Only units whose name contains this.",
                },
                "failed_only": {
                    "type": "boolean",
                    "description": "Only units in the failed state. Defaults to false.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    if shutil.which("systemctl") is None:
        return (
            "Could not list services: systemctl is not installed, so this "
            "machine is not systemd-managed. That is an empty answer to a "
            "question that was not asked, not an empty list of services."
        )
    args = ["systemctl", "list-units", "--type=service", "--no-legend",
            "--no-pager", "--plain", "--all"]
    if arguments.get("failed_only"):
        args.insert(2, "--state=failed")
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"systemctl did not answer within {_TIMEOUT}s, so no services are listed."
    except OSError as exc:
        return f"Could not list services: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return f"systemctl could not list services (exit {proc.returncode})" + (
            f": {detail[-1]}" if detail else ".")

    needle = (arguments.get("name_filter") or "").strip().lower()
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        if needle and needle not in unit.lower():
            continue
        rows.append((unit, load, active, sub))

    if not rows:
        if needle:
            return f"No service unit's name contains {needle!r}."
        return "systemctl returned no service units, which is not expected on a running system."

    # Anything running is what a person usually wants to see first.
    order = {"failed": 0, "activating": 1, "deactivating": 2, "active": 3, "inactive": 4}
    rows.sort(key=lambda r: (order.get(r[2], 5), r[0]))
    shown = rows[:_MAX]
    lines = [f"{len(rows)} service unit(s); showing {len(shown)} (running first):"]
    for unit, load, active, sub in shown:
        note = "" if sub == active or not sub else f" ({sub})"
        lines.append(f"  {unit:<44} {active:<12}{note}")
    if len(rows) > len(shown):
        lines.append(f"  ... {len(rows) - len(shown)} more not shown (limit {_MAX}).")
    lines.append("  Use read_logs for a unit's output, control_service to change it.")
    return "\n".join(lines)


SKILLS = [Skill(name="list_services", schema=SCHEMA, run=_run)]
