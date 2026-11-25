"""Import shim: the implementation lives in ``shani_sysadmin.ml_harness.model_registry``.

``gui/surfaces/models.py`` still calls ``active_entry()``, so the Chronoa
import name remains. See ``apps/shani-sysadmin/README.md`` for the split.
"""

from __future__ import annotations

import sys

from shani_chronoa import _sysadmin_path  # noqa: F401
try:
    import shani_sysadmin  # noqa: F401
    import shani_sysadmin.ml_harness.model_registry as _impl
except ImportError as exc:
    if not (getattr(exc, "name", "") or "").startswith("shani_sysadmin"):
        raise
    # No app: an unreadable registry is its own row in the Models panel.
    _impl = _sysadmin_path.missing_app(
        __name__,
        active_entry=lambda *a, **k: None,
        scan=lambda *a, **k: {"models": [], "excluded": [], "unreadable": []},
    )

sys.modules[__name__] = _impl
