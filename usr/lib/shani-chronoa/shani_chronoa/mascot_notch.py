"""Compatibility shim: mascot_notch moved to shani_chronoa.ui_gtk.mascot_notch."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("shani_chronoa.ui_gtk.mascot_notch")
