"""Skill: is my disk filling up with logs?

Nothing answered this. `read_logs` reads the **journal** and says nothing about
the files under `/var/log` - which on a machine that has been running a while
is where the gigabytes actually are. `disk_usage` says the filesystem is full.
Neither says *which log*, whether it is still growing, or whether anything
rotates it at all.

**Measured on this box, and the measurement corrected a claim I had already
written down.** `/var/log` is **3.3 GB**, and one file -
`/var/log/Workpuls/output.log` - is **470 MB and still growing** (mtime from
four minutes earlier). Nothing in logrotate's configuration mentions it.

**And `logrotate --debug`'s contract is not what a first reading says:**

    logrotate --debug /etc/logrotate.conf 2>/dev/null | head -3   -> NOTHING
    logrotate --debug /etc/logrotate.conf 2>&1 >/dev/null | head   -> everything
    logrotate --debug /etc/logrotate.conf >/dev/null 2>&1; echo $? -> 1

**All of its output is on stderr, and it exits non-zero when a config is
broken.** Reading it through a pipe reports the pipe's status - which is the
same mistake `read_document`'s `pdffonts` claim made, recorded in `AGENTS.md`.
It is not a harmless detail here: rc=1 with output means *a rotation is
misconfigured*, which is the finding this skill exists to surface.

The measured error shape, on this box:

    error: cloud-init-base:1 duplicate log entry for /var/log/cloud-init.log
    error: found error in file cloud-init-base, skipping

so a rotation that silently stopped is detectable **as an unprivileged user**.
`--debug` is used and always will be: without it `logrotate` actually rotates,
and a skill that answers a question by rewriting the logs is not a reader.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from shani_chronoa.skills import Skill

_TIMEOUT = 25

#: The log directory is a constant rather than a literal in the walk, so a test
#: can point it at a fixture. **The first version of the tests wrote a fixture
#: tree under `tmp_path` and the skill kept reading the real `/var/log`** - so
#: they passed or failed on this machine's actual logs and none of them was
#: testing what it claimed.
_LOG_ROOT = Path("/var/log")

#: Where rotation is configured. A log named in none of these is not rotated,
#: however big it gets.
_CONFIGS = (Path("/etc/logrotate.conf"), Path("/etc/logrotate.d"))

#: **Only a config fault, not a privilege limit.** Measured as an
#: unprivileged user, `logrotate --debug` also emits
#:
#:     error opening state file /var/lib/logrotate/status; assuming empty state: Permission denied
#:     error switching euid from 1001 to 0 and egid from 1001 to 4 (pid 874044): Operation not permitted
#:
#: and counting those as "these stanzas are being skipped" reported **21
#: errors** on a machine whose only real fault was one duplicate entry. They
#: are the *reader's* limits, not the configuration's, so they are named apart.
_CONFIG_FAULT = re.compile(r"^error:\s*(\S+:\d+\s+.+|found error in file \S+, skipping)")

#: The permission-shaped ones: not config faults, reported as a caveat.
_PERMISSION = re.compile(r"switching euid|state file|Permission denied|"
                         r"could not (?:lock|open)|insecure permissions")

#: `warning:` lines are not config faults and are not reported as findings.
_WARNING = re.compile(r"^warning:", re.I)

#: "Actively growing" - mtime within this many seconds.
_GROWING = 300

#: **Paths, *relative to the log root*, whose rotation is somebody else's
#: job.** systemd-journald caps its own files, so `journal/**.journal` being
#: absent from logrotate's config is correct and not a finding. Measured: nine
#: journal files filled the top of the list and every one came back
#: "unrotated", which is wrong.
#:
#: **Relative, not absolute**, because an absolute prefix describes a path on
#: one machine and a fixture's path on another - and a rule that only holds for
#: `/var/log` exactly cannot be exercised anywhere else.
_MANAGED_ELSEWHERE = ("journal/",)

#: **A rotation *archive*, not a live log.** `syslog.1` and `auth.log.2.gz`
#: are what a working rotation leaves behind, so flagging them as "not rotated"
#: reports the rotation's own output as its absence.
_ARCHIVE = re.compile(r"\.(?:\d+|gz|bz2|xz|zst|old)$", re.I)


def _walk(top: Path, ceiling: int) -> "tuple[list, int]":
    """(entries, skipped). Each entry is a real file with a size."""
    found, skipped = [], 0
    try:
        for root, _dirs, names in os.walk(top, onerror=lambda _e: None):
            for name in names:
                path = Path(root) / name
                try:
                    stat = path.stat()
                except OSError:
                    skipped += 1
                    continue
                if not os.path.isfile(path):
                    continue
                found.append((path, stat.st_size, stat.st_mtime))
    except OSError:
        return found, skipped + 1
    found.sort(key=lambda item: -item[1])
    return found[:ceiling], skipped


def _configured_paths() -> "set[str]":
    """Every path logrotate's configuration names, read as text.

    A path present in a config is a path with *a* rotation rule, whatever that
    rule says; the absence of the path is the finding.
    """
    named: set = set()
    texts: list = []
    for entry in _CONFIGS:
        try:
            if entry.is_dir():
                texts.extend(sorted(entry.glob("*")))
            elif entry.is_file():
                texts.append(entry)
        except OSError:
            continue
    for path in texts:
        try:
            body = path.read_text(errors="replace")
        except OSError:
            continue
        # The path is the only part of a stanza that is a path; take quoted
        # and bare tokens that look absolute.
        for match in re.finditer(r'(?:^|\s)(/[^\s\'"]+)', body, re.M):
            named.add(match.group(1))
    return named


def _config_errors() -> "tuple[list, str]":
    """(errors, reason). `logrotate --debug` never touches a log."""
    if shutil.which("logrotate") is None:
        return [], "logrotate is not installed"
    try:
        proc = subprocess.run(
            ["logrotate", "--debug", "/etc/logrotate.conf"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return [], f"logrotate did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return [], str(exc)
    faults, limited = [], []
    for line in proc.stderr.splitlines():
        if _CONFIG_FAULT.match(line):
            faults.append(line.split(":", 1)[1].strip())
        elif _PERMISSION.search(line):
            limited.append(line.strip())
    if not faults and not limited and proc.returncode not in (0, 1):
        return [], f"logrotate exited {proc.returncode}"
    return faults, ("was not run as root, so it could not read its state file "
                    "or switch user" if limited else "")


def _human(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _run_skill(arguments: dict) -> str:
    try:
        limit = max(1, min(int(arguments.get("limit") or 12), 200))
    except (TypeError, ValueError):
        return f"{arguments.get('limit')!r} is not a count."

    top = _LOG_ROOT
    if not top.is_dir():
        return ("There is no /var/log on this machine, so there are no log "
                "files to fill it up with.")

    entries, skipped = _walk(top, limit)
    if not entries:
        return ("I could not read any files under /var/log. That is not the "
                "same as it being empty - a permission problem looks like this "
                "too.")

    total = sum(item[1] for item in entries)
    named = _configured_paths()
    errors, why = _config_errors()

    now = time.time()
    lines = [f"The largest files under /var/log ({len(entries)} shown, "
             f"{_human(total)} between them):"]
    unrotated, growing = [], []
    for path, size, mtime in entries:
        age = int(now - mtime)
        fresh = age < _GROWING
        covered = str(path) in named
        marks = []
        if fresh:
            marks.append(f"**changed {age}s ago**")
            growing.append(path)
        # Only a live, otherwise-unmanaged log is a finding: journald caps its
        # own files and a `.1`/`.gz` is rotation's output.
        try:
            relative = path.relative_to(top).as_posix()
        except ValueError:
            relative = path.as_posix()
        managed = any(relative.startswith(m) for m in _MANAGED_ELSEWHERE)
        archive = bool(_ARCHIVE.search(path.name))
        if not covered and not managed and not archive:
            marks.append("**not rotated by anything**")
            unrotated.append(path)
        lines.append(f"- {_human(size):>10}  {path}"
                     + (f"  ({'; '.join(marks)})" if marks else ""))

    lines.append("")
    if unrotated:
        # Singular as well as plural: "1 of these are not rotated" reads as
        # generated text on the one case a person looks at hardest.
        many = len(unrotated) != 1
        lines.append(
            f"**{len(unrotated)} of these "
            f"{'are' if many else 'is'} not rotated by anything** - nothing "
            "caps them, so they grow until the filesystem is full. That is the "
            "usual cause of a disk filling with no obvious reason.")
    elif growing:
        lines.append("Every large log here is covered by a rotation rule.")
    if growing:
        lines.append(f"**{len(growing)} changed in the last "
                     f"{_GROWING // 60} minutes**, so something is writing to "
                     "them right now.")

    if errors:
        lines.append("")
        lines.append(f"**{len(errors)} logrotate config error(s)** - these "
                     "stanzas are being skipped, so those logs are not "
                     "rotating:")
        for message in errors[:6]:
            lines.append(f"- {message}")
    # **The caveat is orthogonal to the faults, not an alternative to them.**
    # An unprivileged run sees both a real config error *and* its own inability
    # to read the state file; the first version only printed one, so the
    # permission limit hid on a machine where it mattered most.
    if why:
        lines.append("")
        lines.append("(This was not run as root, so logrotate could not read "
                     "its state file or switch user - the errors above may be "
                     "fewer than a root run would find.)")
    else:
        lines.append("")
        lines.append("logrotate's configuration loaded without errors, so "
                     "what it covers is covered.")

    if skipped:
        lines.append(f"({skipped} path(s) under /var/log could not be read.)")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "log_files",
        "description": (
            "See which log files under /var/log are largest, whether they are "
            "still being written to right now, and whether anything rotates "
            "them. Use for 'why is my disk full', 'what is filling up the "
            "disk', 'is that log growing'. Also reports logrotate "
            "configuration errors, which silently stop a rotation. Read-only: "
            "it runs logrotate in debug mode, which rotates nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": ("How many files to show, 1-200. "
                                          "Default 12.")},
            },
        },
    },
}

SKILLS = [Skill(name="log_files", schema=SCHEMA, run=_run_skill)]
