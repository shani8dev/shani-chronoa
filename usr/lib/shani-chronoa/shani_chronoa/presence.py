"""How much of the machine Chronoa is holding on to: Active, Drowsy, Sleeping.

`assistd` has three states and one key cycles them: **Active** (llama-server
running with weights loaded), **Drowsy** (server up, weights unloaded), and
**Sleeping** (server stopped). The point is that a laptop should not be holding a
model resident from login until shutdown.

**Chronoa could not stop it at all.** `local_llm` had `start_service()` and no
`stop_service()`, and setup ran `systemctl --user enable --now`, so from the first
login the unit came up on its own and stayed up. There was no way to release it
and no way to say it had been released.

So Drowsy here does **not** claim to unload weights, because nothing here can.
It is: the assistant is listening, the model is not resident, and the next
question brings it back cold. Sleeping is the same but it waits to be asked -
which is the distinction a person actually wants between "let go of the memory
for now" and "leave the machine alone until I touch it".

That honesty matters more than the naming. A state called "drowsy" that quietly
meant "still fully loaded" would be the exact failure this repository documents:
a plausible-looking state that reports something other than what is true. The
tests below pin the wording, because the wording is the claim.
"""

from __future__ import annotations

import enum
import logging
from typing import Callable

logger = logging.getLogger(__name__)

#: How long a cold start is allowed to take before the caller is told it is slow
#: rather than left waiting. A 0.6 GB model loads in seconds; a 4 GB one on a
#: spinning disk can take a while, and silence is the worst possible answer.
COLD_START_HINT_SECONDS = 8.0


class Presence(enum.Enum):
    ACTIVE = "active"
    DROWSY = "drowsy"
    SLEEPING = "sleeping"

    def next(self) -> "Presence":
        """The next state in the manual cycle, as `assistd` orders it."""
        return {Presence.ACTIVE: Presence.DROWSY,
                Presence.DROWSY: Presence.SLEEPING,
                Presence.SLEEPING: Presence.ACTIVE}[self]

    def label(self) -> str:
        return {Presence.ACTIVE: "Ready",
                Presence.DROWSY: "Letting go of the model",
                Presence.SLEEPING: "Asleep"}[self]

    def detail(self) -> str:
        """What this state means, in a sentence a person can act on."""
        return {
            Presence.ACTIVE:
                "The model is loaded and answering. It costs about as much "
                "memory as the model is large.",
            Presence.DROWSY:
                "The assistant is listening but the model is not loaded. The "
                "next question loads it again, which takes a moment.",
            Presence.SLEEPING:
                "Nothing is loaded. Chronoa stays quiet until you ask it "
                "something.",
        }[self]

    def action(self) -> str:
        """The verb on the button that moves to the next state."""
        return {Presence.ACTIVE: "Free the model",
                Presence.DROWSY: "Go to sleep",
                Presence.SLEEPING: "Wake up"}[self]


def detect(is_up: Callable[[], bool]) -> Presence:
    """The state the machine is actually in.

    Read from whether the server answers, not from a remembered flag: a presence
    that disagrees with the machine is worse than no presence, because it reports
    memory held that has been released.
    """
    return Presence.ACTIVE if is_up() else Presence.DROWSY


def apply(target: Presence,
          *,
          is_up: Callable[[], bool],
          wake: Callable[[], str],
          sleep: Callable[[], str]) -> tuple:
    """Move to `target`. Returns `(reached, reason)`.

    `reached` is False when the machine did not do what was asked, and the reason
    says why - which is the case that matters, because "Drowsy" is a claim about
    memory and an unmet claim must not render as a met one.
    """
    now = detect(is_up)
    if target is Presence.ACTIVE:
        if now is Presence.ACTIVE:
            return True, "already loaded"
        problem = wake()
        if problem:
            logger.warning("presence: could not wake: %s", problem)
            return False, problem
        return True, "loaded"
    # Both other states mean "not resident". Stopping is the only lever here.
    if now is not Presence.ACTIVE:
        return True, "already not resident"
    problem = sleep()
    if problem:
        logger.warning("presence: could not release the model: %s", problem)
        return False, problem
    return True, "released"