"""Skill: what has this file open?

Nothing in Chronoa answers this. `port_owner` reads `/proc/net/tcp`, which
gives a socket, an inode and a state - but no command line, and no answer at
all for a *file*. `list_processes` shows names and CPU. Between them: if a
program is holding a file open, or has a lock, or is sitting on a deleted
file whose space cannot be reclaimed, there is no way to find out which.

`lsof` answers all three, and it is the only thing on either image that does.

**Measured on the dev box (lsof 4.95), not guessed:**

    COMMAND    PID             USER  FD   TYPE             DEVICE SIZE/OFF    NODE NAME
    bash    587023 shrinivaskumbhar cwd    DIR               0,38     4320     426 /tmp/opencode
    bash    587023 shrinivaskumbhar txt    REG              252,1  1540520 8923339 /usr/bin/bash

    COMMAND      PID             USER FD   TYPE  DEVICE SIZE/OFF NODE NAME
    llama-ser   4311 shrinivaskumbhar  3u  IPv4   34848      0t0  TCP 127.0.0.1:8765 (LISTEN)
    opencode    6476 shrinivaskumbhar 13u  IPv4 129072      0t0  TCP 127.0.0.1:35842->127.0.0.1:49374 (ESTABLISHED)

**Three things in that output that a reader gets wrong by assumption:**

- **`COMMAND` is truncated to nine characters.** `llama-ser` is
  `llama-server`, and `opencode` is `opencode` only by luck. Reporting that
  string as the program name names a program that does not exist. The real
  name is read from `/proc/<pid>/comm`, which holds all fifteen.
- **`FD` is not always a number.** `cwd`, `rtd`, `txt`, `mem` and `rtd` are
  *descriptors with names* - the working directory, the root, the executable,
  mapped memory. `cwd` in particular is how you find the directory a daemon
  will delete relative to, which is a question `/proc/<pid>/cwd` answers and
  `list_processes` does not.
- **A connected socket prints `local->peer (STATE)` and a listening one
  prints `local (LISTEN)`.** The arrow is the only difference between
  "accepting connections" and "talking to something", and a parser that
  splits on whitespace and takes the last token reports `(LISTEN)` for an
  established session.

**And the empty case, measured:**

    lsof -nP /tmp/empty.txt        -> rc=1, **empty stdout**, rc=0 with content

with `WARNING: can't stat() nsfs file system ...` lines on **stderr**. So a
reader that concatenates the two streams reports six warnings as though they
were open files, and one that treats rc!=0 as an error cannot say "nothing
has it open" at all. Both are wrong, in the direction of looking busy.

`-nP` is not optional decoration: without `-n` lsof resolves every address
through DNS and every port through `/etc/services`, which is slow enough to
time out on a busy machine and turns the output into something a parser
cannot read.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 30

#: `COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME`, with a header whose
#: column widths change with the content - `COMMAND` is 7 here and 9 there -
#: so the row is split on **runs of two or more spaces**, not on columns.
#:
#: **And the number of separators varies by row, which a regex cannot say.**
#: Measured, the same split gives two shapes:
#:
#:     9 fields   `bash 587023 you cwd DIR 0,38 4320 426 /tmp/opencode`
#:     6 fields   `systemd 1 root cwd unknown /proc/1/cwd (readlink: ...)`
#:
#: In the second there is **no DEVICE, SIZE/OFF or NODE at all** - lsof leaves
#: them blank for a descriptor it could not read. A regex written against the
#: 9-field shape parses the 6-field one by taking its last token, which is how
#: the first version reported `denied)` as a path. So the name is the *last*
#: field and the field count is what distinguishes them.
#:
#:     `_GAP.split('systemd 1 root cwd unknown /proc/1/cwd (readlink: Permission denied)')`
#:         -> ['systemd', '1', 'root', 'cwd', 'unknown', '/proc/1/cwd (readlink: Permission denied)']
#:
#: because the gap before `/proc` is wide and the gap inside the path is one
#: space. Splitting is therefore both necessary and sufficient.
#: `DEVICE`, `SIZE/OFF` and `NODE` are numeric. Skipping leading numeric
#: tokens is what separates them from the name - sockets carry only two
#: of the three and files all three, so counting them is wrong.
_NUMERIC = re.compile(r"^\d+(,\d+)?$|^\d+t\d+$|^\d+f$")

#: A number, with an optional unit. `SIZE/OFF` is `0t0` for a socket.
_SIZE = re.compile(r"^[0-9a-fA-F]+t?\d*$")
_NODE = re.compile(r"^\d+$")

#: `FD` values that name a descriptor rather than numbering it.
_NAMED_FD = frozenset({"cwd", "rtd", "txt", "mem", "DEL", "unk", "tr", "pd",
                      "NOFD"})

#: A `(deleted)` suffix means the file is gone but still held open, so the
#: space is still in use. That is the single most useful line lsof prints and
#: the one most often dropped.
_DELETED = "(deleted)"

#: **`/proc/1/cwd (readlink: Permission denied)` is not a working directory.**
#: Measured by running this skill for real: `lsof -p 1` as an unprivileged user
#: returns four rows, every one with the real name replaced by the unreadable
#: path and the kernel's own refusal in parentheses. A parser that takes the
#: last whitespace-separated field reports that string **as the program's
#: working directory** - a confident, wrong, and entirely plausible-looking
#: answer, and the one that sends someone exploring /proc instead of looking
#: at their permissions. A path lsof could not read is not a path.
_REFUSAL = re.compile(r"\s*\((?:readlink|opendir|open|stat):\s*[^)]*\)\s*$")


def _run(arguments: "list[str]") -> "tuple[str, str, int]":
    try:
        proc = subprocess.run(arguments, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"lsof did not answer within {_TIMEOUT}s", 124
    except OSError as exc:
        return "", str(exc), 127
    return proc.stdout or "", proc.stderr or "", proc.returncode


def _parse(text: str) -> "list[dict]":
    """Rows from lsof's stdout. Warnings on stderr never reach this.

    **The first five columns are whitespace-separated and the middle ones are
    numeric.** Two attempts at the separator, both wrong:

      - split on two-or-more spaces: with a short pid the gap between `PID` and
        `USER` collapses to *one* space, so `1 root` merges and the row is
        silently dropped;
      - take `DEVICE SIZE/OFF NODE` as an all-or-nothing block: sockets have
        **only two of the three** (`34848  0t0` and no NODE), so they parsed
        with `TCP` in the name.

    So the name is: everything after the first five whitespace tokens, with
    the leading **numeric** tokens skipped. Measured against all four shapes:

        socket    ... 3u  IPv4 34848 0t0  TCP 127.0.0.1:8765 (LISTEN)
                     -> name "TCP 127.0.0.1:8765 (LISTEN)"
        file      ... cwd DIR  0,38  4320 426  /tmp/opencode
                     -> name "/tmp/opencode"
        unreadable... cwd unknown    /proc/1/cwd (readlink: Permission denied)
                     -> name "/proc/1/cwd (readlink: Permission denied)"
        spaced    ... cwd DIR  0,38   4320 426  /home/you/My Documents
                     -> name "/home/you/My Documents"
    """
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) < 6 or not tokens[1].isdigit():
            # The header (whose second token is `PID`, not a number), or
            # something this does not understand.
            continue
        command, pid_s, user, fd, kind = tokens[:5]
        rest = tokens[5:]
        while rest and _NUMERIC.match(rest[0]):
            rest = rest[1:]
        if not rest:
            continue
        name = " ".join(rest)
        # lsof prefixes a socket's NAME with its protocol (`TCP 127.0.0.1:8765
        # (LISTEN)`). **The protocol is kept, not folded into the name** - "is
        # this TCP or UDP" is a real question about a socket - but it is moved
        # out of `name` so the address stays clean and the arrow/state parse
        # does not have to know about it.
        protocol = ""
        if kind in ("IPv4", "IPv6") and " " in name:
            head, _, tail = name.partition(" ")
            if head.isupper() or head in ("TCP", "UDP", "RAW", "UNIX"):
                protocol, name = head, tail
        # **A refused read is its own state, not a path.** See `_REFUSAL`.
        refused = bool(_REFUSAL.search(name))
        if refused:
            # lsof names the /proc entry it tried, so the honest name is that
            # path and not the refusal that follows it.
            name = name.split(" ", 1)[0].strip()
        unreadable = refused or kind == "unknown" or fd == "NOFD"
        rows.append({
            "command": command,      # TRUNCATED - see _real_command
            "pid": int(pid_s),
            "user": user,
            "fd": fd,
            "named_fd": fd in _NAMED_FD,
            "type": kind,
            "protocol": protocol,
            "name": name,
            "deleted": _DELETED in name,
            "refused": unreadable,
        })
    return rows


def _real_command(pid: int, reported: str) -> str:
    """The untruncated name.

    **`/proc/<pid>/comm` holds 15 characters; lsof prints 9.** Reading the
    short form back is why `llama-ser` is reported as the name of a program
    that does not exist. Falls back to lsof's own string, because a process
    that exited between the two reads has no `/proc` entry and *its* name is
    the only one left.
    """
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return reported
    return comm or reported


def _current_user() -> str:
    """Who "you" are for the owner column. `getpass` reads the environment
    and raises when there is none, which is not a reason to fail a read."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return os.environ.get("USER", "")


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(part for part in raw.decode("utf-8", "replace").split("\0")
                    if part)


def _describe(row: dict) -> str:
    if row["refused"]:
        # The name is what lsof could not read, so it must not be described as
        # a path. This is a *permission* answer, which is the whole point.
        # **The descriptor is named**, because four refused rows read as one
        # sentence otherwise and there is no way to tell them apart.
        which = {"cwd": "its working directory", "rtd": "its root",
                 "txt": "the program itself", "mem": "mapped memory"}.get(
                     row["fd"], f"its descriptor {row['fd']}")
        return (f"is holding {which}, but this user cannot read where that "
                f"points - lsof got the `/proc/{row['pid']}/{row['fd']}` "
                f"link back instead of the real name")
    if row["named_fd"]:
        meaning = {"cwd": "its working directory", "rtd": "its root",
                   "txt": "the program itself", "mem": "mapped memory"}.get(
                       row["fd"], f"its {row['fd']}")
        return f"holds {row['name']} open as {meaning}"
    if row["type"] in ("IPv4", "IPv6"):
        # `local->peer (STATE)` vs `local (STATE)`: the arrow is the whole
        # difference between listening and connected, and the state word is
        # the difference between connected and broken.
        label = (row.get("protocol") or "") + " " + row["name"] if row.get("protocol") else row["name"]
        address, _, state = label.rpartition("(")
        state = state.rstrip(")").strip()
        if "->" in address:
            local, _, peer = address.partition("->")
            article = "an" if state[:1].lower() in "aeiou" else "a"
            return (f"has {article} {state.lower()} connection "
                    f"{local.strip()} → {peer.strip()}")
        verb = "is listening on" if state == "LISTEN" else f"is {state.lower()} on"
        return f"{verb} {address.strip()}"
    if row["deleted"]:
        # The single most useful line lsof prints: the file is gone and its
        # space is still in use until this process closes it. That is why a
        # disk can be full with nothing visible on it.
        return (f"holds **{row['name'].replace(_DELETED, '').strip()}** open, "
                f"and that file has been deleted - its space cannot be "
                f"reclaimed until this is closed")
    return f"has {row['name']} open"


def _run_skill(arguments: dict) -> str:
    if shutil.which("lsof") is None:
        return files.tool_missing("lsof", "find out which program has a file open")

    raw_path = str(arguments.get("path") or "").strip()
    if raw_path:
        try:
            target = Path(os.path.expanduser(raw_path))
            if not target.exists():
                return f"{target} does not exist, so nothing can have it open."
            target = target.resolve()
        except OSError as exc:
            return f"{raw_path} could not be resolved: {exc}"
        command = ["lsof", "-nP", "--", str(target)]
        heading = f"What has {target} open:"
    else:
        raw_pid = arguments.get("pid")
        try:
            pid = int(raw_pid) if raw_pid not in (None, "") else None
        except (TypeError, ValueError):
            return f"{raw_pid!r} is not a process id."
        if pid is not None:
            command = ["lsof", "-nP", "-p", str(pid)]
            heading = f"Files and sockets open by process {pid}:"
        else:
            try:
                limit = max(1, min(int(arguments.get("limit") or 40), 400))
            except (TypeError, ValueError):
                return f"{arguments.get('limit')!r} is not a count."
            command = ["lsof", "-nP"]
            heading = (f"Open file and socket handles (first {limit}). A "
                       "non-root read sees only this user's processes:")

    out, err, rc = _run(command)

    if rc == 124:
        return (f"lsof did not answer within {_TIMEOUT}s. Listing every open "
                "handle on a busy machine can take longer than that, and "
                "`lsof -nP` with no filter is the slowest form of it - name a "
                "file or a pid and ask me again.")
    if rc not in (0, 1) and not out.strip():
        detail = [line for line in err.splitlines()
                  if line.strip() and not line.startswith("lsof: WARNING")]
        return (f"lsof could not answer (exit {rc})"
                + (f": {detail[-1]}" if detail else "."))

    rows = _parse(out)
    # **`WARNING: can't stat()` means the list is not the whole truth**, so it
    # belongs on the answer whether or not there are rows. The first version
    # computed it only inside the empty-result branch, which is exactly
    # backwards: the caveat matters most on a list that looks complete.
    warnings = [line for line in err.splitlines() if "WARNING" in line]
    tail = ("\n\nlsof could not read every file system on this machine, so "
            "this list may be incomplete." if warnings else "")

    # rc=1 with empty stdout is "nothing matched", which is a real answer and
    # the most likely one. It is not an error, and it is not the WARNINGs.
    if not rows:
        if raw_path:
            return f"Nothing has {target} open." + tail
        if pid is not None:
            return (f"Process {pid} has nothing open that this user can see."
                    + tail + " As a non-root read, that is expected for "
                              "another user's processes.")
        return "No open file handles were visible to this user." + tail

    try:
        limit = max(1, min(int(arguments.get("limit") or 40), 400))
    except (TypeError, ValueError):
        limit = 40
    shown = rows[:limit]
    for row in shown:
        row["command"] = _real_command(row["pid"], row["command"])

    deleted = [row for row in shown if row["deleted"]]
    lines = [heading]
    if raw_path or pid is not None:
        if len(shown) > 1:
            lines[0] += f" ({len(shown)} handle(s))"
    else:
        lines[0] += f" — showing {len(shown)} of {len(rows)}."

    seen = set()
    for row in shown:
        key = (row["command"], row["name"])
        if key in seen:
            continue
        seen.add(key)
        detail = _describe(row)
        # The owner is parsed but only worth saying when it is **not** you:
        # every row on a single-user list is that user's own name, and it
        # tells them nothing. It is the first thing worth knowing about a
        # handle nobody recognises.
        me = _current_user()
        who = "" if row["user"] == me else f" (as {row['user']})"
        # The full command line is what makes this actionable, and it is only
        # here - lsof's own column is nine characters.
        argv = _cmdline(row["pid"])
        suffix = f"\n    $ {argv}" if argv and len(argv) < 300 else ""
        lines.append(f"- **{row['command']}** (pid {row['pid']}){who} "
                     f"{detail}{suffix}")

    if deleted:
        lines.append("")
        lines.append(f"**{len(deleted)} of these are deleted files whose space "
                     f"is still in use** - nothing reclaims it until the "
                     f"holding process closes it, which is why a disk can be "
                     f"full with nothing on it.")
    return "\n".join(lines) + tail


SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_files",
        "description": (
            "Find which programs have a file, directory or network socket "
            "open. Use for 'what is using this file', 'why can't I delete "
            "this', 'what has my disk open', 'which process is holding that "
            "socket'. Names the holding program with its full command line, "
            "and flags deleted files whose space cannot be reclaimed. Reads "
            "only this user's processes unless run as root. Read-only: it "
            "lists open handles and never closes one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "File or directory to find holders of. "
                                        "Optional."},
                "pid": {"type": "integer",
                        "description": "Process to list open handles for. "
                                       "Optional."},
                "limit": {"type": "integer",
                          "description": "Maximum handles to show, 1-400. "
                                         "Default 40."},
            },
        },
    },
}

SKILLS = [Skill(name="open_files", schema=SCHEMA, run=_run_skill)]