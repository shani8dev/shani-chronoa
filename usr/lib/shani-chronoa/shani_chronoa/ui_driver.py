"""Compatibility shim: ui_driver moved to shani_chronoa.ui_gtk.ui_driver."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("shani_chronoa.ui_gtk.ui_driver")
