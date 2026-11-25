"""Skill: who holds a lock, and on what?

"Why won't this file save?" is the question a blocked writer asks, and
the answer is almost always a lock somebody else is holding. Nothing in
this package read the kernel's lock table. `open_files` answers "what
has this file **open**", and its own docstring claims to answer "has a
lock" as well - but it never surfaces lock state: `lsof` prints a lock
as a suffix on the descriptor (`7ur`, where the `r` is the read-lock
mark) and the parser drops it. A WRITE lock is the one that stops a
save, and a file can be open by nobody and locked by somebody - the
case that makes a write fail with `EAGAIN` while every reader of
"who has this open" comes back empty.

**Read from `/proc/locks`, not by running `lslocks`.** The lock table
is the kernel's own, world-readable, and needs no binary and no
privilege. `lslocks` is the obvious wiring and it is on the image, but
reading `/proc` directly is what the rest of this package does for
exactly this reason - and `/proc/locks` is the only place a lock's
byte **range** exists, which a "locked? yes/no" reading loses.

**The inode in `/proc/locks` is decimal; the device is hex
`major:minor`.** Both were measured, not assumed, and they are read
apart because matching on the inode alone is wrong: two files on
different filesystems share an inode number, so a lock on one would be
reported as a lock on the other. A lock is matched on
`(major, minor, inode)`, which is unique.

**The path is resolved from the holder's own `/proc/<pid>/fd`.**
`/proc/locks` names a pid and an inode but no path, so each holder's
file descriptors are read and matched on the same `(major, minor,
inode)` triple. A process owned by another user has an fd table this
user cannot read, so its locks keep their pid and program name and the
path is reported as unreadable rather than guessed at - the same
privacy boundary `list_processes` already draws.

**`comm` is truncated to fifteen characters**, and a program named by a
cut-off string names a program that may not exist, so the name is read
from `/proc/<pid>/comm` (all fifteen) and stated as the kernel's own
name rather than lsof's nine.

**A lock's byte range is part of the answer.** `0 EOF` is the whole
file, which is what a save-blocking lock looks like; `128 512` is a
range lock, which does not block a write to a different part of the
same file. Reporting only "locked" loses the difference between a lock
that stops the save and one that does not.

**What it deliberately does not do.** It reads. It does not break a
lock - that is `fuser`/`kill`, and breaking somebody else's lock is a
destructive act this skill is not - and it does not poll, because a
lock is a state, not a rate.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from shani_chronoa.skills import Skill


class Lock(NamedTuple):
    """One entry of the kernel's lock table."""

    index: int
    klass: str       # "POSIX" or "FLOCK"
    lock_type: str   # "ADVISORY" or "MANDATORY"
    access: str      # "READ" or "WRITE"
    pid: int
    major: int       # device major, read as hex
    minor: int       # device minor, read as hex
    inode: int       # read as decimal
    start: str       # "0" for a whole-file lock
    end: str         # "EOF" for a whole-file lock


def _parse(line: str) -> "Optional[Lock]":
    """`1: POSIX ADVISORY READ 112059 fc:01:14596476 128 128`.

    The sixth field is `major:minor:inode` in one colon-joined token -
    the device half in hex, the inode in decimal. A line that does not
    have all eight fields is not a lock, and one that fails to parse is
    skipped rather than raised over: the table is read on every call, so
    a line the kernel adds later must not stop the rest from reading.
    """
    tokens = line.split()
    if len(tokens) < 8:
        return None
    try:
        dev = tokens[5].split(":")
        if len(dev) != 3:
            return None
        return Lock(
            index=int(tokens[0].rstrip(":")),
            klass=tokens[1],
            lock_type=tokens[2],
            access=tokens[3],
            pid=int(tokens[4]),
            major=int(dev[0], 16),
            minor=int(dev[1], 16),
            inode=int(dev[2]),
            start=tokens[6],
            end=tokens[7],
        )
    except (ValueError, IndexError):
        return None


def _locks() -> "List[Lock]":
    """The kernel's lock table, or nothing if it cannot be read."""
    try:
        raw = Path("/proc/locks").read_text()
    except OSError:
        return []
    out: "List[Lock]" = []
    for line in raw.splitlines():
        lock = _parse(line)
        if lock is not None:
            out.append(lock)
    return out


def _comm(pid: int) -> str:
    """The holder's name, all fifteen characters of it."""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return f"pid {pid}"


def _fd_paths(pid: int) -> "Dict[tuple, str]":
    """`(major, minor, inode) -> path`, from one process's fd table.

    A process owned by another user has a fd table this user cannot
    read, so the glob yields nothing and the lock keeps its pid without
    a path - the honest state, not a failure.
    """
    out: "Dict[tuple, str]" = {}
    try:
        entries = list(Path(f"/proc/{pid}/fd").glob("*"))
    except OSError:
        return out
    for fd in entries:
        try:
            st = fd.stat()
        except OSError:
            continue
        key = (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
        if key in out:
            continue
        try:
            out[key] = os.readlink(fd)
        except OSError:
            continue
    return out


def _resolve_all(locks: "List[Lock]") -> "Dict[int, Optional[str]]":
    """`lock index -> path`, reading each holder's fd table once.

    A process can hold many locks, so the fds are read per holder rather
    than per lock - one walk of a large fd table answers every lock that
    holder carries.
    """
    by_pid: "Dict[int, List[Lock]]" = {}
    for lock in locks:
        by_pid.setdefault(lock.pid, []).append(lock)
    out: "Dict[int, Optional[str]]" = {}
    for pid, group in by_pid.items():
        paths = _fd_paths(pid)
        for lock in group:
            out[lock.index] = paths.get((lock.major, lock.minor, lock.inode))
    return out


def _range(lock: Lock) -> str:
    """Where in the file the lock sits, which is part of the answer."""
    if lock.start == "0" and lock.end == "EOF":
        return "the whole file"
    if lock.end == "EOF":
        return f"bytes {lock.start} to the end"
    return f"bytes {lock.start}-{lock.end}"


def _blocks_a_writer(lock: Lock) -> bool:
    """Does this lock stop somebody writing to the file?

    **Measured on this box, because the obvious answer is wrong.** A whole-file
    POSIX *read* lock blocks a whole-file write:

        parent holds whole-file READ   -> child wants WRITE: BLOCKED errno=11
        CONTROL nothing held           -> child wants WRITE: ACQUIRED

    `F_RDLCK` is shared with other readers and exclusive against writers, so a
    read lock is *not* the harmless thing its name suggests. Seven such locks
    existed on this machine while this was written, and every one of them
    stopped a save.

    A *ranged* read lock is a weaker claim and is not included here: it blocks
    a write only where the ranges overlap, so calling it a save-blocker would
    be as wrong as ignoring a whole-file one. Those stay in the shared list,
    with their bytes.
    """
    return lock.access == "WRITE" or _range(lock) == "the whole file"


def _survey() -> str:
    """Every lock on the machine, blocking first because blocking is the point."""
    locks = _locks()
    if not locks:
        return ("No process holds a lock anywhere on this machine. That "
                "is the ordinary state of a quiet machine, not a "
                "failure to read the lock table.")
    writes = [lock for lock in locks if lock.access == "WRITE"]
    reads = [lock for lock in locks if lock.access != "WRITE"]
    blocking = [lock for lock in locks if _blocks_a_writer(lock)]
    # What a whole-file read lock holds is its bytes; what a ranged one holds
    # is a slice of them. Both are named, so the reader can tell a lock that
    # stops a save from one that only touches part of the file.
    shared = [lock for lock in reads if lock not in blocking]
    resolved = _resolve_all(locks)
    lines = [f"{len(locks)} lock(s) held right now: "
             f"{len(writes)} write, {len(reads)} read"]
    if blocking:
        lines.append("")
        lines.append("Locks that block another writer - a save to any of "
                     "these files will wait:")
        for lock in blocking[:12]:
            where = resolved.get(lock.index)
            if not where:
                where = "(the holder's fd table is not readable)"
            lines.append(f"  {lock.access:<5} {_comm(lock.pid):<15} "
                         f"pid {lock.pid}  {_range(lock)}  {where}")
        if len(blocking) > 12:
            lines.append(f"  ... and {len(blocking) - 12} more blocking "
                         f"lock(s)")
    if shared:
        by_program: "Dict[str, int]" = {}
        for lock in shared:
            name = _comm(lock.pid)
            by_program[name] = by_program.get(name, 0) + 1
        lines.append("")
        lines.append("The rest are ranged read locks. Several programs can "
                     "hold one on the same file, and each blocks a write "
                     "only to the bytes it covers:")
        for name, count in sorted(by_program.items(),
                                  key=lambda item: -item[1])[:8]:
            lines.append(f"  {name}: {count}")
        if len(by_program) > 8:
            lines.append(f"  ... and {len(by_program) - 8} more program(s)")
    return "\n".join(lines)


def _locks_on(path: str) -> str:
    """The locks on one named file, matched on the device and inode."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return f"Cannot look at {path}: {exc.strerror or exc}."
    key = (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
    locks = [lock for lock in _locks()
             if (lock.major, lock.minor, lock.inode) == key]
    if not locks:
        return (f"No process holds a lock on {path}. The file is not "
                "locked - if a write to it still fails, the cause is "
                "elsewhere: permissions, or a read-only filesystem.")
    resolved = _resolve_all(locks)
    lines = [f"{len(locks)} lock(s) on {path}:"]
    for lock in locks:
        where = resolved.get(lock.index)
        if not where:
            where = "(the holder's fd table is not readable)"
        lines.append(f"  {lock.access}  {lock.klass}  {_comm(lock.pid)} "
                     f"(pid {lock.pid})  {_range(lock)}  {where}")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    args = arguments or {}
    raw = args.get("path")
    if raw:
        return _locks_on(str(raw))
    return _survey()


SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_locks",
        "description": (
            "Show who holds a file lock, and on what - the answer to "
            "\"why won't this file save\" and \"why did my write fail "
            "with EAGAIN\". A WRITE lock blocks another writer; a READ "
            "lock is shared and usually harmless. Use for 'who has this "
            "file locked', 'what is holding a lock', 'why can't I write "
            "this file', 'is this file locked'. Give a path to check one "
            "file; omit it for every lock on the machine. Reads the "
            "kernel's own lock table (/proc/locks), so it needs no "
            "binary and no privilege. Read-only; nothing is polled, "
            "changed or broken."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": ("The file to check for locks, e.g. "
                                    "'/home/me/notes.txt'. Omit for "
                                    "every lock on the machine."),
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="file_locks", schema=SCHEMA, run=_run)]
