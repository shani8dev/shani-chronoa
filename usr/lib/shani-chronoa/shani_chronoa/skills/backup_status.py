"""Library shim: the ``backup_status`` skill moved to shani-sysadmin.

``backup_adapters`` still delegates its restic ``check`` action to this name,
so the helper stays reachable without the tool being offered to the model.
``_CHRONOA_NOT_A_SKILL`` keeps the loader from registering the app's schema
here; the implementation itself lives in the app.
"""

from __future__ import annotations

from shani_chronoa import _sysadmin_path  # noqa: F401 - installs the app path

_CHRONOA_NOT_A_SKILL = True

try:
    from shani_sysadmin.skills.backup_status import _run
except ImportError as exc:
    if not (getattr(exc, "name", "") or "").startswith("shani_sysadmin"):
        raise

    def _run(arguments):  # type: ignore[misc]
        """No app: the adapter reports the missing dependency instead."""
        return "This needs the shani-sysadmin app, which is not installed here."
