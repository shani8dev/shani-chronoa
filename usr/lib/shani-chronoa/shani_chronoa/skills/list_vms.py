"""Skill: which virtual machines exist, and are they running?

`virsh` (libvirt) is the one interface GNOME Boxes, virt-manager and the
command line all share. Two connections matter and they are different machines'
worth of VMs: `qemu:///session` (the user's own, what GNOME Boxes uses) and
`qemu:///system` (the system daemon, what virt-manager uses by default). Each is
asked separately and reported separately, because a refusal on one says
nothing about the other.

Honesty rules: no `virsh` is "libvirt is not installed", never "no VMs"; a
connection that cannot be opened (no daemon, not in the libvirt group) is
reported as unreadable rather than as empty.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 20
_URIS = (("qemu:///session", "your own (GNOME Boxes)"),
         ("qemu:///system", "system-wide (virt-manager)"))

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_vms",
        "description": (
            "List the virtual machines defined with libvirt - both your own "
            "(GNOME Boxes) and the system-wide ones (virt-manager) - and whether "
            "each is running, paused or shut off. Use for 'what VMs do I have', "
            "'is my Windows VM running'. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def parse_virsh_list(stdout: str) -> "list[dict]":
    """Rows of `virsh list --all`: ` Id   Name   State`, dashes, then data."""
    rows = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Id") or set(stripped) <= {"-"}:
            continue
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            continue
        rows.append({"id": parts[0], "name": parts[1], "state": parts[2].strip()})
    return rows


def _list(uri: str) -> "tuple[list | None, str]":
    try:
        proc = subprocess.run(["virsh", "-c", uri, "list", "--all"], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, f"did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return None, str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, detail[-1] if detail else f"exit {proc.returncode}"
    return parse_virsh_list(proc.stdout), ""


def _run(_arguments: dict) -> str:
    if shutil.which("virsh") is None:
        return files.tool_missing("virsh", "list virtual machines").replace(
            "so nothing was done", "so which VMs exist is UNKNOWN (not the same as none)")
    lines = []
    for uri, label in _URIS:
        rows, why = _list(uri)
        if rows is None:
            lines.append(f"{label} ({uri}): could not be read - {why}. Treat as UNKNOWN, not empty.")
            continue
        if not rows:
            lines.append(f"{label} ({uri}): no virtual machines defined.")
            continue
        running = sum(1 for r in rows if r["state"] == "running")
        lines.append(f"{label} ({uri}): {len(rows)} VM(s), {running} running:")
        for r in rows:
            lines.append(f"  {r['name']:<28} {r['state']}")
    return "\n".join(lines)


SKILLS = [Skill(name="list_vms", schema=SCHEMA, run=_run)]
