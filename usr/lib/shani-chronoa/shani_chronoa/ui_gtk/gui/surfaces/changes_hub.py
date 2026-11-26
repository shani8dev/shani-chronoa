"""11f: Changes = Diff, Editor and Artifacts on one page.

"Files Chronoa changed" was three answers: the diff of a write, the editor
that could open it, and the artifacts it made. One tab strip keeps the three
views of the same question together without duplicating any of them.
"""

from __future__ import annotations
from shani_chronoa.ui_gtk.gui.locale import _

from typing import Any

from shani_chronoa.ui_gtk.gui.surfaces import hub

TITLE = _('Changes')

ICON = "document-revert-symbolic"
SECTION = "Making"
SUBTITLE = _('What Chronoa wrote and made: the diff of each change, an editor to open it in, and the files it produced.')



def build(app: Any):
    return hub.build_hub(
        app,
        TITLE,
        SUBTITLE,
        ICON,
        ("diff", "editor", "artifact_store"),
        SECTION,
        first="diff",
    )
