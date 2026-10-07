"""Skill: what is waiting to print, and cancel a stuck job.

`print_file` sends a job; nothing could see what happened to it afterwards. A
job stuck at the head of the queue blocks everything behind it, and "cancel
that print" is the fix people ask for. CUPS' own `lpstat` and `cancel` are on
every image that can print.

Cancelling is gated by `print-control-enabled`: a cancelled job cannot be
resumed, and on a shared printer it may not be the user's own. Listing needs no
permission.

Honesty rules: a job is cancelled only if it is in the queue *now* (a stale id
is refused rather than passed on); the queue is read again afterwards and a job
still listed is reported as not cancelled; no CUPS scheduler is reported as
"the queue could not be read", never as an empty queue.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "print-control-enabled"
_TIMEOUT = 15
_JOB_ID = re.compile(r"^[A-Za-z0-9_.@-]+-\d+$|^\d+$")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "print_queue",
        "description": (
            "Show the printers, which one is the default, and the print jobs "
            "still waiting; or cancel a waiting job by its id. Use for 'what's "
            "printing', 'why isn't my document printing', 'cancel that print'. "
            "Cancelling requires the 'print-control-enabled' consent key; "
            "listing does not."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "cancel"],
                           "description": "list (default) or cancel."},
                "job": {"type": "string", "description": "cancel: the job id, e.g. 'Office_Laser-42'."},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"cancelling print jobs is turned off (enable '{_CONSENT_KEY}' in "
                       f"Settings). A cancelled job cannot be resumed.")
    return True, ""


def _lpstat(*args: str) -> "str | None":
    try:
        proc = subprocess.run(["lpstat", *args], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def queued_jobs() -> "list[dict] | None":
    out = _lpstat("-o")
    if out is None:
        return None
    jobs = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            jobs.append({"id": parts[0], "user": parts[1], "bytes": parts[2],
                         "when": parts[3].strip() if len(parts) > 3 else ""})
    return jobs


def _matches(job: dict, wanted: str) -> bool:
    return job["id"] == wanted or (wanted.isdigit() and job["id"].rsplit("-", 1)[-1] == wanted)


def _list() -> str:
    jobs = queued_jobs()
    if jobs is None:
        return "The print queue could not be read (is CUPS running?), so it is UNKNOWN, not empty."
    lines = []
    default = _lpstat("-d")
    if default:
        lines.append(default.strip())
    printers = _lpstat("-p")
    if printers:
        lines.extend(l.strip() for l in printers.splitlines() if l.startswith("printer"))
    if not jobs:
        lines.append("No print jobs are waiting.")
    else:
        lines.append(f"{len(jobs)} job(s) waiting:")
        lines.extend(f"  {j['id']:<24} {j['user']:<12} {j['when']}" for j in jobs)
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    if shutil.which("lpstat") is None:
        return files.tool_missing("lpstat", "read the print queue")
    action = (arguments.get("action") or "list").strip().lower()
    if action == "list":
        return _list()
    if action != "cancel":
        return f"Action must be list or cancel, not {action!r}."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to cancel: {reason}"
    wanted = (arguments.get("job") or "").strip()
    if not _JOB_ID.match(wanted):
        return f"{wanted!r} is not a print job id; list the queue to see the ids."
    jobs = queued_jobs()
    if jobs is None:
        return "The print queue could not be read, so nothing was cancelled."
    job = next((j for j in jobs if _matches(j, wanted)), None)
    if job is None:
        return f"No waiting job has the id {wanted!r} (it may already have printed), so nothing was cancelled."
    if shutil.which("cancel") is None:
        return files.tool_missing("cancel", "cancel a print job")
    try:
        proc = subprocess.run(["cancel", job["id"]], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cancel did not finish ({exc}); the job may still be queued."
    if proc.returncode != 0:
        return f"CUPS refused to cancel {job['id']}: {(proc.stderr or '').strip() or 'no detail'}."
    after = queued_jobs()
    if after is not None and not any(j["id"] == job["id"] for j in after):
        return f"Cancelled {job['id']} (verified: it is no longer in the queue)."
    return f"cancel accepted {job['id']}, but it is still listed in the queue, so this is not verified."


def _post_condition(arguments: dict):
    if (arguments.get("action") or "list").strip().lower() != "cancel":
        return None
    wanted = (arguments.get("job") or "").strip()
    jobs = queued_jobs()
    if not wanted or jobs is None:
        return None
    still = [j["id"] for j in jobs if _matches(j, wanted)]
    return not still, "still queued" if still else "no longer queued"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="print_queue", schema=SCHEMA, run=_run)]
