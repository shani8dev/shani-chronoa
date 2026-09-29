"""Skill: list the windows currently open on the desktop.

The first of three window skills, and the only one that only reads.

**Listing works on Wayland; controlling does not.** `xdotool` drives X11. Under
Wayland it is either absent or, worse, present and talking to an Xwayland server
that only knows about X11 clients - so it would list a fraction of the windows
and look like it had listed them all. Detecting that and returning the fraction
is the quiet wrongness this project avoids.

So there are two paths, and they answer slightly different questions.

Under X11, `xdotool` is authoritative: window ids, geometry, and every window
whether or not it implements accessibility.

Where `xdotool` cannot see the session, the titles come from the AT-SPI
accessibility bus instead - which is D-Bus and works under both. That is a
*different observation*, not a repaired version of the same one, and the message
says so: an app that does not implement AT-SPI is absent from that list, and a
desktop that mirrors a window can report it twice. Measured here, the two paths
disagree - 23 windows by xdotool against 11 titled windows on the bus - which is
exactly why neither is presented as the other.

**The control half is still X11 only.** `focus_window` and `close_window` have no
fallback: AT-SPI exposes no `WindowAction` interface on any window inspected on
this machine, so there is no portable way to raise or close one. Those skills
continue to refuse rather than pretend.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 15
_MAX_WINDOWS = 60

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_windows",
        "description": (
            "List the windows currently open, with their title, class and "
            "geometry. Pass title_contains to narrow the list to windows "
            "matching some text in their title or application - a desktop "
            "usually has more windows open than are worth reading. Where "
            "xdotool cannot see the session it falls back to the accessibility "
            "bus and says so, because that list is not complete."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title_contains": {
                    "type": "string",
                    "description": (
                        "Only list windows whose title or application contains "
                        "this text, case-insensitively. Omit to list every window."
                    ),
                },
            },
        },
    },
}


def session_problem() -> str:
    """Why window control cannot work here, or '' when it can."""
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return (
            "Window control is X11-only here, and this is a Wayland session. "
            "There is no verified way to enumerate windows across GNOME, Plasma "
            "and COSMIC, so nothing is listed rather than a partial list. "
            "Chronoa does not drive the compositor directly."
        )
    if not os.environ.get("DISPLAY"):
        return (
            "No DISPLAY is set, so there is no X11 session to ask. This may be a "
            "Wayland session or a headless login."
        )
    if shutil.which("xdotool") is None:
        return files.tool_missing("xdotool", "list or control windows")
    return ""


def _text_argument(arguments: dict, name: str) -> str:
    """A string argument, or "" for anything that is not one.

    A model can send a number, a list or null for a parameter the schema calls a
    string. Reading it as text without this check raises `AttributeError` on
    `.strip()` and takes the whole turn down, which is a crash over a mistyped
    argument. Anything non-string is treated as absent, which is also what the
    rest of this codebase does with a malformed call.
    """
    value = arguments.get(name) if isinstance(arguments, dict) else None
    return value if isinstance(value, str) else ""


def _matches_filter(row: "tuple", needle: str) -> bool:
    """Whether a `(id, name, class, geometry)` row matches a title filter.

    Case-insensitive substring over the title and the class, because "which
    window is Slack" is asked with either. An empty or absent filter matches
    everything, so the unfiltered call is unchanged.
    """
    if not needle:
        return True
    haystack = " ".join(part for part in (row[1], row[2]) if part).lower()
    return needle.strip().lower() in haystack


def _via_accessibility(arguments: dict | None = None) -> str:
    """Window titles from the AT-SPI bus, for a session xdotool cannot serve.

    The fallback exists because the X11 path has a specific failure mode this
    codebase refuses to paper over: under Wayland, xdotool is either absent or
    present and talking to Xwayland, which knows only about X11 clients. It
    would then list a fraction of the windows and look like it had listed them
    all.

    What comes back here is a *different* observation, not a repaired version of
    the same one, and the message says so. These are the windows that expose
    themselves on the accessibility bus: an app that does not implement AT-SPI
    is absent from this list, and one window can appear twice if the desktop
    mirrors it (the frames window here does, alongside the real one). Calling
    that a partial view is the honest description; calling it the window list
    would be the quiet wrongness the module docstring exists to avoid.
    """
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.senses import accessibility

    config = ChronoaConfig()
    if not config.sense_allowed("accessibility"):
        return (
            "xdotool cannot see this session, and the accessibility bus that "
            "could is turned off. Enable the 'accessibility-sense-enabled' "
            "sense to list windows this way instead."
        )
    try:
        windows, _ = accessibility.read_window_titles()
    except accessibility._Unavailable as exc:
        return f"Could not list windows: {exc}"

    needle = _text_argument(arguments or {}, "title_contains")
    if needle:
        before = len(windows)
        windows = [(app, title) for app, title in windows
                   if needle.strip().lower()
                   in f"{app} {title}".lower()]
        if not windows:
            return (
                f"No window's title or application contains {needle!r}. "
                f"{before} window(s) are on the accessibility bus - call "
                f"list_windows with no filter to see them all."
            )

    if not windows:
        return (
            "No window reported a title on the accessibility bus. Some windows "
            "really do have no title set, and apps that do not implement "
            "AT-SPI are not listed at all, so this is not proof the desktop is "
            "empty."
        )

    kept, withheld = files.cap_list(
        [f"{app} - {title}" for app, title in windows], _MAX_WINDOWS
    )
    body = "\n".join(f"  {row}" for row in kept)
    note = files.withheld_note(
        "window", withheld,
        widen="ask for the window list again, or raise _MAX_WINDOWS",
    )
    return (
        f"{len(windows)} window(s) reported a title on the accessibility bus "
        f"(xdotool cannot see this session, so these come from the a11y bus; "
        f"apps that do not implement AT-SPI are not in this list):\n{body}\n{note}"
    )


def _run(arguments: dict) -> str:
    problem = session_problem()
    if problem:
        # Listing is read-only, so there is a real alternative on a Wayland or
        # headless session. Saying "could not" and stopping would be accurate
        # but wasteful when a different mechanism can answer the same question.
        return _via_accessibility(arguments)
    try:
        proc = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", ""],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"xdotool did not answer within {_TIMEOUT}s, so no windows are listed."
    except OSError as exc:
        return f"Could not list windows: {exc}"

    ids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not ids:
        note = (proc.stderr or "").strip()
        return (
            "xdotool found no visible windows with a name. Some windows really "
            "do have no title set, so this is not proof the desktop is empty."
            + (f" xdotool said: {note}" if note else "")
        )

    rows = []
    for wid in ids[:_MAX_WINDOWS]:
        def ask(*args):
            try:
                out = subprocess.run(["xdotool", *args, wid], capture_output=True,
                                     text=True, timeout=_TIMEOUT, check=False)
                return out.stdout.strip() if out.returncode == 0 else ""
            except (subprocess.TimeoutExpired, OSError):
                return ""
        name = ask("getwindowname")
        cls = ask("getwindowclassname")
        geo = ask("getwindowgeometry")
        rows.append((wid, name, cls, geo))

    # A desktop routinely has more windows than anyone wants to read, and the
    # answer to "which window is Slack" should not carry all twenty-two of them.
    # Filtering after collecting rather than before means the xdotool search
    # stays a single call, and the count reported is of everything found, so a
    # filter that matches nothing is distinguishable from a filter that was
    # ignored.
    title_filter = ""
    title_filter = _text_argument(arguments, "title_contains")
    matched = [row for row in rows if _matches_filter(row, title_filter)]
    if title_filter and not matched:
        return (
            f"No visible window's title or class contains {title_filter!r}. "
            f"{len(rows)} window(s) are open - call list_windows with no "
            f"filter to see them all."
        )
    if len(matched) != len(rows):
        rows = matched

    lines = [f"{len(ids)} visible window(s); showing {len(rows)}:"]
    for wid, name, cls, geo in rows:
        geo_text = ""
        if geo:
            parts = [p for p in geo.splitlines() if p.strip()]
            geo_text = " " + parts[1].strip() if len(parts) > 1 else ""
        lines.append(
            f"  {wid:>10}  {cls or '(no class)':<24} {(name or '(no title)')[:60]}{geo_text}"
        )
    if len(ids) > len(rows):
        lines.append(f"  ... {len(ids) - len(rows)} more not shown (limit {_MAX_WINDOWS}).")
    lines.append("  Use the window id with focus_window or close_window.")
    return "\n".join(lines)


SKILLS = [Skill(name="list_windows", schema=SCHEMA, run=_run)]
