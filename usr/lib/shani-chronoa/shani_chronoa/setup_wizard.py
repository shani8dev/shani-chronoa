"""Compatibility shim: setup_wizard moved to shani_chronoa.ui_gtk.setup_wizard."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("shani_chronoa.ui_gtk.setup_wizard")
