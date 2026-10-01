"""Skill: a stopwatch - start, lap, read, stop - kept in a small state file so
it survives between turns (and restarts of the app). Local."""

import json
import time

from shani_chronoa import files
from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "stopwatch",
        "description": "A stopwatch: start, lap, read the time so far, or stop.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["start", "lap", "read", "stop"]}}, "required": ["action"]},
    },
}


def _path():
    p = files.data_home() / "shani-chronoa" / "stopwatch.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _fmt(s: float) -> str:
    m, sec = divmod(s, 60)
    h, m = divmod(int(m), 60)
    return (f"{h} h {m} min {sec:.1f} s" if h else f"{m} min {sec:.1f} s" if m else f"{sec:.1f} s")


def _run(arguments: dict, now: "float | None" = None) -> str:
    now = time.time() if now is None else now
    p = _path()
    try:
        state = json.loads(p.read_text())
    except (OSError, ValueError):
        state = {}
    action = arguments.get("action")
    if action == "start":
        p.write_text(json.dumps({"start": now, "laps": []}))
        return "Stopwatch started."
    if "start" not in state:
        return "The stopwatch is not running. Say 'start the stopwatch'."
    elapsed = now - state["start"]
    if action == "lap":
        state["laps"].append(elapsed)
        p.write_text(json.dumps(state))
        return f"Lap {len(state['laps'])}: {_fmt(elapsed)}."
    if action == "stop":
        p.unlink(missing_ok=True)
        laps = "; ".join(f"lap {i + 1} {_fmt(t)}" for i, t in enumerate(state["laps"]))
        return f"Stopped at {_fmt(elapsed)}." + (f" ({laps})" if laps else "")
    return f"{_fmt(elapsed)} so far."


SKILLS = [Skill(name="stopwatch", schema=_SCHEMA, run=lambda a: _run(a))]
