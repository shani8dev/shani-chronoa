"""Built-in setpoints: proposals, never armed fixes.

A setpoint is a condition the machine should notice without being asked -
a disk nearly full, a unit that keeps failing, a battery wearing out. The
trigger engine already knows how to arm a rule and how to ask before it acts;
what was missing is a short list of *sensible defaults* a person can pick from
instead of writing a rule from scratch.

They are off by default in the strongest sense: this module never writes a
rule file, never enables one, and never calls an actuator. It returns plain
dicts a person reviews; `manage_triggers` is the only door that arms them, so
consent keys, the approval flow and the whitelist all still apply. A preset
whose own consent key is off is returned only as a description, not as an
armable rule.

No new consent keys are introduced here. Each preset names a key that already
exists - the sense it reads and the "notify" actuator - because a key no screen
shows is a dead switch.
"""

from __future__ import annotations

from typing import Optional

#: Presets are proposals. This constant exists so a caller cannot mistake the
#: presence of a preset for consent to arm it.
ENABLED_BY_DEFAULT = False

PRESETS = (
    {
        "name": "disk-nearly-full",
        "description": "Notice when a filesystem has little room left.",
        "kind": "percept",
        "sense": "storage",
        "match_mode": "keywords",
        "keywords": ["full", "low space", "almost full"],
        "actuator": "notify",
        "arguments": {"title": "Disk space is low"},
        "consent_key": "storage-sense-enabled",
    },
    {
        "name": "overheating",
        "description": "Notice sustained high temperatures.",
        "kind": "percept",
        "sense": "hwmon",
        "match_mode": "keywords",
        "keywords": ["hot", "overheat", "critical temperature"],
        "actuator": "notify",
        "arguments": {"title": "This machine is running hot"},
        "consent_key": "hwmon-sense-enabled",
    },
    {
        "name": "failed-unit",
        "description": "Notice a service unit that has failed.",
        "kind": "event",
        "event_type": "unithealth",
        "source": "failed",
        "match_mode": "substring",
        "substring": "failed",
        "actuator": "notify",
        "arguments": {"title": "A service has failed"},
        "consent_key": "unithealth-sense-enabled",
    },
    {
        "name": "battery-wear",
        "description": "Notice a battery whose health has dropped.",
        "kind": "percept",
        "sense": "power",
        "match_mode": "keywords",
        "keywords": ["wear", "health", "degraded"],
        "actuator": "notify",
        "arguments": {"title": "Battery health has changed"},
        "consent_key": "power-sense-enabled",
    },
)

_BY_NAME = {p["name"]: p for p in PRESETS}


def proposals(config=None) -> list[dict]:
    """Copies of every preset, all explicitly not enabled.

    A copy, never the module's dict: a caller that edits one for display must
    not silently change what a later caller would arm.
    """
    return [
        {
            **p,
            "enabled": ENABLED_BY_DEFAULT,
            "arguments": dict(p["arguments"]),
            "keywords": list(p.get("keywords", ())),
        }
        for p in PRESETS
    ]


def describe(name: str, config=None) -> str:
    """One sentence a person can read, or a refusal naming the presets."""
    preset = _BY_NAME.get(name)
    if preset is None:
        return f"there is no setpoint named {name!r}; the presets are " + ", ".join(
            sorted(_BY_NAME)
        )
    gate = "off" if config is None else ("on" if _permitted(config, preset) else "off")
    return (
        f"{preset['description']} This is a proposal, not an armed rule - "
        f"its switch ({preset['consent_key']}) is {gate} and nothing here "
        "enables it."
    )


def _permitted(config, preset: dict) -> bool:
    key = preset["consent_key"]
    getter = getattr(config, "get_bool", None)
    return bool(getter(key, False)) if getter is not None else False


def armable(name: str, config=None) -> Optional[dict]:
    """A rule-shaped dict for `manage_triggers`, or None if not yet allowed.

    Still not armed: this hands the caller the *arguments* it would pass to the
    existing arming skill. Refusing an off consent key here means a preset
    cannot become a way to fire a sense the person switched off.
    """
    preset = _BY_NAME.get(name)
    if preset is None or config is None or not _permitted(config, preset):
        return None
    rule = {
        "name": preset["name"],
        "kind": preset["kind"],
        "actuator": preset["actuator"],
        "arguments": dict(preset["arguments"]),
        "match_mode": preset["match_mode"],
        "ask_first": True,
    }
    if preset["kind"] == "event":
        rule["event_type"] = preset["event_type"]
        rule["source"] = preset["source"]
        rule["substring"] = preset.get("substring", "")
    else:
        rule["sense"] = preset["sense"]
        rule["keywords"] = list(preset.get("keywords", ()))
    return rule
