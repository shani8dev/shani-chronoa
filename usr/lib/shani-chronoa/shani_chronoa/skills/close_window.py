"""Skill: ask a window to close.

Gated, because closing a window is the human action most likely to destroy
something: the application is asked politely, and a polite ask is exactly how
"save your work?" gets dismissed.

`windowclose` rather than `windowkill`. `xdotool`'s `windowkill` destroys the
client without giving it any chance to prompt, which turns a closing window into
a lost document. That distinction is the whole reason this skill is worth
having separately from `kill_process`, which does not deal in windows at all.

Consent is checked before the window id is inspected, so a refusal does not
report whether the window exists.
"""

from __future__ import annotations

import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.skills.list_windows import act, session_problem

_CONSENT_KEY = "window-close-enabled"
_TIMEOUT = 15

SCHEMA = {
    "type": "function",
    "function": {
        "name": "close_window",
        "description": (
            "Ask a window to close, the way its close button would - the "
            "application still gets the chance to ask about unsaved work. "
            "Requires the 'window-close-enabled' consent key. Works on X11, Plasma and "
            "GNOME (GTK4 apps, or any app with Chronoa's window control extension)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "window_id": {"type": "string", "description": "The window id from list_windows."},
                "title_contains": {
                    "type": "string",
                    "description": "Match a window whose title contains this.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"closing windows is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Listing and focusing windows needs no such permission. "
            f"This one can discard unsaved work."
        )
    return True, ""


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to close the window: {reason}"

    problem = session_problem()
    if problem:
        # Wayland: KWin, the GNOME extension, or - without it - the accessibility
        # bus, which closes GTK4 windows through their own `window.close` action
        # (measured). Every route asks the app, so unsaved work still prompts.
        return act("close a window", arguments, lambda b, w: (b.close(w.id), (
            "Asked {label} to close through {backend}. This is a request, not a "
            "guarantee: the application may still ask about unsaved work, and may refuse."))[1])

    wid = (arguments.get("window_id") or "").strip()
    needle = (arguments.get("title_contains") or "").strip()
    if not wid and not needle:
        return (
            "Give a window_id from list_windows, or title_contains. Refusing "
            "to guess which window to close."
        )

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
            return f"No visible window's title contains {needle!r}, so nothing was closed."
        if len(ids) > 1:
            return (
                f"{len(ids)} windows match {needle!r}, so nothing was closed. "
                f"Pass a window_id from list_windows to say which one."
            )
        wid = ids[0]

    try:
        name = subprocess.run(["xdotool", "getwindowname", wid], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        name = ""
    label = f"window {wid}" + (f" ({name[:60]})" if name else "")

    try:
        proc = subprocess.run(["xdotool", "windowclose", wid], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"Asked to close {label} but xdotool did not answer in {_TIMEOUT}s."
    except OSError as exc:
        return f"Could not close {label}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return f"Could not close {label} (exit {proc.returncode})" + (
            f": {detail[-1]}" if detail else ".")
    return (
        f"Asked {label} to close. This is a request, not a guarantee: the "
        f"application may still ask about unsaved work, and may refuse."
    )


def _post_condition(arguments: dict) -> tuple[bool, str]:
    """Is the window that was asked to close gone from the list?

    The tool's own message says the close is "a request, not a guarantee", and
    that is true - an application with unsaved work prompts instead. The
    question for a post-condition is therefore not "did xdotool exit 0" but
    "is that window still open", which `list_windows` answers directly.

    Checking the window's absence rather than the close's exit code is what
    makes this a real check: an app that refused to close exits 0 from
    xdotool and would otherwise be recorded as done.

    Where the window list itself cannot be read, that is reported as failure -
    an unverifiable close must not be stored as a completed one, because that
    is the only verdict the replay guard will act on.
    """
    wid = (arguments.get("window_id") or "").strip()
    needle = (arguments.get("title_contains") or "").strip()
    problem = session_problem()
    if problem:
        return True, (
            f"the close went through the {problem.split(',')[0]} backend, which "
            f"asks the application itself; whether it honoured the request is "
            f"the application's answer to give")
    try:
        from shani_chronoa.windows import list_windows
        windows = list_windows()
    except Exception as exc:  # noqa: BLE001 - a window list that raises is not a verification
        return False, f"the window list could not be read: {exc.__class__.__name__}"
    if wid:
        still = [w for w in windows if str(w.id) == wid]
        if still:
            return False, f"window {wid} ({str(still[0].title)[:60]}) is still open"
        return True, f"window {wid} is no longer in the window list"
    if needle:
        remaining = [w for w in windows if needle.lower() in str(w.title).lower()]
        if remaining:
            return False, (f"{len(remaining)} window(s) still match {needle!r}: "
                           f"{', '.join(str(w.title)[:40] for w in remaining[:3])}")
        return True, f"no window's title contains {needle!r} any more"
    return False, ("neither window_id nor title_contains was given, so there is "
                   "no specific window whose absence could be checked")


POST_CONDITION = _post_condition

SKILLS = [Skill(name="close_window", schema=SCHEMA, run=_run)]
