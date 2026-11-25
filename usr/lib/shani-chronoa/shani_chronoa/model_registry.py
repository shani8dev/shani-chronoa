"""Import shim: the implementation lives in ``shani_sysadmin.model_registry``.

``gui/surfaces/models.py`` still calls ``active_entry()``, so the Chronoa
import name remains. See ``apps/shani-sysadmin/README.md`` for the split.
"""

from __future__ import annotations

import sys

from shani_chronoa import _sysadmin_path  # noqa: F401
import shani_sysadmin  # noqa: F401
import shani_sysadmin.model_registry as _impl

sys.modules[__name__] = _impl
