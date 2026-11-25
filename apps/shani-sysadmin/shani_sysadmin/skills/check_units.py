"""Skill: is a systemd unit file actually valid, and why will it not start?

`control_service` can start, stop, restart and enable a unit. It cannot answer
the question that comes first - *"why doesn't it start?"* - and nothing here
looked at a unit **file** at all. `boot_report` uses `systemd-analyze time` and
`blame`; `systemd-analyze verify`, which is the command that answers exactly
that, was unused.

**Two behaviours measured on this machine, and both would have shipped a
confident wrong answer:**

- **The exit status is not the verdict, and it is not stable across versions.**
  The first version of this docstring recorded *"exits 0 whether or not it found
  problems"* - measured on the systemd of the machine this was written on.
  Measured again here (systemd 257, Ubuntu 26.04) with the same broken unit: it
  prints the complaint and **exits 1**. A reader that trusts the status code is
  right on one version and wrong on the other, which is exactly why the
  complaint text is what this module reads and the exit code is read for
  nothing at all.
- **It verifies the whole system, not the unit you asked about.** The same two
  invocations printed the same three pre-existing complaints about
  `/usr/lib/systemd/system/com.Workpuls.service`, which has nothing to do with
  either file. So the output is **filtered to the units and paths asked about**,
  or every run would blame somebody else's unit file.

**Passing this is not the same as the service starting.** `verify` is a static
check of the file: it does not know that a dependency is down, that a path is
mounted, or that the user does not exist. A clean report says the file is
well-formed, and says so, rather than the service will work.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from typing import List, Optional

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 30
_MAX_REPORTED = 25


def _verify(units: List[str]):
    """`systemd-analyze verify` on `units`, or None when it could not be run."""
    try:
        return subprocess.run(["systemd-analyze", "verify", *units],
                              capture_output=True, text=True, timeout=_TIMEOUT,
                              check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _wanted(unit: str) -> str:
    """The unit's base name, so `foo.service` and `/etc/.../foo.service` match."""
    return pathlib.PurePath(unit).name


def _for_units(output: str, units: List[str]) -> List[str]:
    """The complaint lines that are about the units asked about.

    `verify` reports in two shapes: `<unit>: <message>` and
    `<path>:<line>: <message>`. The first is matched on the unit name; the second
    on the path, so a complaint about a line inside the file asked about counts.
    Anything else is another unit's business and is dropped - see the module
    docstring for why dropping it is the point.
    """
    names = {_wanted(u) for u in units}
    prefixes = tuple(os.path.realpath(u) for u in units
                     if os.path.sep in u)
    out: List[str] = []
    for line in output.splitlines():
        line = line.rstrip()
        if not line:
            continue
        head = line.split(":", 1)[0].strip()
        if head in names or (prefixes and head.startswith(prefixes)):
            out.append(line)
        elif any(head.endswith(name) for name in names):
            out.append(line)
    return out


def _known_units(user: bool) -> Optional[List[str]]:
    """Unit files systemd can see, so a name can be offered rather than guessed.

    **No `--no-finity`.** Measured: this systemd answers
    `systemctl: unrecognized option '--no-finity'` - and **exits 0** - so the
    flag made every listing come back empty while looking like a healthy
    machine with no services. Same shape as the exit-code traps twice already:
    a flag that is rejected quietly is worse than one that is obviously wrong.

    **`systemctl` is checked for like `systemd-analyze` is.** They ship
    together, but "ships together" is not a guarantee and a missing binary is
    an OSError from `subprocess.run` rather than the UNKNOWN this call is
    supposed to return - which `tests/test_disk_usage_missing_df.py`'s
    unguarded-command scan exists to catch, and did, on this very function.
    """
    if shutil.which("systemctl") is None:
        return None
    argv = ["systemctl"] + (["--user"] if user else []) + \
           ["list-unit-files", "--type=service", "--no-pager", "--no-legend",
            ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    names = []
    for row in proc.stdout.splitlines():
        head = row.split()[0] if row.split() else ""
        if head.endswith((".service", ".socket", ".timer", ".mount", ".path")):
            names.append(head)
    return sorted(set(names))


def _run(args: dict) -> str:
    if shutil.which("systemd-analyze") is None:
        return (files.tool_missing("systemd-analyze",
                                   "check whether a systemd unit file is valid")
                + " Nothing was guessed about it.")

    units = [u.strip() for u in str(args.get("unit") or "").split(",")
             if u.strip()]
    if not units:
        units = [u for u in str(args.get("units") or "").split(",") if u.strip()]

    if not units:
        known = _known_units(user=False)
        mine = _known_units(user=True)
        if known is None and mine is None:
            return ("Could not list the units on this machine - systemd did not "
                    "answer. Name the unit to check, e.g. 'nginx.service'.")
        names = sorted(set(known or []) | set(mine or []))
        shown = [n for n in names if n.endswith(".service")][:40]
        head = (f"{len(shown)} service(s) systemd can see here. Name one to "
                f"check, e.g. 'chronoa.service'." if shown else
                "No service unit files were found here.")
        return "\n".join([head, *(f"  {n}" for n in shown)])

    # A path is verified as given; a bare name is looked for on disk so that
    # "why won't my unit start" works without the person knowing where it lives.
    paths: List[str] = []
    for unit in units:
        candidate = pathlib.Path(unit).expanduser()
        if candidate.exists():
            paths.append(str(candidate))
        elif "/" in unit:
            return (f"{unit} does not exist, so there is nothing to check. "
                    f"Nothing was read.")
        else:
            paths.append(unit)

    proc = _verify(paths)
    if proc is None:
        return (f"systemd-analyze did not answer within {_TIMEOUT}s for "
                f"{', '.join(units)}, so whether these are valid is UNKNOWN. "
                f"Nothing was guessed.")

    complaints = _for_units((proc.stdout or "") + "\n" + (proc.stderr or ""),
                            units)
    if complaints:
        lines = [f"{len(complaints)} problem(s) in "
                 f"{', '.join(_wanted(u) for u in units)}:"]
        lines += [f"  {c}" for c in complaints[:_MAX_REPORTED]]
        if len(complaints) > _MAX_REPORTED:
            lines.append(f"  ... and {len(complaints) - _MAX_REPORTED} more")
        return "\n".join(lines)

    return ("No problems found in "
            f"{', '.join(_wanted(u) for u in units)}: the unit file parses, its "
            "dependencies exist and its ExecStart is executable.\n\n"
            "That is a check of the *file* only. It does not know whether a "
            "dependency is currently down, whether a path is mounted, or whether "
            "the User= it names exists - so a clean report here does not promise "
            "the service will start. If it still will not, "
            "`journalctl -u <unit> -b` is where the runtime reason is, and "
            "`read_logs` reads it.")


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_units",
        "description": (
            "Check whether systemd unit files are valid and say what is wrong "
            "with them - a missing ExecStart binary, an unknown key, a "
            "dependency that does not exist - by running systemd-analyze verify "
            "on just the units you name. With no unit named, lists the services "
            "this machine has. This is a static check of the file: it does not "
            "know whether a dependency is down or a path is mounted, so a clean "
            "report does not promise the service will start. Reading only; "
            "nothing is started, stopped or changed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "unit": {
                    "type": "string",
                    "description": ("Unit name(s) or path(s), comma-separated, "
                                    "e.g. 'nginx.service' or "
                                    "'~/.config/systemd/user/chronoa.service'."),
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="check_units", schema=_SCHEMA, run=_run)]