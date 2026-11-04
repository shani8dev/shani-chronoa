"""Where shani-link is, for the device engines that moved there.

The phone, watch, Bluetooth, landline, FM, NFC and infrared engines are
shani-link's now (desktop plumbing, not the assistant). Their old module names
here are aliases of the shani_link modules - one module object, so code and
tests that patch `shani_chronoa.moyoung` patch the real thing. shani-link is
found installed (/usr/lib/shani-link), at $SHANI_LINK_PATH, or beside this
checkout (../shani-link) while developing.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _candidates():
    yield os.environ.get("SHANI_LINK_PATH", "")
    yield "/usr/lib/shani-link"
    here = Path(__file__).resolve()
    # usr/lib/shani-chronoa/shani_chronoa/_link.py -> the folder holding both checkouts
    if len(here.parents) > 5:
        yield str(here.parents[5] / "shani-link")


def ensure_path() -> bool:
    for p in _candidates():
        if p and os.path.isdir(os.path.join(p, "shani_link")):
            if p not in sys.path:
                sys.path.insert(0, p)
            return True
    return False


def module(name: str):
    """The shani_link module `name` (ImportError when shani-link is not installed)."""
    ensure_path()
    return importlib.import_module("shani_link." + name)
