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
from types import ModuleType

#: _sysadmin_path.py -> shani_chronoa -> shani-chronoa -> lib -> usr -> repo
REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT = REPO_ROOT / "apps" / "shani-sysadmin"


def install() -> Path:
    """Put the app root on sys.path and return it."""
    text = str(APP_ROOT)
    if APP_ROOT.is_dir() and text not in sys.path:
        sys.path.insert(0, text)
    return APP_ROOT


class SysadminNotInstalled(RuntimeError):
    """A caller needs a shani-sysadmin function and the app is not installed."""


def missing_app(name: str, **degrade: object) -> ModuleType:
    """A stand-in module for a shim when the app is absent.

    Import must keep working on a Chronoa-only install: the callers degrade
    when there is nothing to run (see ``tool_select`` and ``models.py``).
    Functions named in ``degrade`` answer with the benign value a caller
    expects; any other attribute raises a named error instead of an
    ``AttributeError`` nobody can act on.
    """
    stub = ModuleType(name)
    stub.SYSADMIN_MISSING = True

    def _missing(attr: str):
        def _call(*_args, **_kwargs):
            raise SysadminNotInstalled(
                f"{name}.{attr} needs the shani-sysadmin app, which is not "
                "installed here"
            )

        return _call

    def __getattr__(attr: str):
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        return _missing(attr)

    for key, value in degrade.items():
        setattr(stub, key, value)
    stub.__getattr__ = __getattr__  # type: ignore[attr-defined]
    return stub


install()
