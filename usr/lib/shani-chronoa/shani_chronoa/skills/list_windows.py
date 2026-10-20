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

**Control on Wayland goes through `shani_chronoa.windows`** (measured
2026-10-08). On Plasma that is KWin's scripting API, the full set. On GNOME it is
Chronoa's own Shell extension when the person has enabled it - GNOME offers other
programs nothing else - and otherwise the accessibility bus, which turned out to
do part of it: GTK4 frames expose `window.close`, `window.minimize` and
`window.toggle-maximized` as actions, and pressing them works. It cannot focus
or move anything (GTK4: `atspi_error (1)`; GTK3: True with nothing raised, and
no WindowAction-style verbs at all), so those still refuse, saying what would
make them work.
"""

from __future__ import annotations

import os
import shutil

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
    """Why the xdotool path cannot work here, or '' when it can."""
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return (
            "this is a Wayland session, where xdotool sees only X11 clients, so "
            "the desktop's own window control is used instead"
        )
    if not os.environ.get("DISPLAY"):
        return (
            "No DISPLAY is set, so there is no X11 session to ask. This may be a "
            "Wayland session or a headless login."
        )
    if shutil.which("xdotool") is None:
        return files.tool_missing("xdotool", "list or control windows")
    return ""


def control_backend():
    """(backend, "") for window control off the xdotool path, or (None, why).

    The accessibility-bus backend reads the same bus the accessibility sense
    does, so it is behind the same switch.
    """
    from shani_chronoa import windows
    from shani_chronoa.windows.atspi import AtspiBackend
    backend, why = windows.detect()
    if isinstance(backend, AtspiBackend):
        from shani_chronoa.config import ChronoaConfig
        if not ChronoaConfig().sense_allowed("accessibility"):
            return None, ("the accessibility bus is the route for windows here. "
                          + ChronoaConfig().sense_allowed_reason("accessibility")
                          + (f". {backend.note}" if backend.note else ""))
    return backend, why


def act(verb: str, arguments: dict, call) -> str:
    """Find one window from `window_id`/`title_contains` and run `call(backend, w)`.

    The shared off-xdotool path for every control skill: one backend, one
    window (an ambiguous title is refused with the candidates), and the
    backend's own sentence when it cannot do the verb.
    """
    from shani_chronoa import windows
    backend, why = control_backend()
    if backend is None:
        return f"Could not {verb}: {why}."
    try:
        target = windows.find(backend.list(), window_id=_text_argument(arguments, "window_id").strip(),
                              title_contains=_text_argument(arguments, "title_contains"))
        said = call(backend, target)
    except windows.WindowError as exc:
        return f"Could not {verb}: {exc}"
    label = f"window {target.id}" + (f" ({target.title[:60]})" if target.title else "")
    return said.format(label=label, backend=backend.name)


#: Rows whose verbs are asked for. `controllable()` walks `_frames()` per call,
#: so a listing of 60 windows would enumerate the whole bus 60 times; a bounded
#: probe with an honest note about the remainder is better than an unbounded one
#: that turns an instant listing into a slow one.
_PROBED = 12


def _verbs_for(backend, window, asked: bool) -> "str | None":
    """What verbs this window offers, for the listing to show.

    **`AtspiBackend.controllable()` existed and had no caller in the tree.** Its
    docstring is one line - *"The verbs this window offers, for a listing to show"* -
    and this listing is the thing it was written for.

    Without it the closing line told you the same thing about every window:

        Use the id with focus_window, close_window or arrange_window.

    Measured on a frame reporting only `window.close`:

    | verb pressed | result |
    |---|---|
    | `close` | offered |
    | `minimize` | **refused**: "this application does not offer 'minimize' on the accessibility bus (GTK4 apps do; GTK3 apps and Chromium-based browsers do not)" |
    | `maximize` | **refused**, same message |

    and on a frame reporting `activate`, all three refused. So the advice was a
    guaranteed failure for most windows, and the function that would have said so
    was the one thing not called.

    Returns `None` when the backend has no answer - which is every backend except
    `AtspiBackend`, where `controllable` is not declared at all - and when the
    probe budget is spent. Callers treat `None` as *say nothing*, never as *none
    offered*: a listing that cannot tell should not assert an empty set.
    """
    from shani_chronoa import windows

    probe = getattr(backend, "controllable", None)
    if probe is None or not asked:
        return None
    try:
        verbs = probe(window.id)
    except windows.WindowError:
        return "verbs unknown - it left the bus while being listed"
    except Exception:  # noqa: BLE001 - a probe must not fail the listing
        return None
    if not verbs:
        return "no verbs on the accessibility bus"
    return ", ".join(verbs)


def _via_backend(backend, arguments: dict) -> str:
    """The window list from the desktop's own window control, with usable ids."""
    from shani_chronoa import windows
    try:
        found = backend.list()
    except windows.WindowError as exc:
        return f"Could not list windows: {exc}"
    needle = _text_argument(arguments, "title_contains")
    shown = [w for w in found if w.matches(needle)]
    if needle and not shown:
        return (f"No window's title or application contains {needle!r}. {len(found)} "
                f"window(s) are open - call list_windows with no filter to see them all.")
    if not found:
        return f"{backend.name} reports no open windows."
    # The verbs go **after** the cap, not before it: probing a window that is
    # then withheld would spend bus round-trips on rows nobody sees. The budget
    # covers the rows that are actually shown, and the note below says when the
    # run stopped early rather than letting the tail look unprobed.
    kept, withheld = files.cap_list([windows.describe(w) for w in shown], _MAX_WINDOWS)
    probed = 0
    rows = []
    for row, window in zip(kept, shown):
        asked = probed < _PROBED
        verbs = _verbs_for(backend, window, asked)
        if asked:
            probed += 1
        rows.append(f"{row}  <{verbs}>" if verbs else row)
    note = files.withheld_note("window", withheld, widen="narrow it with title_contains")
    if probed < len(kept):
        note += (f"  The verbs of the last {len(kept) - probed} window(s) were not "
                 f"checked: each check walks the accessibility bus, and the listing "
                 f"stops at {_PROBED} so it stays quick. Narrow it with "
                 f"title_contains to see those.\n")
    return (f"{len(found)} window(s) from {backend.name}; showing {len(shown)}:\n"
            + "\n".join(f"  {row}" for row in rows) + f"\n{note}"
            + "  Use the id with focus_window, close_window or arrange_window.")


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
            "could is the route instead. "
            + config.sense_allowed_reason("accessibility")
        )
    try:
        windows, _ = accessibility._bounded(accessibility.read_window_titles)
    except accessibility._TimedOut:
        return (
            f"Could not list windows: the accessibility tree walk did not return "
            f"within {accessibility._TIMEOUT_SECONDS:g}s, so it was given up on. "
            f"That is not the same as there being no windows - something on the "
            f"bus stopped answering. It will clear when that application does."
        )
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
    """The window list, from the desktop's own window control.

    **One path, and it is the desktop's own.** This used to fork one `xdotool
    search` and then one process *per window* for its name, its class and its
    geometry - and `xdotool getwindowclassname` **does not exist** in this build,
    so every class came back empty while the call still exited 0. Measured on both
    counts: `xdotool` is absent from the GNOME *and* the Plasma ShaniOS image, so
    that block could not run where this ships at all, and the backend that
    replaces it reads `_NET_CLIENT_LIST` and its properties over one connection.

    The X11/xdotool distinction is kept where it is still true - a Wayland session
    falls to the accessibility bus, and `session_problem` still says so - but the
    listing itself is one call whichever display server it runs under.
    """
    problem = session_problem()
    if problem and not os.environ.get("DISPLAY"):
        # No display at all: no backend can run, and the a11y bus is the only
        # thing left. Reported as partial, as it always was.
        from shani_chronoa.windows.atspi import AtspiBackend
        backend, why = control_backend()
        if backend is not None and not isinstance(backend, AtspiBackend):
            return _via_backend(backend, arguments)
        return _via_accessibility(arguments)
    backend, why = control_backend()
    if backend is None:
        return f"Could not list windows: {why}."
    return _via_backend(backend, arguments)


SKILLS = [Skill(name="list_windows", schema=SCHEMA, run=_run)]
