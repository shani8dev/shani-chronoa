"""11c: Models = what answers now on top, the catalogue under it.

The two were separate sidebar rows in different sections - "model" answered
what is answering, "models" listed what is installed or downloadable - so the
first question a person has was two clicks away from the second.
"""

from __future__ import annotations

from typing import Any

from shani_chronoa.gui.surfaces import hub

TITLE = "Models"
ICON = "document-properties-symbolic"
SECTION = "Thinking"
SUBTITLE = (
    "What answers now and what is installed: the live answer first, "
    "the catalogue underneath."
)


def build(app: Any):
    return hub.build_hub(
        app, TITLE, SUBTITLE, ICON, ("model", "models"), SECTION, first="model"
    )
