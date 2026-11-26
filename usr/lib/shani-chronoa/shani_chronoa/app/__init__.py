"""Compatibility shim: shani_chronoa.app moved to shani_chronoa.ui_gtk.app."""
from shani_chronoa.ui_gtk import app as _real
_g = globals()
_g.update({k: v for k, v in vars(_real).items() if not k.startswith("__")})
del _g, _real
