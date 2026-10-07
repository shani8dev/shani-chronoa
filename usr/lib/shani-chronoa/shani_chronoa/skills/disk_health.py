"""Skill: is my disk healthy, and is it encrypted?

Two questions people ask about the same drive, answered from two sources:

- **Health** - the `storage` sense: drives from sysfs, wear, and SMART/NVMe
  health through `smartctl` where it can be read. Background context until now.
- **Encryption** - `lsblk`'s device tree. A mount is encrypted when one of its
  ancestors is a LUKS container (`FSTYPE=crypto_LUKS`, child `TYPE=crypt`). This
  needs no root, unlike `cryptsetup status`, and it answers the question people
  actually mean: "is what I save to `/` and `/home` encrypted at rest?"

Shanios installs on LUKS by default, so the device tree is
`nvme0n1 -> nvme0n1p2 (crypto_LUKS) -> shani_root (crypt, btrfs) -> /, /home...`.
Walking ancestors, not matching names, is what makes that answer reliable on a
layout this code has never seen.

The health half follows the `storage` sense's switch (on by default) - see
`sense_reading.py`; the encryption half needs none.

Honesty rules: an `lsblk` that cannot be read makes encryption UNKNOWN, never
"not encrypted"; a mount whose chain has no LUKS container *is* reported as not
encrypted, because that is what the tree says.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 15
_KEY_MOUNTS = ("/", "/home", "/data", "/var")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "disk_health",
        "description": (
            "Health of this machine's drives (SMART/NVMe status and wear where it "
            "can be read) and whether each mounted filesystem - /, /home and the "
            "rest - sits on an encrypted (LUKS) volume. Use for 'is my SSD "
            "healthy', 'is my disk encrypted', 'how worn is my drive'. Drive "
            "health uses the 'storage-sense-enabled' switch. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the storage sense's switch (see sense_reading.py)."""
    if config.sense_allowed("storage"):
        return True, ""
    return False, sense_reading.refusal(config, "storage")


def read_tree() -> "tuple[list | None, str]":
    if shutil.which("lsblk") is None:
        return None, "lsblk is not installed"
    for cols in ("NAME,TYPE,FSTYPE,SIZE,MOUNTPOINTS", "NAME,TYPE,FSTYPE,SIZE,MOUNTPOINT"):
        try:
            proc = subprocess.run(["lsblk", "-J", "-o", cols], capture_output=True,
                                  text=True, timeout=_TIMEOUT, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return None, str(exc)
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout).get("blockdevices") or [], ""
            except ValueError as exc:
                return None, f"unreadable output ({exc})"
    return None, (proc.stderr or "").strip() or f"exit {proc.returncode}"


def _mounts(node: dict) -> "list[str]":
    points = node.get("mountpoints")
    if points is None:
        points = [node.get("mountpoint")]
    return [p for p in points if p]


def encryption_by_mount(tree: list) -> "dict[str, str | None]":
    """mountpoint -> name of the LUKS device it sits inside, or None."""
    found = {}

    def walk(node: dict, luks):
        if (node.get("fstype") or "") == "crypto_LUKS":
            luks = node.get("name")
        for point in _mounts(node):
            found[point] = luks
        for child in node.get("children") or []:
            walk(child, luks)

    for top in tree:
        walk(top, None)
    return found


def describe_encryption(tree: list) -> "list[str]":
    by_mount = encryption_by_mount(tree)
    if not by_mount:
        return ["No mounted filesystems were found in the device tree."]
    lines = []
    ordered = [m for m in _KEY_MOUNTS if m in by_mount] + sorted(
        m for m in by_mount if m not in _KEY_MOUNTS and m != "[SWAP]")
    for point in ordered:
        luks = by_mount[point]
        state = f"encrypted (LUKS on {luks})" if luks else "NOT encrypted"
        lines.append(f"  {point:<20} {state}")
    if "[SWAP]" in by_mount:
        luks = by_mount["[SWAP]"]
        lines.append(f"  {'swap':<20} {'encrypted (LUKS on ' + luks + ')' if luks else 'NOT encrypted'}")
    return lines


def _run(_arguments: dict) -> str:
    lines = ["Encryption at rest:"]
    tree, why = read_tree()
    if tree is None:
        lines.append(f"  UNKNOWN - the device tree could not be read ({why}). "
                     "That is not the same as the disk being unencrypted.")
    else:
        lines.extend(describe_encryption(tree))
    lines.append("")
    lines.append("Drive health:")
    config = ChronoaConfig()
    allowed, why = _consent(config)
    lines.append(sense_reading.reading("storage") if allowed else why)
    return "\n".join(lines)


SKILLS = [Skill(name="disk_health", schema=SCHEMA, run=_run)]
