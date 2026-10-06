"""Skill: a stopwatch - start, lap, read, stop - kept in a small state file so
it survives between turns (and restarts of the app). Local."""

import json
import os
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


def _write(state: dict, p) -> None:
    """Write the state atomically, owner-only.

    **Was `p.write_text(json.dumps(...))`, which a crash mid-write turns into a
    truncated file.** The failure mode is self-healing the next time the skill
    reads it (`json.loads` raises, falls back to `{}`, and the stopwatch simply
    reads as not-running) - but it means *every* lap or start that lands at the
    wrong instant silently discards the whole run, and the two call sites below
    both said the same thing, so the crash lives exactly where the state does.

    Same shape as `conversation_store`'s own state writer: a temp file in the
    same directory, fsync, then `os.replace`. The temp is chmodded before the
    replace because `os.replace` preserves the temp's mode, and `restrict_file`
    on the destination *after* would leave the file world-readable in the
    window where it is already at its final path.
    """
    tmp = p.with_suffix(".json.tmp")
    # Same shape as conversation_store.py:271: open the temp owner-only, write,
    # let fdopen close the fd, then one os.replace. No second os.close, and no
    # fsync pretending to be on an fd the with-block already closed - my first
    # draft had both, and pyflakes only catches the second by luck.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
    os.replace(tmp, p)
    files.restrict_file(p)


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
        _write({"start": now, "laps": []}, p)
        return "Stopwatch started."
    if "start" not in state:
        return "The stopwatch is not running. Say 'start the stopwatch'."
    elapsed = now - state["start"]
    if action == "lap":
        state["laps"].append(elapsed)
        _write(state, p)
        return f"Lap {len(state['laps'])}: {_fmt(elapsed)}."
    if action == "stop":
        p.unlink(missing_ok=True)
        laps = "; ".join(f"lap {i + 1} {_fmt(t)}" for i, t in enumerate(state["laps"]))
        return f"Stopped at {_fmt(elapsed)}." + (f" ({laps})" if laps else "")
    return f"{_fmt(elapsed)} so far."


def _verify_stopwatch(arguments: dict, tool=None):
    """Post-condition: is the stopwatch actually in the state that was asked for?

    A stopwatch is the smallest mutator in the package - a number in a file -
    and therefore the clearest case for why every other one needs this: "Started"
    and "Stopped" are claims, and the state file is the only evidence. `start` and
    `stop` both write; `reset`, `lap` and `show` do not, so they return None
    rather than pretending to be checked.

    The state is read from `_path()` in this file rather than from a module:
    there is no separate `stopwatch` module - the first version of this check
    imported one, and guessed an accessor it did not have.
    """
    action = str(arguments.get("action") or "").strip().lower()
    if action not in ("start", "stop", "restart"):
        return None  # nothing was changed
    try:
        raw = _path().read_text(encoding="utf-8")
    except OSError as exc:
        return (False, f"could not read the stopwatch state: "
                       f"{type(exc).__name__}")
    try:
        state = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return (False, "the stopwatch state file is not valid JSON, so the "
                       "stopwatch cannot be trusted")
    if not isinstance(state, dict) or not state:
        return (False, f"the stopwatch state holds {state!r}, not a record")
    running = bool(state.get("running"))
    elapsed = state.get("elapsed")
    if action in ("start", "restart") and not running:
        return (False, f"asked to start, but the stopwatch reads "
                       f"running={running}")
    if action == "stop" and running:
        return (False, f"asked to stop, but the stopwatch still reads "
                       f"running=True")
    detail = f"running={running}"
    if elapsed is not None:
        detail += f", elapsed={elapsed}"
    return (True, f"the stopwatch state reads {detail}")


POST_CONDITION = _verify_stopwatch

SKILLS = [Skill(name="stopwatch", schema=_SCHEMA, run=_run)]
