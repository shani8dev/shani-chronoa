"""Compatibility shim: shani_chronoa.windows moved to shani_chronoa.ui_gtk.windows."""
from shani_chronoa.ui_gtk import windows as _real
_g = globals()
_g.update({k: v for k, v in vars(_real).items() if not k.startswith("__")})
del _g, _real
