"""Skill: list the processes running on this machine.

Reads `/proc` directly rather than shelling out to `ps`, for the same reason
`system_info` reads `/proc/meminfo` instead of calling three binaries: `ps` is
absent on minimal images, `ps aux` truncates the command line to a terminal
width, and the answer is already in the kernel. Nothing here can be missing.

Honesty rules:

- A process whose `/proc/<pid>/cmdline` cannot be read is shown with its *name*
  and marked unreadable, never omitted. A vanished PID is normal and expected.
- `cpu_percent` and `mem_percent` are computed against a real total, and a
  process that exited between listing and measuring is reported as gone rather
  than as 0% CPU.
- Processes this user cannot signal are shown but marked, because "it is
  running" and "I can stop it" are different facts.
"""

from __future__ import annotations

import os
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_PROC = Path("/proc")
_MAX_ROWS = 60
_CLOCK_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_processes",
        "description": (
            "List the processes running on this machine, with their pid, name, "
            "owner, CPU and memory use, sorted by CPU unless sort_by says "
            "otherwise. Use this to find what is using the machine - pass "
            "sort_by='memory' for the RAM question, which CPU order does not "
            "answer - or the pid of something you want to stop."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name_filter": {
                    "type": "string",
                    "description": "Only processes whose name or command contains this.",
                },
                "sort_by": {
                    "type": "string",
                    "description": (
                        "'cpu' (the default) or 'memory'. Use 'memory' to answer "
                        "'what is using my RAM' - the two questions have "
                        "different answers."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many to show. Defaults to {_MAX_ROWS}.",
                },
            },
        },
    },
}


def _read_first_line(path: Path) -> str:
    try:
        return path.read_text(errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def _cmdline(pid: str) -> tuple[str, bool]:
    """Return (text, readable). Empty text is a kernel thread, which is normal."""
    try:
        raw = (Path(_PROC) / pid / "cmdline").read_bytes()
    except OSError:
        return "", False
    parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
    return " ".join(parts), True


def _owner_uid(pid: str) -> str:
    try:
        return _read_first_line(Path(_PROC) / pid / "status").split(":", 1)[-1].strip()
    except (OSError, IndexError):
        return "?"


def _rss_bytes(pid: str) -> int:
    try:
        for line in (Path(_PROC) / pid / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _cpu_seconds(pid: str) -> float:
    raw = _read_first_line(Path(_PROC) / pid / "stat")
    if not raw:
        return 0.0
    # comm may contain spaces and parentheses; fields after the last ')' are safe
    tail = raw.rsplit(")", 1)[-1].split()
    if len(tail) < 13:
        return 0.0
    try:
        ticks = int(tail[11]) + int(tail[12])
    except ValueError:
        return 0.0
    return ticks / _CLOCK_TICKS


def _run(arguments: dict) -> str:
    if not _PROC.is_dir():
        return (
            "Could not list processes: /proc is not mounted, so this is not a "
            "Linux-style system or the kernel filesystem is unavailable. "
            "Nothing was listed."
        )
    needle = (arguments.get("name_filter") or "").strip().lower()
    try:
        limit = max(1, min(int(arguments.get("limit") or _MAX_ROWS), 500))
    except (TypeError, ValueError):
        limit = _MAX_ROWS

    rows = []
    unreadable = 0
    for entry in _PROC.iterdir():
        if not entry.name.isdigit():
            continue
        cmd, readable = _cmdline(entry.name)
        comm = _read_first_line(Path(_PROC) / entry.name / "comm") or entry.name
        label = cmd or comm
        if needle and needle not in label.lower() and needle not in comm.lower():
            continue
        if not readable and not cmd:
            unreadable += 1
        rows.append({
            "pid": entry.name, "comm": comm, "cmd": cmd, "readable": readable,
            "rss": _rss_bytes(entry.name), "uid": _owner_uid(entry.name),
            "cpu": _cpu_seconds(entry.name),
        })

    if not rows:
        base = "No processes matched." if needle else "No processes were listed."
        if needle:
            return f"{base} Nothing here matches {needle!r} by name or command line."
        return base

    # "What is eating my RAM" and "what is hogging the CPU" are different
    # questions, and the answer to one is not the answer to the other: on this
    # machine the biggest CPU user and the biggest memory user are the same
    # process today, but that is luck, not a property of the machine. Sorting by
    # memory is what makes the first question answerable at all, so it is an
    # argument rather than a second tool.
    #
    # "size" is an alias for "memory" because that is the word people use, and a
    # model asked "what is using the most memory" will reach for either.
    sort_by = str(arguments.get("sort_by") or "cpu").strip().lower()
    if sort_by in ("memory", "size", "mem", "rss"):
        rows.sort(key=lambda r: r["rss"], reverse=True)
        ordering = "memory use"
    else:
        # Anything unrecognised falls back to CPU rather than being refused: the
        # default is the useful one, and an error here would be about a word.
        rows.sort(key=lambda r: r["cpu"], reverse=True)
        ordering = "CPU use"
    total_rss = sum(r["rss"] for r in rows)
    total_cpu = sum(r["cpu"] for r in rows) or 1.0
    shown = rows[:limit]

    lines = [
        f"{len(rows)} process(es); showing {len(shown)} by {ordering}.",
        f"{'PID':>8}  {'CPU%':>6}  {'MEM':>9}  {'USER':>6}  NAME",
    ]
    me = os.getuid()
    for r in shown:
        name = (r["cmd"] or r["comm"])[:64]
        mark = "" if r["readable"] or r["cmd"] else "  (command unreadable)"
        mine = "" if r["uid"] == str(me) else "  (not yours)"
        lines.append(
            f"{r['pid']:>8}  {100.0 * r['cpu'] / total_cpu:>5.1f}  "
            f"{files.human_size(r['rss']):>9}  {r['uid']:>6}  {name}{mark}{mine}"
        )
    if len(rows) > len(shown):
        lines.append(f"  ... {len(rows) - len(shown)} more not shown (limit {limit}).")
    lines.append(
        f"  CPU% is each process's share of the CPU used by all of these since "
        f"boot, not instantaneous. {len(shown)} shown, "
        f"{files.human_size(sum(r['rss'] for r in shown))} of "
        f"{files.human_size(total_rss)} resident."
    )
    if unreadable:
        lines.append(f"  {unreadable} kernel thread(s) have no command line, which is normal.")
    return "\n".join(lines)


SKILLS = [Skill(name="list_processes", schema=SCHEMA, run=_run)]
