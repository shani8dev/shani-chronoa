"""A sense's reading as plain text, for the skills that answer a sense's question.

`snapshot_status`, `security_status`, `temperatures` and the rest each put a
sense's reading in front of a person who asked for it directly. They share that
sense's consent switch rather than minting their own - the precedent is
`git_inspect` sharing `git-sense-enabled`: the skill and the sense report the
same facts, and two switches would let an install ship one open and the other
shut, so a model could read through the skill what the person turned off in the
sense.

**The gate is checked in the calling skill, not here.** Each skill calls
`ChronoaConfig().sense_allowed(name)` itself and only then calls `reading()`, so
the check is visible in the skill's own source - which is what
`tests/test_skill_gates_are_enforced.py` reads. A gate hidden in a helper is a
gate that test cannot see, and the first version of that test's fix was a no-op
for exactly that reason.

Five of the wrapped senses also gate inside their own `_run` (security, boots,
storage, containers, hwmon); three do not (snapshots, usb, coredumps), because
the scheduler gates them. Checking in the skill makes all eight behave alike.
"""

from __future__ import annotations

import importlib


def reading(sense: str) -> str:
    """The sense's own text. Never raises: a broken sense reads as UNKNOWN.

    **The nose light lives here, because this is the one funnel every
    machine-state sense goes through** - power, thermal, storage, boots, network,
    snapshots. `nose` is "feeling the machine" in the taxonomy, and it was in
    `body.ORGANS` and in `ORGAN_ICONS` with **nothing ever lighting it**:
    measured, only `ears`, `eyes`, `mouth`, `skin`, `memory` and `hands` were
    ever passed to the body. So the strip showed the machine's own state as
    permanently idle while senses were being read.

    A *pulse* rather than a `use`/`done` pair: a sense reading is a single short
    act with no caller left holding the handle afterwards, and `egress.py` lights
    `skin` the same way for the same reason.
    """
    try:
        from shani_chronoa import body
        body.body.pulse("nose", f"reading {sense}"[:80], "")
    except Exception:                                   # noqa: BLE001
        pass
    try:
        module = importlib.import_module(f"shani_chronoa.senses.{sense}")
        result = module._run({})
    except Exception as exc:  # noqa: BLE001 - one half failing must not hide the other
        return f"The {sense} reading could not be taken ({exc}), so it is UNKNOWN."
    return result if isinstance(result, str) else result.content


def refusal(config, sense: str) -> str:
    """Why this part is not shown, naming the switch that would show it."""
    return (f"Not shown: {config.sense_allowed_reason(sense)}. This part reads the same "
            f"thing as the '{sense}' sense, so it follows that sense's switch "
            f"('{sense}-sense-enabled').")
