"""Sense: what is mounted, and how much room each filesystem really has.

`storage` walks `/sys/block` and reports disks. `disk_usage` (a skill) reports
how full a filesystem is. Neither answers the question a person actually has
when something refuses to save a file: **is there room for another file, or just
room for more bytes?**

Those are different questions. A filesystem can have 40% of its bytes free and
be unable to create one more file, because every inode is used - which is what
happens on a machine with millions of small files, and it looks exactly like
"plenty of space" to every byte-oriented tool. This reads both and says which
one is the constraint.

Also reports the mount options, because `ro` in there means writes will fail no
matter how much room there is.

Honesty rules, which are the reason this is not `df`:

- **A filesystem that cannot be stat'd is reported as unreadable, not as zero
  free.** A remote or vanished mount returning "0 B free" would be the single
  most misleading line this project could emit.
- Pseudo-filesystems are shown but marked, rather than being filtered out and
  silently omitted. Someone asking "what is mounted" wants the tmpfs list.
- Inode numbers are reported as `None` for filesystems that do not have them,
  never as `0` and never as `100%`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa import files
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_MOUNTINFO = Path("/proc/self/mountinfo")
#: Types that are noise in a listing but must not be silently dropped, because
#: "what is mounted" means all of it.
_PSEUDO = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
    "securityfs", "pstore", "bpf", "debugfs", "tracefs", "configfs", "fusectl",
    "hugetlbfs", "mqueue", "autofs", "binfmt_misc", "efivarfs", "ramfs",
    "squashfs", "nsfs", "overlay",
}
#: Under this fraction, a filesystem is worth naming in the headline rather than
#: only in the table.
_LOW = 15

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "filesystems",
        "description": (
            "List mounted filesystems with type, size, bytes free, and inodes "
            "free, flagging any that are nearly full or mounted read-only. "
            "Reports inodes because a filesystem can have plenty of bytes and "
            "still be unable to create another file."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _decode(field: str) -> str:
    """mountinfo octal-escapes space, tab, newline and backslash as \\040 etc."""
    out = []
    i = 0
    while i < len(field):
        ch = field[i]
        if ch == "\\" and i + 3 < len(field):
            try:
                out.append(chr(int(field[i + 1:i + 4], 8)))
                i += 4
                continue
            except ValueError:
                pass
        out.append(ch)
        i += 1
    return "".join(out)


def read_mounts() -> List[dict]:
    """Parse `/proc/self/mountinfo` into mount points with their options.

    The `stat -f` route is not used for the mount list itself: it is slow on a
    network mount and cannot say which source a mount came from.
    """
    try:
        raw = _MOUNTINFO.read_text()
    except OSError as exc:
        logger.debug("mountinfo unreadable: %s", exc)
        return []
    mounts: List[dict] = []
    for line in raw.splitlines():
        parts = line.split(" - ")
        if len(parts) != 2:
            continue
        left, right = parts[0].split(), parts[1].split()
        if len(left) < 6 or not right:
            continue
        try:
            target = _decode(left[4])
        except (IndexError, ValueError):
            continue
        mounts.append({
            "target": target,
            # mountinfo is `... - fstype source superoptions`, so right[0] is
            # the type and right[1] the source. Having these the other way round
            # made every snap squashfs look like a `/dev/loop` filesystem, so the
            # pseudo-filesystem filter missed all 30 of them and the report led
            # with thirty immutable package images as "needs attention".
            "fstype": right[0],
            "source": _decode(right[1]) if len(right) > 1 else "unknown",
            "options": left[5].split(","),
        })
    return mounts


def stat_fs(path: str) -> Optional[dict]:
    """Space and inodes for one mount, or None if it cannot be read."""
    try:
        st = os.statvfs(path)
    except OSError as exc:
        logger.debug("statvfs(%s) failed: %s", path, exc)
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - (st.f_bfree * st.f_frsize)
    inodes = None if st.f_files == 0 else {
        "total": st.f_files,
        "free": st.f_favail,
    }
    return {
        "total": total,
        "free": free,
        "used": used,
        "inodes": inodes,
    }


def _pct(used: int, total: int) -> int:
    return 0 if total <= 0 else round(100.0 * used / total)


def _run(arguments: dict) -> Union[str, Percept]:
    mounts = read_mounts()
    if not mounts:
        return (
            "Could not read the mount table, so no filesystem is reported. "
            "That is not the same as there being none: /proc/self/mountinfo was "
            "unreadable on this machine."
        )

    rows: List[dict] = []
    unreadable: List[str] = []
    for m in mounts:
        stats = stat_fs(m["target"])
        if stats is None:
            unreadable.append(m["target"])
            rows.append({**m, "stats": None})
            continue
        stats["byte_pct"] = _pct(stats["used"], stats["total"])
        if stats["inodes"]:
            stats["inode_pct"] = _pct(
                stats["inodes"]["total"] - stats["inodes"]["free"],
                stats["inodes"]["total"],
            )
        else:
            stats["inode_pct"] = None
        stats["read_only"] = "ro" in m["options"]
        rows.append({**m, "stats": stats})

    real = [r for r in rows if r["stats"] and r["fstype"] not in _PSEUDO]
    if not real:
        return (
            "The mount table was read but held no real filesystems - only "
            f"{len(rows)} pseudo-filesystem(s). That is unusual and suggests "
            f"this is a container or a very minimal system."
        )

    lines: List[str] = []
    tight_bytes = [r for r in real if r["stats"]["byte_pct"] >= _LOW]
    tight_inodes = [r for r in real
                    if r["stats"]["inode_pct"] is not None
                    and r["stats"]["inode_pct"] >= _LOW]
    ro = [r for r in real if r["stats"]["read_only"]]

    if tight_bytes or tight_inodes or ro:
        lines.append("Needs attention:")
        for r in tight_inodes:
            lines.append(
                f"  {r['target']}: {r['stats']['inode_pct']}% of inodes used "
                f"({r['stats']['inodes']['free']:,} free of "
                f"{r['stats']['inodes']['total']:,})"
                + (" - no new files can be created here even with bytes free"
                   if r["stats"]["inode_pct"] >= 100 else ""))
        for r in tight_bytes:
            lines.append(f"  {r['target']}: {r['stats']['byte_pct']}% of space used")
        for r in ro:
            lines.append(f"  {r['target']}: mounted read-only, so writes fail")
    else:
        lines.append("No filesystem is low on space or inodes.")

    lines.append("")
    lines.append(f"{'MOUNT':<34} {'TYPE':<10} {'USED':>9} {'FREE':>9} "
                 f"{'INODES FREE':>12}")
    for r in sorted(real, key=lambda x: -x["stats"]["byte_pct"]):
        s = r["stats"]
        inode_txt = ("n/a" if s["inode_pct"] is None
                     else f"{s['inodes']['free']:,} ({100 - s['inode_pct']}%)")
        mark = " ro" if s["read_only"] else ""
        lines.append(
            f"{r['target'][:33]:<34} {r['fstype'][:9]:<10} "
            f"{files.human_size(s['used']):>9} {files.human_size(s['free']):>9} "
            f"{inode_txt:>12}{mark}")
    pseudo = [r for r in rows if r["fstype"] in _PSEUDO]
    if pseudo:
        lines.append(f"\n  plus {len(pseudo)} pseudo-filesystem(s) "
                     f"({', '.join(sorted({p['fstype'] for p in pseudo})[:6])}"
                     f"{'...' if len({p['fstype'] for p in pseudo}) > 6 else ''}), "
                     f"not detailed.")
    if unreadable:
        lines.append(
            f"  {len(unreadable)} mount(s) could not be read and are NOT counted "
            f"as empty: {', '.join(unreadable[:4])}"
            f"{'...' if len(unreadable) > 4 else ''}")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc-mountinfo",
        metadata={
            "mounts": len(rows),
            "real_filesystems": len(real),
            "unreadable": len(unreadable),
            "tight_on_bytes": [r["target"] for r in tight_bytes],
            "tight_on_inodes": [r["target"] for r in tight_inodes],
            "read_only": [r["target"] for r in ro],
        },
    )


_SENSE = Sense(
    name="filesystems",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
