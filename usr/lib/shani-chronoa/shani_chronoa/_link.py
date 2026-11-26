"""Where the device engines live, whichever edition is installed.

Phone, watch, Bluetooth, landline, FM, NFC and infrared engines are not part
of the assistant; they ship in one of two packages:

- niri: shani-shell's ``phone`` package at ``/usr/lib/shani-shell/phone``.
  ``$SHANI_PHONE_PATH`` overrides that directory (it must *contain* ``phone/``).
  Its modules are top-level inside it: ``phone.moyoung``, ``phone.phone``, ...
- other editions: shani-link's ``shani_link`` package at ``/usr/lib/shani-link``,
  at ``$SHANI_LINK_PATH``, or beside this checkout (``../shani-link``). Its
  modules are ``shani_link.moyoung``, etc.

Providers are checked in that order. ``$SHANI_LINK_PROVIDER=phone|shani_link``
forces one when both are installed. Chronoa's old module names are aliases of
the chosen module - one module object, so code and tests patching
``shani_chronoa.moyoung`` patch the real thing. With no provider installed,
``module()`` raises ImportError, the contract callers already catch.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

_PHONE_DEFAULT = "/usr/lib/shani-shell/phone"
_LINK_DEFAULT = "/usr/lib/shani-link"

#: (package, directory containing it) for the chosen provider.
_cached_provider = None


def _phone_candidates():
    yield os.environ.get("SHANI_PHONE_PATH", "")
    yield _PHONE_DEFAULT


def _link_candidates():
    override = os.environ.get("SHANI_LINK_PATH")
    yield override or ""
    yield _LINK_DEFAULT
    # An explicit override means "use this one": falling back to the dev
    # checkout would silently ignore it when the override is wrong.
    if override is None:
        here = Path(__file__).resolve()
        # usr/lib/shani-chronoa/shani_chronoa/_link.py -> folder holding both checkouts
        if len(here.parents) > 5:
            yield str(here.parents[5] / "shani-link")


def _providers():
    """Installed providers as (package, path), in a deterministic order."""
    found = (
        (
            "phone",
            next(
                (
                    p
                    for p in _phone_candidates()
                    if p and os.path.isdir(os.path.join(p, "phone"))
                ),
                None,
            ),
        ),
        (
            "shani_link",
            next(
                (
                    p
                    for p in _link_candidates()
                    if p and os.path.isdir(os.path.join(p, "shani_link"))
                ),
                None,
            ),
        ),
    )
    installed = [(package, path) for package, path in found if path]
    forced = os.environ.get("SHANI_LINK_PROVIDER", "").strip()
    if forced:
        return [(package, path) for package, path in installed if package == forced]
    return installed


def _reset():
    """Forget the chosen provider, so the next call re-reads the environment."""
    global _cached_provider
    _cached_provider = None


def ensure_path() -> bool:
    """Put the first installed provider on sys.path. False when none is installed."""
    global _cached_provider
    providers = _providers()
    if not providers:
        return False
    package, path = providers[0]
    if path not in sys.path:
        sys.path.insert(0, path)
    _cached_provider = (package, path)
    return True


def module(name: str):
    """The engine module `name` (ImportError when no provider has it).

    A failed import of one provider is dropped, so it cannot poison a later
    call: the next provider in the same ordered list is then tried.
    """
    global _cached_provider
    providers = _providers()
    if _cached_provider in providers:
        providers = [_cached_provider] + [p for p in providers if p != _cached_provider]
    tried = []
    for package, path in providers:
        if path not in sys.path:
            sys.path.insert(0, path)
        fullname = package + "." + name
        tried.append(fullname)
        try:
            mod = importlib.import_module(fullname)
        except ImportError:
            # Drop a half-initialised package; it would fail every later call.
            sys.modules.pop(fullname, None)
            continue
        _cached_provider = (package, path)
        return mod
    _reset()
    raise ImportError("no provider has " + name + ": tried " + ", ".join(tried))
