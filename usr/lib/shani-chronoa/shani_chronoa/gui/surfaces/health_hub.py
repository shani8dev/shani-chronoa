"""11g: Health = Diagnostics, Desktop, Machine and What's running.

Four panels answered one question - "is this machine all right" - and each
held a piece: the diagnostics, the desktop session, the hardware readings, and
the processes Chronoa left running. Plain status per row and a Details
expander stay in the member panels; the hub is one place to look.
"""

from __future__ import annotations
from shani_chronoa.gui.locale import _

from typing import Any

from shani_chronoa.gui.surfaces import hub

TITLE = _('Health')

ICON = "battery-good-symbolic"
SECTION = "Health and trust"
SUBTITLE = _('Whether Chronoa can see and drive this desktop, what the machine is reporting, and what it left running.')



def build(app: Any):
    return hub.build_hub(
        app,
        TITLE,
        SUBTITLE,
        ICON,
        ("diagnostics", "desktop", "machine", "processes"),
        SECTION,
        first="diagnostics",
    )
