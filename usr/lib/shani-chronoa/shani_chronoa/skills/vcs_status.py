"""Library shim: the ``vcs_status`` skill moved to shani-sysadmin.

The Work chip and the Git panel still read ``git_state(folder)`` from this
name, so the helper stays reachable without the tool being offered to the
model. ``_CHRONOA_NOT_A_SKILL`` keeps the loader from registering the app's
schema here; the implementation itself lives in the app.
"""

from __future__ import annotations

from shani_chronoa import _sysadmin_path  # noqa: F401 - installs the app path

_CHRONOA_NOT_A_SKILL = True

try:
    from shani_sysadmin.skills.vcs_status import git_state
except ImportError as exc:
    if not (getattr(exc, "name", "") or "").startswith("shani_sysadmin"):
        raise

    def git_state(folder):  # type: ignore[misc]
        """No app: the chip and panel already treat an unreadable state as empty."""
        return "", 0
