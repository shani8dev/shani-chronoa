"""Make the app and Chronoa's shared library importable from a source checkout.

The skills that live here were moved out of Chronoa. They are still written
against Chronoa's plugin contract and still call Chronoa's shared library
modules (``files``, ``config``, ``sysfs``, ``sense_reading``, ``egress``,
``netprovision`` and ``skills.Skill``). Those modules deliberately stay in
Chronoa - they are not sysadmin-only, and moving them would fork the app.

So this module puts two directories on ``sys.path``:

* this app's root, so ``import shani_sysadmin`` works;
* ``usr/lib/shani-chronoa``, so ``import shani_chronoa`` works.

When the app is installed as a package, Chronoa is expected to be installed
too; the fallback is for running straight from a checkout, which is how the
tests and the CLI in this repository are used.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: shani_sysadmin/bootstrap.py -> shani_sysadmin/ -> shani-sysadmin/ -> apps/ -> repo/
APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent.parent
CHRONOA_LIB = REPO_ROOT / "usr" / "lib" / "shani-chronoa"


def install() -> tuple[Path, Path]:
    """Add both roots to sys.path, newest first, and return them."""
    for path in (CHRONOA_LIB, APP_ROOT):
        text = str(path)
        if path.is_dir() and text not in sys.path:
            sys.path.insert(0, text)
    return CHRONOA_LIB, APP_ROOT


install()
