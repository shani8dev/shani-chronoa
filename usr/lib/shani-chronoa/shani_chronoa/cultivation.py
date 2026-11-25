"""Import shim: the implementation lives in ``shani_sysadmin.cultivation``.

Kept so external callers of ``shani_chronoa.cultivation`` do not break.
"""

from __future__ import annotations

import sys

from shani_chronoa import _sysadmin_path  # noqa: F401
import shani_sysadmin  # noqa: F401
import shani_sysadmin.cultivation as _impl

sys.modules[__name__] = _impl
