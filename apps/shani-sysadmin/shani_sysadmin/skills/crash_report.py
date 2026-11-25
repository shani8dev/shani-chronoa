"""Skill: what crashed recently?

The `coredumps` sense reads `coredumpctl` - programs killed by a signal, which
the journal's error log does not necessarily contain. Background context only
until now; this answers the direct question, behind the sense's own switch
(`coredumps-sense-enabled`) - see `sense_reading.py`.

A machine with no `coredumpctl`, or with core dumps disabled, reports that as
itself - "nothing recorded" and "nothing crashed" are different claims, and the
sense already keeps them apart.
"""

from __future__ import annotations

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "crash_report",
        "description": (
            "Programs that crashed recently on this machine (core dumps recorded "
            "by systemd-coredump): which program, when, and the signal that "
            "killed it. Use for 'what crashed', 'why did that app close'. "
            "Uses the 'coredumps-sense-enabled' switch. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the coredumps sense's switch (see sense_reading.py)."""
    if config.sense_allowed("coredumps"):
        return True, ""
    return False, sense_reading.refusal(config, "coredumps")


def _run(_arguments: dict) -> str:
    config = ChronoaConfig()
    allowed, why = _consent(config)
    return sense_reading.reading("coredumps") if allowed else why


SKILLS = [Skill(name="crash_report", schema=SCHEMA, run=_run)]
