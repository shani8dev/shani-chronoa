"""Moved to shani-link (shani_link/avrcp_target.py): device plumbing, not the assistant.
This name is an alias of that module, so everything here keeps working."""
import sys

from shani_chronoa import _link

sys.modules[__name__] = _link.module("avrcp_target")
