"""Compatibility shim: desktop_cue moved to shani_chronoa.ui_gtk.desktop_cue."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("shani_chronoa.ui_gtk.desktop_cue")
