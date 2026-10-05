"""The running bandit over tool names.

Merged into `learning.py`; kept as a re-export so existing imports work.

**No bare `load`/`save` aliases.** They were here once, and they collide with the
outcome model's own loaders in the same module - so one of those imports meant the
bandit while the other meant the model, and a caller who imported both got
whichever the wildcard won. The names are `load_bandit` and `save_bandit` in both
modules, which cannot be confused.
"""

from shani_chronoa.learning import (
    Arm,
    Bandit,
    Choice,
    REWARD,
    load_bandit,
    render_bandit,
    save_bandit,
)

__all__ = ["Arm", "Bandit", "Choice", "REWARD", "load_bandit", "render_bandit",
           "save_bandit"]
