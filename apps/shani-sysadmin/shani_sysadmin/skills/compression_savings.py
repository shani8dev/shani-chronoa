"""Skill: how much is btrfs compression actually saving me?

Nothing in Chronoa answered this, and on an immutable btrfs system with
compression on it is the most useful number about storage there is: it is why a
~20 GB install fits in the slot it boots from, and why deleting a big file
sometimes frees less than its size.

`compsize` walks the extents and reports the ratio. **Measured on a real slot**
(`shani-testbed` `chronoa-cli-formats.sh`, `@blue`, 2026-10-10) - and the
measurement is what makes this parseable rather than guessed:

```
Processed 14 files, 15 regular extents (15 refs), 8 inline.
Type       Perc     Disk Usage   Uncompressed Referenced
TOTAL        9%      145K         1.4M         1.4M
zstd         9%      145K         1.4M         1.4M
```

So the answer is one line of numbers out of that table, not a re-derivation:
**TOTAL's Perc is the fraction of the uncompressed size the data actually
occupies** (9% means the disk holds about a ninth), and the algorithm row below
it names the compressor - `zstd`, on Shanios.

**Two measurements decided where this runs, and both are recorded because both
were wrong first.**

- **`compsize /` answers "Not btrfs" in an nspawn slot**, because nspawn's root
  is an overlay rather than the image's btrfs. A one-path skill would have
  reported "this is not btrfs" about a machine whose root is btrfs - so the
  subvolumes ShaniOS's own fstab documents (`/data`, `/home`, `/var/cache`) are
  what get measured, and the first one that answers is the answer.
- **`compsize` on an empty directory answers `No files.` and exits 1**, which is
  an answer, not a failure: an empty `@home` in a fresh slot is exactly that.
  Exiting 1 is not read as "could not measure" without that stderr being
  checked first, because a directory that is merely empty is not a tool that
  is broken.

This reports the same subject the `filesystems` sense does (mounts and real
room), so it follows that sense's switch rather than opening a new one - see
`sense_reading.py`, and `snapshot_status` for the precedent.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 120

#: The btrfs mounts Shanios's own fstab documents, in the order they are asked.
#: `/` is deliberately last: in a container it is an overlay, not the btrfs the
#: machine boots from, and measuring it first would produce "not btrfs" about a
#: machine whose root is btrfs.
_MOUNTS = ("/var/cache", "/data", "/home", "/")


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's subject is the filesystems sense's, so its switch is too."""
    if config.sense_allowed("filesystems"):
        return True, ""
    return False, sense_reading.refusal(config, "filesystems")


def _run_compsize(path: str) -> "tuple[str, str]":
    """(stdout, stderr) of `compsize path`, or ("", reason) when it cannot run."""
    try:
        proc = subprocess.run(["compsize", path], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"compsize did not finish within {_TIMEOUT}s"
    except OSError as exc:
        return "", str(exc)
    return proc.stdout or "", proc.stderr or ""


def _parse(output: str) -> "tuple[Optional[str], Optional[str]]":
    """`(total-line, algorithm)` from compsize's table.

    The TOTAL row is the one that answers the question, and the row type below
    it is the compressor. Read by the leading word, because every other field
    is a sized number whose meaning shifts with the locale - the label is the
    only stable anchor.
    """
    total, algorithm = None, None
    for line in output.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "TOTAL" and len(words) >= 5:
            total = f"TOTAL occupies {words[1]} of {words[3]} uncompressed"
        elif words[0] not in ("Type", "Processed") and len(words) >= 5 and total:
            algorithm = words[0]
    return total, algorithm


def _measure() -> List[str]:
    """The mounts that answered, with the numbers, or the reason none did."""
    if shutil.which("compsize") is None:
        return ["Compression savings are UNKNOWN: compsize (btrfs-progs) is not "
                "installed, so the ratio cannot be read."]
    reasons: List[str] = []
    for mount in _MOUNTS:
        if not __import__("pathlib").Path(mount).is_dir():
            continue
        out, err = _run_compsize(mount)
        # **`No files.` and exit 1 is an answer, not a failure** - an empty
        # subvolume is a legitimate state, and reporting it as "could not
        # measure" would be a confident wrong answer about the machine.
        if "No files" in (err or ""):
            reasons.append(f"{mount}: nothing in it yet")
            continue
        if "Not btrfs" in (err or ""):
            reasons.append(f"{mount}: not btrfs here")
            continue
        total, algorithm = _parse(out)
        if total:
            line = f"{mount}: {total}"
            if algorithm:
                line += f" (compressed with {algorithm})"
            return [line]
        detail = (err or out or "").strip().splitlines()
        reasons.append(f"{mount}: {(detail[-1] if detail else 'no numbers')[:80]}")
    return ["Compression savings are UNKNOWN on this machine - no btrfs mount "
            "with files in it could be measured (" + "; ".join(reasons[:4]) + ")."]


def _run(_arguments: dict) -> str:
    config = ChronoaConfig()
    allowed, why = _consent(config)
    if not allowed:
        return why
    return "\n".join(_measure())


SCHEMA = {
    "type": "function",
    "function": {
        "name": "compression_savings",
        "description": (
            "How much space btrfs compression is saving on this machine: the "
            "uncompressed size of the data, how much disk it actually occupies, "
            "and which compressor is doing it. Use for 'how much is compression "
            "saving me', 'why is my disk less full than the files suggest'. "
            "Read-only; nothing is changed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="compression_savings", schema=SCHEMA, run=_run)]
