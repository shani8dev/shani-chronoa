"""Import shim: the implementation lives in ``shani_sysadmin.distill``.

The router-harness module moved out of Chronoa with the other sysadmin/ML
tooling. Chronoa still calls it from ``app/brain.py`` and ``tool_select.py``,
so this name remains. Assigning the app's module object keeps monkeypatching
and attribute reads working exactly as they did before the move.
"""

from __future__ import annotations

import sys

from shani_chronoa import _sysadmin_path  # noqa: F401
import shani_sysadmin  # noqa: F401
import shani_sysadmin.distill as _impl

sys.modules[__name__] = _impl
