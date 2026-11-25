"""Find the shani-sysadmin app that lives beside this source checkout.

The ML harnesses moved to ``apps/shani-sysadmin``. Chronoa still has a few
callers of two of them (``distill`` from the brain and the selector,
``model_registry`` from the Models panel), so those two keep an import name
here as a *shim* that resolves to the app's module object. This module is the
one place that knows where the app lives.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: _sysadmin_path.py -> shani_chronoa -> shani-chronoa -> lib -> usr -> repo
REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT = REPO_ROOT / "apps" / "shani-sysadmin"


def install() -> Path:
    """Put the app root on sys.path and return it."""
    text = str(APP_ROOT)
    if APP_ROOT.is_dir() and text not in sys.path:
        sys.path.insert(0, text)
    return APP_ROOT


install()
