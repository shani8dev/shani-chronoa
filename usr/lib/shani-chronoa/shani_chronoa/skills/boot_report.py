"""Skill: why is booting slow, and did the machine shut down cleanly?

Two halves:

- the `boots` sense - when the machine last booted, how often, and whether the
  previous shutdown was clean - background context until now;
- `systemd-analyze time` and `systemd-analyze blame`, which nothing in Chronoa
  called: how long firmware, loader, kernel and userspace each took, and which
  services took longest to start.

`blame` is a list of start times, not of culprits: services start in parallel,
so a slow one is not necessarily what made the boot slow. That is said in the
reply, because "the slowest unit" reads like "the cause" and often is not.

The boot-history half follows the `boots` sense's switch (on by default) - see
`sense_reading.py`; the timing half needs none.

Honesty rules: `systemd-analyze time` refuses while the boot has not finished
("Bootup is not yet finished"), and that refusal is reported as itself.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 20
_DEFAULT_TOP = 10
_MAX_TOP = 30

SCHEMA = {
    "type": "function",
    "function": {
        "name": "boot_report",
        "description": (
            "How the last boot went: total boot time split into firmware, "
            "loader, kernel and userspace, the services that took longest to "
            "start, when the machine booted and whether it shut down cleanly "
            "before. Use for 'why does it boot slowly', 'did it crash last time'. "
            "Boot history uses the 'boots-sense-enabled' switch. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "top": {"type": "integer",
                        "description": f"How many of the slowest services to list (default {_DEFAULT_TOP})."},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the boots sense's switch (see sense_reading.py)."""
    if config.sense_allowed("boots"):
        return True, ""
    return False, sense_reading.refusal(config, "boots")


def _analyze(*args: str) -> "tuple[str | None, str]":
    try:
        proc = subprocess.run(["systemd-analyze", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, f"did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return None, str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, detail[-1] if detail else f"exit {proc.returncode}"
    return proc.stdout, ""


def _top(arguments: dict) -> int:
    try:
        n = int(arguments.get("top") or _DEFAULT_TOP)
    except (TypeError, ValueError):
        n = _DEFAULT_TOP
    return max(1, min(n, _MAX_TOP))


def _run(arguments: dict) -> str:
    lines = []
    if shutil.which("systemd-analyze") is None:
        lines.append("systemd-analyze is not installed, so boot timing is UNKNOWN.")
    else:
        time_out, why = _analyze("time")
        if time_out is None:
            lines.append(f"Boot timing is UNKNOWN: systemd-analyze time said {why!r}.")
        else:
            first = time_out.strip().splitlines()
            lines.append(first[0].strip() if first else "systemd-analyze time printed nothing.")
            lines.extend(l.strip() for l in first[1:] if l.strip())
        blame, why = _analyze("blame", "--no-pager")
        n = _top(arguments)
        if blame is None:
            lines.append(f"Per-service start times are UNKNOWN: {why}.")
        else:
            rows = [l.strip() for l in blame.splitlines() if l.strip()][:n]
            lines.append(f"The {len(rows)} services that took longest to start "
                         "(they start in parallel, so the slowest is not necessarily "
                         "what delayed the boot - check the userspace total above):")
            lines.extend(f"  {row}" for row in rows)
    lines.append("")
    config = ChronoaConfig()
    allowed, why = _consent(config)
    lines.append(sense_reading.reading("boots") if allowed else why)
    return "\n".join(lines)


SKILLS = [Skill(name="boot_report", schema=SCHEMA, run=_run)]
