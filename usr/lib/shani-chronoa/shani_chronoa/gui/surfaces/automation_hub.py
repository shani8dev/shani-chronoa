"""11d: Automation = Routines, Rules, Goals and Background mode, one page.

They are one decision - what may Chronoa do with the window closed - split
across four panels. The background-mode switch is the header of that decision:
without it none of the three kinds of saved work can run unattended.
"""

from __future__ import annotations
from shani_chronoa.gui.locale import _

from typing import Any

from shani_chronoa.gui.surfaces import hub

TITLE = _('Automation')

ICON = "preferences-system-symbolic"
SECTION = "Acting"
SUBTITLE = _('Saved phrases, rules that fire on their own, plans, and the switch that lets any of them run with the window closed.')



def build(app: Any):
    return hub.build_hub(
        app,
        TITLE,
        SUBTITLE,
        ICON,
        ("daemon", "routines", "triggers", "goals"),
        SECTION,
        first="daemon",
    )
