"""Compatibility shim: automation_overlay moved to shani_chronoa.ui_gtk.automation_overlay."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("shani_chronoa.ui_gtk.automation_overlay")
