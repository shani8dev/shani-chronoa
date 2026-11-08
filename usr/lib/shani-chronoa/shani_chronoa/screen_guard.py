"""Applications that must never end up in a screenshot.

**Why this is a refusal and not a redaction.** On Windows, Signal had to add
screen-security DRM after Recall shipped: the only reliable way to keep a
password manager out of an ambient capture was for the *capture* to be unable
to see it. A Wayland client cannot read another client's pixels, so the
equivalent here is a guard in front of the one capture path Chronoa has -
`capture_screen()` - that refuses while an excluded application is on screen.

Refusing the whole screen rather than blanking a rectangle is deliberate. The
geometry a compositor reports is not the geometry a capture contains (scaling,
fractional scale, workspace offsets), and a redaction computed from the wrong
rectangle is worse than no redaction because it *looks* applied. A refusal is a
fact a person can check: close the app, take the screenshot.

**Fail closed, and say which half is missing.** With exclusions configured and
no way to ask the desktop what is on screen, the honest answer is "not
captured", not "probably fine". The message names the setting and the reason,
so the person can remove the exclusion or use a desktop that can be asked.
With no exclusions configured this module costs nothing and calls nothing -
which is every machine that never turns it on.

**Detection is per compositor, and the list is the source of truth.**
- niri: `niri msg --json windows` (and `workspaces`, so a window on another
  workspace is not treated as visible). This is the flagship profile.
- X11: `wmctrl -lx`, with the desktop column matched against `wmctrl -d`.
- Anything else: unknown, and honest about it.

The match is on an application id (`org.keepassxc.KeePassXC`, `firefox`), never
on a window title: titles change with the document, and a guard that stops
working when a page is renamed is a guard that lies.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Comma-separated application ids, in the user's own settings.
SETTING = "screen-capture-excluded-apps"

_QUERY_TIMEOUT = 3

Window = Tuple[str, str]  # (application id or class, title)


def excluded(config=None) -> "list[str]":
    """The configured application ids, normalised for comparison."""
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    try:
        raw = config.get(SETTING, "")
    except Exception:  # noqa: BLE001 - a broken store must not look like "no exclusions"
        logger.warning("could not read %s", SETTING, exc_info=True)
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _run(argv: "list[str]") -> "Optional[str]":
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_QUERY_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def _niri_windows() -> "Optional[list[Window]]":
    """Windows on the active workspace, or None when niri is not the desktop."""
    exe = shutil.which("niri")
    if exe is None:
        return None
    raw = _run([exe, "msg", "--json", "windows"])
    if raw is None:
        return None
    try:
        windows = json.loads(raw)
        spaces = json.loads(_run([exe, "msg", "--json", "workspaces"]) or "[]")
    except (ValueError, TypeError):
        return None
    if not isinstance(windows, list):
        return None
    active = {space.get("id") for space in spaces
              if isinstance(space, dict) and space.get("is_active")}
    found: "list[Window]" = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        # A window on another workspace is not on screen. When the workspace
        # list cannot be read, every window is considered - the strict reading.
        if active and window.get("workspace_id") not in active:
            continue
        found.append((str(window.get("app_id") or ""), str(window.get("title") or "")))
    return found


def _wmctrl_windows() -> "Optional[list[Window]]":
    """Windows on the current X11 desktop, or None when wmctrl is absent."""
    exe = shutil.which("wmctrl")
    if exe is None:
        return None
    listing = _run([exe, "-lx"])
    if listing is None:
        return None
    current = None
    desktops = _run([exe, "-d"])
    if desktops:
        for line in desktops.splitlines():
            fields = line.split()
            if "*" in fields:
                try:
                    current = fields[0]
                except IndexError:
                    current = None
                break
    found: "list[Window]" = []
    for line in listing.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        _id, desktop, wm_class = parts[0], parts[1], parts[2]
        if current is not None and desktop != current:
            continue
        title = parts[4] if len(parts) > 4 else ""
        found.append((wm_class, title))
    return found


def visible_windows() -> "Optional[list[Window]]":
    """What the desktop reports as on screen, or None when it cannot be asked."""
    for probe in (_niri_windows, _wmctrl_windows):
        found = probe()
        if found is not None:
            return found
    return None


def _matches(application: str, excluded_ids: "list[str]") -> bool:
    """Whether `application` names one of the excluded apps.

    `wmctrl` reports `instance.Class` (e.g. `Navigator.firefox`), niri reports a
    desktop id (`org.keepassxc.KeePassXC`). Either component may be what the
    person wrote, so all three spellings are compared, case-insensitively.
    """
    names = {application.casefold()}
    names.update(part.casefold() for part in application.split(".") if part)
    return any(want.casefold() in names for want in excluded_ids)


def _refuse(message: str) -> None:
    # Imported here: `screengrab` imports this module at module scope, and a
    # module-level import back would be a cycle.
    from shani_chronoa.screengrab import ScreenCaptureError
    raise ScreenCaptureError(message)


def check(config=None) -> None:
    """Refuse a screen capture while an excluded application is on screen.

    Raises `ScreenCaptureError` with a sentence meant for a person. Returns
    quietly when no exclusions are configured - the common case, and the one
    that must not pay for a subprocess on every screenshot.
    """
    wanted = excluded(config)
    if not wanted:
        return
    windows = visible_windows()
    if windows is None:
        _refuse(
            "not captured: screen-capture exclusions are set, but this desktop "
            "cannot be asked which applications are on screen. Remove the "
            "exclusions in Settings > Privacy ("
            f"{SETTING}), or use niri or an X11 session where they can be "
            "checked."
        )
    for application, title in windows:
        if _matches(application, wanted):
            named = application or title or "an application"
            _refuse(
                f"not captured: {named} is on screen and is excluded from screen "
                f"capture. Close it or switch to another workspace, then try again."
            )
