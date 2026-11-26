"""Compatibility shim: this module moved to shani_chronoa.ui_gtk.gui.surfaces.today."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("shani_chronoa.ui_gtk.gui.surfaces.today")
