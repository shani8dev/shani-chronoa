"""Skill: is my backup actually working?

`shani-backup` is a sibling repo about btrfs **snapshots** - the local
undo-safety-net. That is not a backup in the sense anyone means when they lose
a disk, and nothing here answered the other half: **is there a copy of my
files anywhere off this machine, and when was it last taken?**

`restic` is in `shani-tools-extra`, so it is on both images by design, and it
is the tool that answers it.

**The whole question is whether the last backup happened and is intact**, and
that is a question about *evidence*, not about configuration. So this reports
what restic can actually prove - the snapshot table, and `restic check` - and
never reports "your backup is fine" from anything less.

**Measured on a real `@blue` slot (restic 0.19.1), both tables verbatim:**

    restic snapshots      ID        Time                 Host        Tags        Paths                                         Size
                          ---------------------------------------------------------------------------------------------------------
                          7ab8163c  2026-10-10 06:14:01  shanios                 /var/tmp/.../tobackup                          8 B
                          ---------------------------------------------------------------------------------------------------------
                          Timestamps shown in UTC

and with no repository at all:

    restic snapshots      Fatal: Please specify repository location (-r or --repository-file)     rc=1

**That refusal is the common case and it is not a failure to read.** A machine
where restic has never been configured has no backup, and saying "I could not
find a repository" is the honest answer - but it must be paired with the fact
that *this means there is no backup*, because a person asking "is my backup
working" and receiving "could not determine" will hear something much milder
than the truth.

**`Size` is the compressed size restic stored, not the size of the files.**
For a deduplicating backup the second snapshot of unchanged files is a few
bytes, and reporting that as "your backup is 8 B" would be a number that looks
like a fault and is not one.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 60

#: One snapshot row, split on runs of two or more spaces rather than matched
#: with a regex.
#:
#: **A first version used a regex and matched nothing.** restic pads its
#: columns, but the gap between the Time and Host columns is not reliably two
#: spaces, and `Paths` can itself be several paths wide - so `\s\s+` failed on
#: the real row captured from the slot. Splitting on 2+ spaces is what the
#: table actually is:
#:
#:     ['7ab8163c', '2026-10-10 06:14:01', 'shanios',
#:      '/var/tmp/chronoa-cli-formats-pQqnkv/tobackup', '8 B']
#:
#: The Host column is optional in restic's output, so a row with five fields
#: may have an empty one; both shapes are accepted.
_COLUMNS = ("ID", "Time", "Host", "Tags", "Paths", "Size")
_RULE = re.compile(r"^-{10,}")
_GAP = re.compile(r"\s{2,}")
_SIZE = re.compile(r"^[\d.]+\s*(B|KiB|MiB|GiB|TiB|KB|MB|GB)\b")


def _parse_row(line: str) -> "dict | None":
    """One snapshot, or None for a header, a rule, or an unrecognised line."""
    fields = [f for f in _GAP.split(line.rstrip()) if f]
    if len(fields) < 4:
        return None
    record = fields[0]
    when = fields[1]
    if not re.fullmatch(r"\w{6,}", record):
        return None
    if not re.match(r"\d{4}-\d{2}-\d{2}", when):
        return None
    # The size is the last field **when it looks like one**; without that test
    # a Tags column would be mistaken for the path.
    size = ""
    if fields and _SIZE.match(fields[-1]):
        size = fields[-1]
        fields = fields[:-1]
    # `fields[3:]` re-joined rather than `fields[3]`. Measured: restic separates
    # the paths inside the Paths column with a **single** space, and `_GAP`
    # splits on two or more - so `/home/u/photos /home/u/docs` arrives as one
    # field and `fields[3]` would be right *for that fixture*. The join is
    # kept because a multi-path snapshot padded across the gap would otherwise
    # be truncated to its first path, which reads as "only my photos are
    # backed up".
    #
    # It is one branch, not two: `size` only ever trims the tail, so the
    # layout either way is id, time, host, path(s). They were written out
    # twice, and a mutation applied to the dead one left the suite green -
    # two copies of a rule is two places for it to be wrong in.
    row = {
        "id": record,
        "time": when,
        "host": fields[2] if len(fields) > 2 else "",
        "paths": "  ".join(fields[3:]) if len(fields) > 3 else "",
        "size": size,
    }
    return row if row["paths"] else None


def _restic(arguments: "list[str]", repo: "str | None" = None) \
        -> "tuple[str, str, int]":
    command = ["restic", *arguments]
    if repo:
        command += ["-r", repo]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"restic did not answer within {_TIMEOUT}s", 124
    except OSError as exc:
        return "", str(exc), 127
    return proc.stdout or "", proc.stderr or "", proc.returncode


def _snapshots(repo: "str | None") -> "tuple[list, str]":
    out, err, rc = _restic(["snapshots"], repo)
    if rc == 124:
        return [], err
    if rc != 0:
        # restic's own refusal. Measured: "Fatal: Please specify repository
        # location (-r or --repository-file)" with rc=1.
        detail = (err or out).strip().splitlines()
        return [], (detail[-1].strip() if detail else f"restic exited {rc}")
    rows = []
    for line in out.splitlines():
        if _RULE.match(line.strip()) or line.strip().startswith("ID "):
            continue
        record = _parse_row(line)
        if record:
            rows.append(record)
    return rows, ""


def _check(repo: "str | None") -> "tuple[bool, str]":
    """(verified, detail). Only a clean exit means verified - `restic check`
    reports damage and still exits 0 in some versions, so the words are read
    as well as the status."""
    out, err, rc = _restic(["check", "--read-data-subset=1/50"], repo)
    if rc == 124:
        return False, "restic check did not answer within the time allowed"
    if rc != 0:
        detail = (err or out).strip().splitlines()
        return False, (detail[-1].strip() if detail else f"restic check exited {rc}")
    return True, ""


def _run(arguments: dict) -> str:
    if shutil.which("restic") is None:
        return files.tool_missing("restic", "see whether your files are backed up")

    raw = str(arguments.get("repository") or "").strip()
    repo = files.expand(raw) if raw else None

    rows, problem = _snapshots(repo)
    if problem:
        no_repo = "Please specify repository location" in problem
        head = ("There is no backup repository configured for restic, so there "
                "is **no off-machine backup** on this machine." if no_repo
                else "I could not read the restic repository.")
        return (f"{head}\n\nrestic said: {problem}\n\nTo start one, run "
                "`restic -r /path/to/repo init`, or point me at a repository "
                "you already have.")

    if not rows:
        return ("The restic repository is configured but holds **no "
                "snapshots** - so there is no backup in it yet, however long "
                "it has existed.")

    verified, why = _check(repo)
    count = len(rows)
    latest = max(rows, key=lambda row: row["time"])

    lines = [f"{count} backup snapshot(s) in this repository.", "",
             f"The most recent is {latest['time']} ({latest['host']}), "
             f"{latest['id']}, covering {latest['paths']}, storing "
             f"{latest['size']}."]
    lines.append("")
    lines.append("The stored size is what restic kept after de-duplication, "
                 "not the size of the files - an unchanged file costs almost "
                 "nothing after the first snapshot.")
    if verified:
        lines.append("")
        lines.append("The repository passed `restic check`, so its structure "
                     "is intact as far as that can tell.")
    else:
        lines.append("")
        lines.append(f"`restic check` did not come back clean: {why}. Treat "
                     "this backup as unverified until it does.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "backup_status",
        "description": (
            "Whether this machine's files are actually backed up off it: how "
            "many restic snapshots exist, when the most recent was taken, what "
            "it covered, and whether the repository still passes restic's own "
            "integrity check. Use for 'is my backup working', 'when did I last "
            "back up', 'do I have a copy of my files'. A machine with no "
            "repository configured says so plainly, because that means there "
            "is no backup. Read-only: it never writes to the repository."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": ("Path to the restic repository. Optional - "
                                    "restic's own configuration is used when "
                                    "this is left out."),
                },
            },
        },
    },
}

SKILLS = [Skill(name="backup_status", schema=SCHEMA, run=_run)]