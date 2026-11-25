"""Import shim: the implementation lives in ``shani_sysadmin.ml_harness.merge``.

Kept so external callers of ``shani_chronoa.merge`` do not break. Chronoa
itself no longer registers or drives the merge harness.
"""

from __future__ import annotations

import sys

from shani_chronoa import _sysadmin_path  # noqa: F401
import shani_sysadmin  # noqa: F401
import shani_sysadmin.ml_harness.merge as _impl

sys.modules[__name__] = _impl
