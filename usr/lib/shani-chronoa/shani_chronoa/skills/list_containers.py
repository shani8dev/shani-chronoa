"""Skill: which containers and distroboxes exist, and did any of them die?

On an immutable system the mutable workspace is a container: Shanios ships
`distrobox` and `podman` for exactly that. The `containers` sense already reads
podman/docker (state, exit codes, OOM kills, image disk use) as background
context. This answers the direct question, and adds the one thing the sense
does not say: which of those containers are *distroboxes* - the ones a person
actually works in, by the name they gave it.

The podman half follows the `containers` sense's switch (off by default), as
every sense-backed skill here does - see `sense_reading.py`. The distrobox half
needs none: it lists names the person chose, from a tool they installed.

Honesty rules: no `distrobox` is "not installed", never "no distroboxes"; a
`distrobox list` that fails is reported as failing, and the podman half is
still shown.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files, sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 25

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_containers",
        "description": (
            "List this machine's containers: distroboxes (by name, with their "
            "image and whether they are running) and podman/docker containers, "
            "including any that exited because they ran out of memory, plus how "
            "much disk the images use. Use for 'what distroboxes do I have', "
            "'is my container running', 'why did my container die'. The podman "
            "details use the 'containers-sense-enabled' switch. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the containers sense's switch (see sense_reading.py)."""
    if config.sense_allowed("containers"):
        return True, ""
    return False, sense_reading.refusal(config, "containers")


def parse_distrobox(stdout: str) -> "list[dict]":
    """Rows of `distrobox list`'s `|`-separated table, header dropped."""
    rows = []
    for line in stdout.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 4 or cells[0].upper() == "ID":
            continue
        rows.append({"id": cells[0], "name": cells[1], "status": cells[2], "image": cells[3]})
    return rows


def distrobox_lines() -> "list[str]":
    if shutil.which("distrobox") is None:
        return [files.tool_missing("distrobox", "list distroboxes").replace(
            "so nothing was done", "so which distroboxes exist is UNKNOWN")]
    try:
        proc = subprocess.run(["distrobox", "list", "--no-color"], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return [f"distrobox list did not answer within {_TIMEOUT}s, so the distroboxes are UNKNOWN."]
    except OSError as exc:
        return [f"distrobox list could not run ({exc}), so the distroboxes are UNKNOWN."]
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return ["distrobox list failed" + (f": {detail[-1]}" if detail else "")
                + ", so the distroboxes are UNKNOWN."]
    rows = parse_distrobox(proc.stdout)
    if not rows:
        return ["No distroboxes exist yet (distrobox list returned an empty table)."]
    out = [f"{len(rows)} distrobox(es):"]
    for r in rows:
        out.append(f"  {r['name']:<24} {r['status']:<22} {r['image']}")
    out.append("  Enter one with: distrobox enter <name>")
    return out


def _run(_arguments: dict) -> str:
    lines = ["Distroboxes:", *distrobox_lines(), "", "Containers:"]
    config = ChronoaConfig()
    allowed, why = _consent(config)
    lines.append(sense_reading.reading("containers") if allowed else why)
    return "\n".join(lines)


SKILLS = [Skill(name="list_containers", schema=SCHEMA, run=_run)]
