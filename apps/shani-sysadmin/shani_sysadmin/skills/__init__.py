"""The sysadmin skills, loaded from this app rather than from Chronoa.

Each module is unchanged in shape: it declares ``SKILLS`` as a list of
``shani_chronoa.skills.Skill`` entries. Reusing that contract is deliberate -
a second plugin format would be a second thing to keep correct.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from shani_chronoa.skills import Skill  # noqa: F401 - the shared contract

_MODULES_DIR = Path(__file__).resolve().parent


def discover_skills() -> tuple[list[dict], dict[str, object]]:
    """Import every skill module here and return ``(schemas, handlers)``."""
    schemas: list[dict] = []
    handlers: dict[str, object] = {}
    for path in sorted(_MODULES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = importlib.import_module(f"{__package__}.{path.stem}")
        for skill in getattr(module, "SKILLS", ()):
            if not isinstance(skill, Skill):
                continue
            schemas.append(skill.schema)
            handlers[skill.name] = skill.run
    return schemas, handlers
