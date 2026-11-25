"""Test setup for the standalone app.

The moved skills import Chronoa's shared library, so both roots must be on
``sys.path``. Their tests were also written against the repo suite's hermetic
environment (compiled schema, isolated HOME/XDG dirs, consent fixtures), so
register that conftest as a plugin rather than copying its fixtures here.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent.parent
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(REPO_ROOT / "usr/lib/shani-chronoa"))

# Share the canonical suite's fixtures: the moved tests still use
# `gsettings_env`, `stubbed_app`, and the autouse environment isolation.
_root_conftest = REPO_ROOT / "tests" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_shani_root_conftest", _root_conftest)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)

pytest_plugins = ["_shani_root_conftest"]
