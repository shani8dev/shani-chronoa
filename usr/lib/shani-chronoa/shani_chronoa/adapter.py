"""Import shim: the implementation lives in ``shani_sysadmin.ml_harness.adapter``.

Kept so external callers of ``shani_chronoa.adapter`` do not break.
"""

from __future__ import annotations

import sys

from shani_chronoa import _sysadmin_path  # noqa: F401
import shani_sysadmin  # noqa: F401
import shani_sysadmin.ml_harness.adapter as _impl

sys.modules[__name__] = _impl
