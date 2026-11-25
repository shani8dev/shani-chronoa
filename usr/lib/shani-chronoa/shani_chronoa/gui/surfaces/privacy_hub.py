"""11e: Privacy & permissions = Privacy, Senses and the consent table.

"May it read, do, send" was three answers: the Privacy panel's switches, the
Senses panel's per-sense gates, and Settings > Privacy's full consent table.
One hub holds the three without re-drawing any of them.
"""

from __future__ import annotations

from typing import Any

from shani_chronoa.gui.surfaces import hub

TITLE = "Privacy & permissions"
ICON = "system-search-symbolic"
SECTION = "Health and trust"
SUBTITLE = (
    "What Chronoa may read, do and send: the switches, the senses and "
    "the full consent table, in one place."
)


def build(app: Any):
    return hub.build_hub(
        app, TITLE, SUBTITLE, ICON, ("privacy", "senses"), SECTION, first="privacy"
    )
