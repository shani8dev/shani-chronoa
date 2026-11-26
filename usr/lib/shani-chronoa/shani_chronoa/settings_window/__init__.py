"""Compatibility shim: shani_chronoa.settings_window moved to shani_chronoa.ui_gtk.settings_window."""

from importlib import import_module

_real = import_module("shani_chronoa.ui_gtk.settings_window")
_g = globals()
_g.update({k: v for k, v in vars(_real).items() if not k.startswith("__")})
del _g, _real
