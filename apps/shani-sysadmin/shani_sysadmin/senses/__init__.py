"""Senses moved with the sysadmin tooling.

``kernellog`` needs root or a group membership and reads the kernel ring
buffer. It stays a real sense with Chronoa's sense contract; it is simply no
longer polled by Chronoa's ambient scheduler.
"""

from __future__ import annotations

import importlib
from pathlib import Path

_MODULES_DIR = Path(__file__).resolve().parent


def discover_senses() -> dict:
    """Return ``{sense name: Sense}`` for the senses shipped here."""
    found: dict = {}
    for path in sorted(_MODULES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = importlib.import_module(f"{__package__}.{path.stem}")
        for sense in getattr(module, "SENSES", ()):
            found[sense.name] = sense
    return found
