"""Import shim: the implementation lives in ``shani_sysadmin.ml_harness.merge``.

Kept so external callers of ``shani_chronoa.merge`` do not break. Chronoa
itself no longer registers or drives the merge harness.
"""

from __future__ import annotations

import sys

from shani_chronoa import _sysadmin_path  # noqa: F401
try:
    import shani_sysadmin  # noqa: F401
    import shani_sysadmin.ml_harness.merge as _impl
except ImportError as exc:
    if not (getattr(exc, "name", "") or "").startswith("shani_sysadmin"):
        raise
    _impl = _sysadmin_path.missing_app(__name__)

sys.modules[__name__] = _impl
