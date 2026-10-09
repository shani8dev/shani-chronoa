"""Skill: bring a window to the front.

Read-only in effect - it changes which window has focus, not what is in it - so
it is not gated. Closing a window is, and is a separate skill.

Uses the window id from `list_windows` when given one, and matches on title
otherwise. An ambiguous title match is reported as ambiguous with the
candidates listed, rather than picking the first: focusing the wrong window
looks like success to the caller and is not.
"""

from __future__ import annotations

import subprocess

from shani_chronoa.skills import Skill
from shani_chronoa.skills.list_windows import act, session_problem

_TIMEOUT = 15

SCHEMA = {
    "type": "function",
    "function": {
        "name": "focus_window",
        "description": (
            "Bring a window to the front and give it keyboard focus, by window "
            "id from list_windows or by matching part of its title. Works on X11 and "
            "Plasma; on GNOME it needs Chronoa's window control extension and says so."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "window_id": {"type": "string", "description": "The window id, if known."},
                "title_contains": {
                    "type": "string",
                    "description": "Match a window whose title contains this.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    problem = session_problem()
    if problem:
        # Wayland: KWin, or the GNOME extension. The accessibility bus cannot
        # focus (measured: GTK4 atspi_error (1), GTK3 True with nothing raised),
        # and its backend says so with what would make it work.
        return act("focus a window", arguments,
                   lambda b, w: (b.focus(w.id), "Focused {label} through {backend}.")[1])

    wid = (arguments.get("window_id") or "").strip()
    needle = (arguments.get("title_contains") or "").strip()
    if not wid and not needle:
        return "Give a window_id from list_windows, or title_contains to match by title."

    if not wid:
        try:
            found = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", needle],
                capture_output=True, text=True, timeout=_TIMEOUT, check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"Could not search for a window titled {needle!r}: {exc}"
        ids = [x.strip() for x in found.stdout.splitlines() if x.strip()]
        if not ids:
            return f"No visible window's title contains {needle!r}."
        if len(ids) > 1:
            names = []
            for candidate in ids[:8]:
                try:
                    n = subprocess.run(["xdotool", "getwindowname", candidate],
                                       capture_output=True, text=True, timeout=_TIMEOUT,
                                       check=False)
                    names.append(f"    {candidate}: {n.stdout.strip()[:60]}")
                except (subprocess.TimeoutExpired, OSError):
                    pass
            return (
                f"{len(ids)} windows match {needle!r}, so nothing was focused - "
                f"guessing would focus the wrong one. Narrow it, or pass a "
                f"window_id:\n" + "\n".join(names)
            )
        wid = ids[0]

    try:
        proc = subprocess.run(["xdotool", "windowactivate", "--sync", wid],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"Asked xdotool to focus window {wid} but it did not answer in {_TIMEOUT}s."
    except OSError as exc:
        return f"Could not focus window {wid}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return (
            f"Could not focus window {wid} (exit {proc.returncode})"
            + (f": {detail[-1]}" if detail else ". The window may have closed.")
        )
    try:
        name = subprocess.run(["xdotool", "getwindowname", wid], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        name = ""
    return f"Focused window {wid}" + (f" ({name[:60]})." if name else ".")


SKILLS = [Skill(name="focus_window", schema=SCHEMA, run=_run)]
