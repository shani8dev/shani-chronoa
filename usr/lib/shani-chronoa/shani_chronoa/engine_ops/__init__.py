"""GTK-free operations behind the Agent surface.

Every function here takes plain data and returns plain data (dicts/lists of
stable keys, never widgets). The GTK app, the shell's panel, the terminal
client and the command line all call the same function; none of them holds a
second copy of the decision. See `docs/ENGINE-SURFACE.md` for the map and the
consent rule that every write obeys.

The submodules are re-exported so the Agent's one wire adapter can name them
as `engine_ops.settings`, `engine_ops.models`, and so on.
"""

from shani_chronoa.engine_ops import (
    automation,
    conversations,
    health,
    memory,
    models,
    settings,
    setup,
    voice,
)

__all__ = [
    "automation",
    "conversations",
    "health",
    "memory",
    "models",
    "settings",
    "setup",
    "voice",
]
