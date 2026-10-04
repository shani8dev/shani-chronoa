"""Which desktop this session is, and GSettings - shared by every appearance and session skill.

Five skills had the same `_desktop()` and four the same `_gs()`;
`desktop_setting` had a third spelling that answered "plasma" where the others
answered "kde". One answer now: "gnome", "kde" or "unknown", read from the
session's own variables rather than guessed from what is installed.
"""

from __future__ import annotations

import os

from shani_chronoa import subproc


def kind() -> str:
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_DESKTOP"):
        value = (os.environ.get(var) or "").lower()
        if "kde" in value or "plasma" in value:
            return "kde"
        if "gnome" in value or "unity" in value or "cinnamon" in value:
            return "gnome"
    return "unknown"


def gsettings(*args: str, timeout: float = 20):
    """`gsettings <args>` as a completed process, or None when gsettings cannot run."""
    return subproc.run(["gsettings", *args], timeout=timeout)
