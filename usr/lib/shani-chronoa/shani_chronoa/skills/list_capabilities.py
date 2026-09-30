"""Skill: what can you do right now, without asking me.

The question a user asks when they are unsure whether a refusal is a broken
assistant or a switch they have not turned on. Answering it from a hand-written
list would be the exact failure `capabilities.py`'s own docstring names: a
static list is a snapshot of the app on the day someone typed it, and it goes
on advertising a skill whose consent key is off, or one that failed to load,
with no way for the caller to tell which.

**So every line here is read live, at call time.** The tool list comes from
`discover_skills()`, the same registry the LLM is handed, so a skill that did
not load has no line. The consent state comes from `ChronoaConfig().get_bool`
against each tool's real key, so a line says *open* or *shut* for right now,
not for the day the schema was written.

**The three states are three states.** A gated tool is `open`, `shut`, or
`unknown` - and unknown is rendered as unknown. `get_bool` returns the caller's
default for a key the running schema does not declare, which is indistinguishable
from a key that is genuinely off unless it is said out loud, so a key missing
from this build is reported as such rather than as "shut". An older install
whose schema predates a skill's key would otherwise be told, confidently, that
the permission it does not have is one the user chose to withhold.

**Gated tools are listed with their human-readable gate name, not the
gsettings key.** `input-control-enabled` means nothing to someone who has
never opened a terminal, and the whole point of this skill is to be readable by
the person asking.

Deliberately not gated and deliberately not a settings editor: this reports
state, it never changes it. A skill that could turn its own permissions on would
make the consent keys advisory.
"""

from __future__ import annotations

from shani_chronoa import capabilities
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill, discover_skills

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_capabilities",
        "description": (
            "Report what this assistant can do right now, and which of those "
            "need a setting switched on first. Reads the live skill registry "
            "and the live consent values rather than a fixed list, so a skill "
            "that is not installed, or a permission that is off, is shown as "
            "such. Changes nothing."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_OPEN = "open"
_SHUT = "shut"
_UNKNOWN = "unknown - this build's schema does not declare the key"


def _gate_state(config: ChronoaConfig, key: str) -> str:
    """`open`, `shut`, or an explicit unknown - never a guess between them.

    `_valid_keys` is the running schema's own key list, so a key this build
    does not have is distinguishable from one the user set to false. Without
    that distinction an install whose schema predates a skill reports the
    missing permission as a deliberate refusal, which is the one answer here
    that would be confidently wrong.
    """
    if key not in config._valid_keys:
        return _UNKNOWN
    return _OPEN if config.get_bool(key, False) else _SHUT


def _run(_arguments: dict) -> str:
    config = ChronoaConfig()
    try:
        tools, handlers = discover_skills()
    except Exception as exc:  # noqa: BLE001 - a broken registry must not crash the answer
        return (
            f"Could not read the skill registry ({type(exc).__name__}: {exc}), "
            f"so what is available is unknown - this is not an empty list."
        )
    if not tools:
        return (
            "No skills loaded. That is not the same as having no capabilities: "
            "discovery failed or every built-in module is missing, and either "
            "way this answer is not a list of what is unavailable."
        )

    found = capabilities.find_capabilities(tools)
    by_group: dict[str, list] = {}
    for capability in found:
        by_group.setdefault(capability.group, []).append(capability)

    gated_open: list[str] = []
    gated_shut: list[str] = []
    gated_unknown: list[str] = []
    ungated: list[str] = []

    lines: list[str] = []
    for group in capabilities.GROUP_ORDER + (capabilities.OTHER,):
        rows = by_group.get(group)
        if not rows:
            continue
        lines.append(f"{group}:")
        for capability in sorted(rows, key=lambda c: c.label):
            if capability.consent_key is None:
                ungated.append(capability.label)
                lines.append(f"  - {capability.label}")
                continue
            state = _gate_state(config, capability.consent_key)
            gate = capability.gate_label() or capability.consent_key
            if state == _OPEN:
                gated_open.append(capability.label)
                lines.append(f"  - {capability.label}  (allowed)")
            elif state == _SHUT:
                gated_shut.append(capability.label)
                lines.append(
                    f"  - {capability.label}  (needs \u201c{gate}\u201d switched "
                    f"on in Settings)"
                )
            else:
                gated_unknown.append(capability.label)
                lines.append(
                    f"  - {capability.label}  (gate \u201c{gate}\u201d cannot be "
                    f"determined: this build's settings schema does not declare "
                    f"it, so this may be unavailable rather than refused)"
                )

    summary = [
        "",
        f"{len(ungated)} of {len(handlers)} available now with no permission "
        f"needed; {len(gated_open)} more available because a setting is on.",
    ]
    if gated_shut:
        summary.append(
            f"{len(gated_shut)} need a setting switched on first: "
            + ", ".join(gated_shut)
        )
    if gated_unknown:
        summary.append(
            f"{len(gated_unknown)} have a gate this build cannot determine: "
            + ", ".join(gated_unknown)
        )
    summary.append(
        "This lists state only - it cannot switch anything on. To have one of "
        "the gated actions, ask for it and the app will ask you."
    )
    return "\n".join([*lines, *summary])


SKILLS = [Skill(name="list_capabilities", schema=SCHEMA, run=_run)]
