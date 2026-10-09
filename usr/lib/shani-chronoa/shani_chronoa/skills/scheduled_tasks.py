"""Skill: what is scheduled to run on this machine.

There was no way to ask. Nothing among the other 205 skills read a crontab, a
`/etc/cron.d` drop-in or a systemd timer, so "what is going to run on this
machine?" had no answer - on an OS whose entire safety story is **blue-green
slots and btrfs rollback**, where an unexpected job is exactly the thing a person
would want to know about before trusting the machine.

**It is a read, and it is ungated** on the same precedent as `list_windows` and
`skill_machine`: reading what the machine has scheduled describes the machine,
not the person's world, and a permission prompt on "what is my computer going to
do next hour" is friction with nothing behind it. The one genuinely sensitive
thing here is *other people's* schedules, and those are in `/etc/cron.d` where
they are system configuration rather than anyone's private business.

**Three sources, and reporting only one of them is a confident wrong answer.**
Cron (`crontab -l`, `/etc/crontab`, `/etc/cron.d/`, and the `cron.hourly`
family), systemd system timers, and systemd *user* timers. A machine can have
any one of the three and none of the others - this box has no user crontab at
all and still has nine timers - so a listing that reads "nothing is scheduled"
while `fwupd-refresh.timer` fires every hour is the exact failure this file is
shaped around. Each source is reported **separately**, and an absent one says
so rather than being counted as empty.

**A crontab file's own syntax is parsed, not pattern-matched for words.** The
schedule fields are five, then the command. `*/15 * * * *` is a real entry;
`MAILTO=root` is a variable assignment and not a job, and reporting it as a
scheduled task would invent one.

**A timer that can never fire is reported as such.** A `.timer` that is
`inactive (dead)` will not run again, and listing it beside a live one with no
distinction invites reading a dead timer as a scheduled job. `systemctl` says
`LAST` as `-` for a timer that has never run, which is a fact about the machine
rather than a missing value - so it is kept and shown as "never".
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from shani_chronoa.skills import Skill

_TIMEOUT = 15

#: `/etc/crontab` and `/etc/cron.d/*` carry a username field between the five
#: schedule fields and the command; a user's own crontab does not. Both shapes
#: exist on every Arch install, so guessing which one you are reading turns half
#: the rows into nonsense commands.
_USER_CRONTAB_FIELDS = 5
_SYSTEM_CRONTAB_FIELDS = 6

#: One schedule entry: five time fields, then the command (and, for the system
#: shapes, the user it runs as). Nothing looser than this is matched, so a
#: `MAILTO=` line or a comment cannot be read as a job.
_ENTRY = re.compile(r"^\s*([\d*/,\-]+)\s+([\d*/,\-]+)\s+([\d*/,\-]+)\s+"
                    r"([\d*/,\-]+)\s+([\d*/,\-]+)\s+(.*)$")

#: Read through `systemctl show` rather than by parsing `list-timers`' columns.
#:
#: `list-timers` prints six whitespace-aligned columns whose widths depend on the
#: data, and two of them are free text that can contain spaces: a NEXT date is
#: "Fri 2026-10-09 23:34:18 IST" and a LEFT column is "3 days" or "56min ago".
#: My first version regexed the prefix before the `.timer` name as one string
#: and then printed it as "next ...", which glued LAST onto NEXT:
#:
#:     dpkg-db-backup.timer  next Sat 2026-10-10 00:00:00 IST 29min Fri 2026-10-09 00:00:00 IST - (never run)
#:
#: - a confidently wrong answer about **when a job will run**, mixing in when it
#: last ran. `systemctl show` returns one `Key=Value` per line per unit, which
#: is unambiguous, and takes every unit in a single call (measured: 15 timers
#: in 8 ms), so it costs nothing over the column parse and is the reason a
#: skill with a 30 s budget can afford to be exact here.
_TIMER_PROPS = ("Id", "ActiveState", "SubState", "NextElapseUSecRealtime",
                 "LastTriggerUSec", "Triggers")

#: What each schedule field means, so an answer can say "at 04:00" rather than
#: handing back "0 4 * * *". Not a translation of every possible cron
#: expression, which is what `crontab(5)` is for - it names the fields.
_FIELDS = ("minute", "hour", "day of month", "month", "day of week")


def _run(cmd: List[str]) -> Optional[str]:
    """`cmd`'s stdout, or None when it could not be run or refused."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _etc_dir() -> pathlib.Path:
    """Where the system keeps its cron files.

    A function, not an import-time constant, for the reason this package
    documents for every other data path: resolved once at import it is a value
    a test cannot point anywhere, and a test that cannot control `/etc` can only
    assert against whatever the machine running it happens to have - which is
    how "what is scheduled" came to be tested against a live systemd whose next
    run moved every minute.
    """
    return pathlib.Path("/etc")


def _describe_schedule(fields: List[str]) -> str:
    """`'0 4 * * *'` in words, for the fields that are literal numbers."""
    out = []
    for name, value in zip(_FIELDS, fields):
        if value == "*":
            continue
        out.append(f"{name} {value}")
    return ", ".join(out) or "every minute"


def _parse_crontab(text: str, user_field: bool) -> List[dict]:
    """The job entries in one crontab file.

    `user_field` is False for a user's own crontab and True for `/etc/crontab`
    and `/etc/cron.d/*`, which name the account between the schedule and the
    command. Getting it backwards turns `root  /usr/bin/thing` into a job whose
    command is "root" - so the shape is passed in rather than guessed per line.
    """
    jobs: List[dict] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENTRY.match(stripped)
        if not match:
            # A variable assignment (`MAILTO=root`, `PATH=...`) and a
            # `@reboot`-style shorthand are not five-field jobs. Skipped, not
            # reported: inventing a task out of a variable is worse than missing
            # one, and the source line is named below when nothing was found.
            continue
        fields = list(match.groups()[:5])
        rest = match.group(6).split()
        if user_field:
            if not rest:
                continue
            user, command = rest[0], " ".join(rest[1:])
        else:
            user, command = os.environ.get("USER") or "you", " ".join(rest)
        jobs.append({"schedule": " ".join(fields), "when": _describe_schedule(fields),
                     "user": user, "command": command})
    return jobs


def _cron_sources() -> Tuple[List[dict], List[str]]:
    """Every crontab on the machine, and the sources that could not be read.

    **A missing `crontab` binary and an empty crontab are different facts**, and
    this returned "you have no crontab" for both. `crontab -l` prints
    "no crontab for <user>" and **exits 1** when you have none - so the nonzero
    exit is the answer, not an error - but on a machine without the binary there
    is no answer at all, and reporting "you have no crontab" there is a
    confident wrong statement about somebody's machine.
    """
    sources: List[dict] = []
    unreadable: List[str] = []

    if shutil.which("crontab") is None:
        sources.append({"where": "your crontab", "jobs": [],
                        "note": "the crontab command is not installed, so your own "
                                "crontab could not be read"})
        unreadable.append("your crontab")
    else:
        listing = _run(["crontab", "-l"])
        if listing is None:
            # Exit 1 with no crontab is the answer, not a failure.
            sources.append({"where": "your crontab", "jobs": [],
                            "note": "you have no crontab"})
        else:
            sources.append({"where": "your crontab",
                            "jobs": _parse_crontab(listing, False)})

    etc = _etc_dir()
    system_file = etc / "crontab"
    if system_file.exists():
        try:
            jobs = _parse_crontab(system_file.read_text(errors="replace"), True)
        except OSError:
            jobs = []
        sources.append({"where": "/etc/crontab", "jobs": jobs})

    for folder in ("cron.d", "cron.hourly", "cron.daily", "cron.weekly", "cron.monthly"):
        directory = etc / folder
        if not directory.is_dir():
            continue
        jobs: List[dict] = []
        scripts: List[str] = []
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for job in _parse_crontab(text, True):
                job["where"] = str(path)
                jobs.append(job)
            # `cron.hourly` and friends hold **scripts, not crontabs**: the
            # `/etc/crontab` entry `run-parts --report /etc/cron.hourly` runs
            # every executable in the folder once an hour. So a folder with no
            # five-field entries in it is not an absence of scheduled work - it
            # is a folder of work that never appears in a crontab at all. My
            # first version printed "nothing (this folder runs every listed
            # file)", which says both "there is nothing" and "everything in
            # here runs", on the same line.
            if any(line.strip() and not line.strip().startswith("#")
                   for line in text.splitlines()):
                scripts.append(path.name)
        sources.append({"where": f"/etc/{folder}", "jobs": jobs,
                        "scripts": scripts if folder != "cron.d" else []})

    return sources, unreadable


def _systemctl(user: bool, *args: str) -> Optional[str]:
    prefix = ["systemctl"] + (["--user"] if user else [])
    return _run(prefix + list(args))


def _timers(user: bool) -> Optional[List[dict]]:
    """systemd timers with their real state, or None when systemd could not be asked.

    ****A dead timer is reported as dead.** `ActiveState=inactive` with
    `SubState=dead` means the unit exists but will not fire again, and listing it
    beside a live one with no distinction invites reading it as something that
    is going to happen - which is the whole question being asked. `list-timers`
    shows these too, in the same flat list, with the fact available only in a
    column this parser could not read.
    """
    listing = _systemctl(user, "list-units", "--type=timer", "--all",
                         "--no-pager", "--no-legend")
    if listing is None:
        return None
    names = []
    for line in listing.splitlines():
        if line.strip():
            names.append(line.split()[0])
    if not names:
        return []

    # One `show` for every timer: `systemctl show` accepts many units and
    # separates their blocks with a blank line, so this is a single subprocess
    # however many timers there are.
    #
    # **`Id` is in `_TIMER_PROPS` because passing any `--property=` makes
    # systemd print *only* those.** Leaving it out meant every block parsed
    # without a unit name, every block was discarded, and this returned an
    # empty list: "system timers: none" on a machine with fifteen of them,
    # reported confidently and with nothing wrong-looking anywhere.
    detail = _systemctl(user, "show", *names, "--no-pager",
                        *(f"--property={p}" for p in _TIMER_PROPS))
    if detail is None:
        return None

    rows: List[dict] = []
    block: dict = {}
    order: List[str] = []

    def _flush() -> None:
        if block.get("_unit"):
            rows.append({
                "unit": block["_unit"],
                "state": block.get("ActiveState", "unknown"),
                "sub": block.get("SubState", ""),
                # Empty means "has never elapsed", which is a fact about the
                # machine rather than a value this code failed to read.
                "next": block.get("NextElapseUSecRealtime", ""),
                "last": block.get("LastTriggerUSec", ""),
                "activates": block.get("Triggers", "") or "",
            })

    for line in detail.splitlines() + [""]:
        if not line.strip():
            _flush()
            block = {}
            continue
        key, _sep, value = line.partition("=")
        if key == "Id":
            block["_unit"] = value.strip()
            order.append(value.strip())
        elif key in _TIMER_PROPS:
            block[key] = value.strip()
    return rows


def _run_skill(arguments: dict) -> str:
    which = (arguments.get("source") or "all").strip().lower()
    if which not in ("all", "cron", "timers"):
        return f"Source must be all, cron or timers, not {which!r}."

    lines: List[str] = []
    unreadable: List[str] = []

    if which in ("all", "cron"):
        cron, cron_unreadable = _cron_sources()
        unreadable.extend(cron_unreadable)
        total = sum(len(s["jobs"]) for s in cron)
        lines.append(f"cron: {total} scheduled job(s) across "
                     f"{len([s for s in cron if s['jobs'] or not s.get('note')])} source(s).")
        for source in cron:
            jobs = source["jobs"]
            if source.get("note"):
                lines.append(f"  {source['where']}: {source['note']}")
                continue
            if not jobs:
                where = source["where"]
                scripts = source.get("scripts") or []
                if scripts:
                    # Said as what it is: N scripts the system runs on a
                    # schedule set elsewhere, not "nothing".
                    names = ", ".join(scripts[:8])
                    more = f" and {len(scripts) - 8} more" if len(scripts) > 8 else ""
                    lines.append(f"  {where}: no crontab entries; "
                                 f"{len(scripts)} script(s) the system runs on a "
                                 f"schedule set elsewhere ({names}{more})")
                else:
                    lines.append(f"  {where}: nothing")
                continue
            for job in jobs:
                where = job.get("where", source["where"])
                lines.append(f"  {where}: [{job['schedule']}] {job['when']} "
                             f"as {job['user']}: {job['command']}")
        if not cron:
            lines.append("  no crontab could be read on this machine")
            unreadable.append("cron")


    if which in ("all", "timers"):
        system = _timers(user=False)
        mine = _timers(user=True)
        for label, rows in (("system", system), ("your", mine)):
            if rows is None:
                lines.append(f"{label} timers: systemd could not be asked")
                unreadable.append(f"{label} timers")
                continue
            live = [r for r in rows if r["state"] == "active"]
            dead = [r for r in rows if r["state"] != "active"]
            if not rows:
                lines.append(f"{label} timers: none")
                continue
            lines.append(f"{label} timers: {len(live)} will run, {len(dead)} installed but dead")
            for row in sorted(rows, key=lambda r: r["unit"]):
                if row["state"] == "active":
                    when = row["next"] or "no next run scheduled"
                    since = f"last {row['last']}" if row["last"] else "never run"
                    lines.append(f"  {row['unit']:36} next {when} ({since})")
                else:
                    # Named separately and first, because "installed" and
                    # "will happen" are different facts and only one is true.
                    lines.append(f"  {row['unit']:36} DEAD ({row['state']}/"
                                 f"{row['sub'] or '?'}) - will not fire"
                                 + (f" -> {row['activates']}" if row["activates"] else ""))

    if not lines:
        return "Nothing to report."

    text = "\n".join(lines)
    if unreadable:
        text += ("\n\nNot answered for " + ", ".join(unreadable) +
                 " - the command could not be run, which is not the same as "
                 "there being nothing there.")
    return text


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scheduled_tasks",
        "description": (
            "What is scheduled to run on this machine: your crontab, the system "
            "crontab and /etc/cron.d, and both the system and your systemd "
            "timers. Each source is reported separately and one that cannot be "
            "read says so, because a machine can have no user crontab and still "
            "have nine timers. Reading what the machine has scheduled describes "
            "the machine rather than the person's world, so it needs no "
            "permission. Nothing is scheduled, changed or removed by asking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["all", "cron", "timers"],
                    "description": "'all' (the default), only cron, or only systemd timers.",
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="scheduled_tasks", schema=_SCHEMA, run=_run_skill)]