"""Skill: report the current local date and time."""

from datetime import datetime

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_datetime",
        "description": "The current date and time here. For the time in another city or country, use get_world_time.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _run(_arguments: dict) -> str:
    return datetime.now().strftime("%A, %B %d, %Y %H:%M")


SKILLS = [Skill(name="get_datetime", schema=_SCHEMA, run=_run)]
