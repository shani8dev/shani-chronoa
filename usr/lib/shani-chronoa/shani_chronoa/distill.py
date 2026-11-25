"""Import shim: the implementation lives in ``shani_sysadmin.distill``.

The router-harness module moved out of Chronoa with the other sysadmin/ML
tooling. Chronoa still calls it from ``app/brain.py`` and ``tool_select.py``,
so this name remains. Assigning the app's module object keeps monkeypatching
and attribute reads working exactly as they did before the move.
"""

from __future__ import annotations

import sys

from shani_chronoa import _sysadmin_path  # noqa: F401

try:
    import shani_sysadmin  # noqa: F401
    import shani_sysadmin.distill as _impl
except ImportError as exc:
    if not (getattr(exc, "name", "") or "").startswith("shani_sysadmin"):
        raise
    # No app: every caller degrades - no router, no rows, no turn carried.
    _impl = _sysadmin_path.missing_app(
        __name__,
        fallback=lambda request, margin=2.0: None,
        load_router=lambda *a, **k: None,
        harvest_rows=lambda **k: [],
        teacher_notes=lambda *a, **k: [],
        train_router=lambda *a, **k: {
            "saved": False,
            "reason": "the shani-sysadmin app is not installed here",
        },
        _cloud_allowed=lambda config: (
            False,
            "the shani-sysadmin app is not installed here",
        ),
        json=__import__("json"),
    )

sys.modules[__name__] = _impl
