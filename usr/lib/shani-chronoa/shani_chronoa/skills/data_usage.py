"""Skill: how much data has this computer used - from vnStat, on this machine.

vnStat (the vnstat package) counts traffic per interface from the kernel's
own counters; nothing is sniffed and nothing leaves the machine. It only
knows what it counted since its service started, and says so when it is not
running.
"""

import json
import subprocess

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "data_usage",
        "description": "How much internet data this computer has used today and this month, "
                       "per network interface (from vnStat).",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1000
    return f"{n:.1f} PB"


def _run(_arguments: dict) -> str:
    try:
        r = subprocess.run(["vnstat", "--json"], capture_output=True, text=True, timeout=10)
    except OSError:
        return "Data usage needs vnStat (the vnstat package), which is not installed."
    except subprocess.TimeoutExpired:
        return "vnStat did not answer."
    if r.returncode != 0:
        return ("vnStat has no data yet - its service (vnstat.service) is not running or has "
                f"not counted anything: {r.stderr.strip()[:150]}")
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return "vnStat's output could not be read."
    lines = []
    for iface in data.get("interfaces", []):
        t = iface.get("traffic", {})
        day = (t.get("day") or [{}])[-1]
        month = (t.get("month") or [{}])[-1]
        if not month:
            continue
        lines.append(f"{iface.get('name')}: this month {_human(month.get('rx', 0))} down and "
                     f"{_human(month.get('tx', 0))} up; today {_human(day.get('rx', 0))} down and "
                     f"{_human(day.get('tx', 0))} up.")
    return " ".join(lines) if lines else "vnStat is running but has not counted any traffic yet."


SKILLS = [Skill(name="data_usage", schema=_SCHEMA, run=_run)]
