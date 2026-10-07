"""Skill: how hot is this machine, and how fast are the fans?

The `hwmon` sense reads `/sys/class/hwmon` and the thermal zones directly - no
`lm_sensors` needed, so there is no binary to be missing. It was background
context only; this lets a person ask "is my laptop overheating" and get the
same reading on demand, behind the sense's own switch (`hwmon-sense-enabled`,
off by default) - see `sense_reading.py` for why the skill shares it.
"""

from __future__ import annotations

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "temperatures",
        "description": (
            "Current CPU, GPU, disk and board temperatures, fan speeds and fan "
            "control, read from the kernel's hardware sensors. Use for 'is my "
            "laptop overheating', 'how hot is the CPU', 'how fast is the fan'. "
            "Uses the 'hwmon-sense-enabled' switch. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the hwmon sense's switch (see sense_reading.py)."""
    if config.sense_allowed("hwmon"):
        return True, ""
    return False, sense_reading.refusal(config, "hwmon")


def _run(_arguments: dict) -> str:
    config = ChronoaConfig()
    allowed, why = _consent(config)
    return sense_reading.reading("hwmon") if allowed else why


SKILLS = [Skill(name="temperatures", schema=SCHEMA, run=_run)]
