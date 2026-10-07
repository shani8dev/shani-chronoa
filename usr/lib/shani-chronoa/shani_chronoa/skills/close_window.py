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
from shani_chronoa.skills.list_windows import session_problem
from shani_chronoa.skills.window_atspi import (
    close_window_atspi,
    is_atspi_available,
)

_CONSENT_KEY = "window-close-enabled"
_TIMEOUT = 15

SCHEMA = {
    "type": "function",
    "function": {
        "name": "close_window",
        "description": (
            "Ask a window to close, the way its close button would - the "
            "application still gets the chance to ask about unsaved work. "
            "Requires the 'window-close-enabled' consent key. X11 and Wayland."
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

    # AT-SPI Wayland backend (portable across GNOME, Plasma, COSMIC)
    if is_atspi_available():
        wid = (arguments.get("window_id") or "").strip()
        needle = (arguments.get("title_contains") or "").strip()
        if wid:
            return f"AT-SPI does not support window_id; give a title_contains match instead"
        return close_window_atspi(title_contains=needle if needle else None)

    # X11 fallback via xdotool
    problem = session_problem()
    if problem:
        return f"Could not close a window: {problem}"

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


SKILLS = [Skill(name="close_window", schema=SCHEMA, run=_run)]
