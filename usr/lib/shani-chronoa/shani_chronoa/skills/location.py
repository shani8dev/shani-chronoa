"""Skill: where is this computer? The location sense, as a tool the model can call.

The sense (senses/location.py) does the work and owns the consent gate:
`location-sense-enabled`, off by default, and blocked in privacy mode. This
is the tool-call surface over it, as web_search is over the web sense.
"""

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_location",
        "description": (
            "Where this computer is right now - latitude, longitude and accuracy - "
            "from a GPS receiver or GNOME's location service. Use for 'where am I'. "
            "Requires the location sense to be enabled and privacy mode to be off."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _run(_arguments: dict) -> str:
    from shani_chronoa.senses import location as sense
    result = sense._run({})
    return result if isinstance(result, str) else result.content


SKILLS = [Skill(name="get_location", schema=_SCHEMA, run=_run)]
