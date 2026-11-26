"""Compatibility shim: shani_chronoa.gui.surfaces moved to shani_chronoa.ui_gtk.gui.surfaces."""
from shani_chronoa.ui_gtk import gui as _real
from shani_chronoa.ui_gtk.gui import surfaces as _pkg
_g = globals()
_g.update({k: v for k, v in vars(_pkg).items() if not k.startswith("__")})
del _g, _pkg, _real
