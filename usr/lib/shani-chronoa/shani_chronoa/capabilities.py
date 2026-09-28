"""What Chronoa can actually do, in words a user would use.

The obvious way to build a suggestions bar is to hardcode a list of example
sentences. That list is a lie the moment it is written: it keeps advertising a
skill that a user's build failed to load, one that was renamed, or one that has
been removed, and the user finds out by typing a suggestion and getting nothing
back.

So every entry here is **derived from the live skill registry**
(`discover_skills()`), at the moment the window is built. A skill that did not
load has no entry, and so cannot be suggested. The cost is that this module must
be tolerant of registry contents it has never seen - a user can drop a module
into `~/.config/shani-chronoa/skills/`, so the 19 built-ins are not a closed
set and `find_capabilities()` must not raise on one it does not recognise.

A second reason the group names are hand-written: the tool names are
`get_volume`, `set_timer`, `type_text` and the descriptions are sentences
written for a model, not for a person. Both are fine for a tool-calling prompt
and useless as a menu heading. The grouping below is by *what a user is trying
to do*, the same re-shaping that turned the settings sense list from module
names (`hwmon`, `thermalgrid`) into something a person can read.

The one thing that is genuinely not derivable is which skills are *gated*.
Those are read out of the registry descriptions, which name their consent key
(`Requires the 'input-control-enabled' consent key`) - see `gated_by()`. This
matters more than it looks: those skills fail silently when their key is off,
so a user asking for one gets nothing and no explanation. Naming the gate, and
saying whether it is currently on, is the difference between a help screen and
a list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "Capability",
    "GATED",
    "capability_for",
    "find_capabilities",
    "gated_by",
    "startup_suggestions",
]


# Consent keys named inside a skill's own description. Kept as a data table
# rather than parsed out at runtime because the set is small, and a wrong
# parse should be a visible constant to correct rather than a regex change
# whose effect nobody can see.
GATED: dict[str, str] = {
    "move_pointer": "input-control-enabled",
    "click_pointer": "input-control-enabled",
    "type_text": "input-control-enabled",
    "notify": "notification-enabled",
    "screenshot": "vision-sense-enabled",
}

# Tool name -> (group heading, short menu label). A tool missing from this table
# is not dropped: `find_capabilities()` files it under `OTHER` so an unexpected
# skill is still discoverable.
_GROUPS: dict[str, tuple[str, str]] = {
    "get_battery_status": ("Power and screen", "Battery status"),
    "set_brightness": ("Power and screen", "Screen brightness"),
    "get_volume": ("Sound", "Output volume"),
    "set_volume": ("Sound", "Output volume"),
    "set_mute": ("Sound", "Mute and unmute"),
    "speak": ("Sound", "Speak a reply aloud"),
    "get_datetime": ("Time and reminders", "Date and time"),
    "set_timer": ("Time and reminders", "Timer"),
    # With timers, not alone: a notification is how a reminder surfaces, and a
    # heading with one row under it is a worse help screen.
    "notify": ("Time and reminders", "Send a notification"),
    "get_clipboard": ("Clipboard", "Read the clipboard"),
    "set_clipboard": ("Clipboard", "Copy something"),
    "screenshot": ("Screen", "Screenshot"),
    "open_application": ("Apps", "Open an app"),
    "web_search": ("Web", "Look something up"),
    "move_pointer": ("Pointer and keyboard", "Move the pointer"),
    "click_pointer": ("Pointer and keyboard", "Click"),
    "type_text": ("Pointer and keyboard", "Type text"),
    "recommend_model": ("Local models", "What model fits this machine"),
    "install_model": ("Local models", "Download a model"),
    "set_privacy": ("Privacy controls", "Mute the microphone, disable a camera, or blank the screen"),
    "set_power_profile": ("Power and screen", "Power profile"),
    "calculate": ("Everyday tools", "Calculation"),
}

# The order groups appear in the help window. Deliberately the order a new user
# would want them, not alphabetical: what Chronoa is (talk to it, ask about the
# machine) before what it can drive (the screen, the apps), and the pointer
# controls last, because they are the ones that move someone's mouse.
GROUP_ORDER: tuple[str, ...] = (
    "Privacy controls",
    "Time and reminders",
    "Everyday tools",
    "Sound",
    "Power and screen",
    "Clipboard",
    "Screen",
    "Apps",
    "Web",
    "Local models",
    "Pointer and keyboard",
)
OTHER = "Other"

# Consent key -> what to call it when explaining the gate to a person. The
# gsettings key is not a label; `input-control-enabled` means nothing to
# someone who has never opened a terminal.
GATE_NAMES: dict[str, str] = {
    "input-control-enabled": "Let Chronoa act",
    "notification-enabled": "Allow notifications",
    "vision-sense-enabled": "Allow the screen sense",
    "web-sense-enabled": "Allow web lookups",
    "microphone-sense-enabled": "Allow the microphone sense",
}


@dataclass(frozen=True)
class Capability:
    """One thing the assistant can do, phrased for a menu rather than a model."""

    tool: str
    """The registry name, verbatim. The join back to reality."""

    group: str
    label: str
    """Short human label for the menu."""

    description: str
    """The skill's own description, first sentence, unchanged."""

    consent_key: str | None = None
    """The gsettings key that must be on for this to work, if any."""

    example: str = ""
    """A prompt that exercises it, or '' for skills needing a free argument."""

    def gate_label(self) -> str:
        """What the gate is called to a person, or '' if ungated."""
        if self.consent_key is None:
            return ""
        return GATE_NAMES.get(self.consent_key, self.consent_key)

    def gate_help(self, allowed: bool) -> str:
        """One line explaining the gate, in whichever state it is in.

        Naming the gate while it is *off* is the entire point: the skill
        refuses to run, and a refusal the user cannot see the reason for looks
        exactly like the assistant being broken.
        """
        if self.consent_key is None:
            return ""
        row = self.gate_label() or self.consent_key
        if allowed:
            return f"On. \u201c{row}\u201d is allowing this."
        return f"Off. Switch on \u201c{row}\u201d in Settings to use this."

    def gate_is_open(self, config) -> bool:
        """Whether this capability is usable right now.

        Fails closed. A key the app does not recognise can never be granted, and
        one whose check raises has not been shown to be on, so both read as
        *closed* rather than open - the same rule the machine-state senses
        follow, where "I could not determine this" is a state the code can
        represent and is never rounded up to a clean yes.
        """
        if self.consent_key is None:
            return True
        try:
            return bool(config.sense_allowed(self.consent_key))
        except Exception:
            return False


def gated_by(tool: str, description: str) -> str | None:
    """The consent key a tool's description declares, if any.

    Parsed from the description as well as cross-checked against `GATED`,
    because the description is written by whoever added the skill and is the
    more likely of the two to be current. `GATED` is the floor, not the ceiling.
    """
    if tool in GATED:
        return GATED[tool]
    match = re.search(r"'([a-z0-9-]+-enabled)'", description or "")
    return match.group(1) if match else None


# An example prompt per skill. Only for skills with no free-text argument -
# a suggestion that needs the user to supply "which app" is not a suggestion,
# it is a question. An empty string means "shown in help, not offered as a
# one-click suggestion".
_EXAMPLES: dict[str, str] = {
    "get_battery_status": "How much battery is left?",
    "get_datetime": "What time is it?",
    "set_timer": "Set a timer for 10 minutes",
    "set_volume": "Set my volume to 50%",
    "set_mute": "Mute the sound",
    "get_clipboard": "What is on my clipboard?",
    "screenshot": "Take a screenshot",
    "web_search": "Search the web for the Arch wiki",
    "recommend_model": "Which model would fit this machine?",
    "speak": "Read that back to me",
    "get_volume": "How loud is the volume?",
    "set_brightness": "Set brightness to 60%",
    "set_clipboard": "Copy this to the clipboard",
}

# The empty state offers these, in this order: enough to show the range of what
# exists, none of them needing an argument, and the first one is a question
# with an immediate answer so the window is not a dead end.
_SUGGESTION_ORDER: tuple[str, ...] = (
    "How much battery is left?",
    "What time is it?",
    "Set a timer for 10 minutes",
    "Which model would fit this machine?",
    "Search the web for the Arch wiki",
    "Take a screenshot",
)

_HELP_SUGGESTION = "What can you do?"


def _first_sentence(text: str) -> str:
    """Trim a model-facing description down to one readable sentence.

    The descriptions are written to be read by a model that has the tool schema
    in front of it, so they are wordy and full of qualifiers. A menu entry that
    says *"Get or set screen brightness as a percentage. With no level, reports
    the current value."* is not a help screen, it is the prompt moved.
    """
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
    sentence = parts[0].strip()
    return sentence if len(sentence) <= 120 else sentence[:117].rstrip() + "…"


def find_capabilities(tools) -> list[Capability]:
    """Build the capability list from a live tool registry.

    `tools` is the `TOOLS` half of `discover_skills()` - the Ollama function
    wrappers. Anything malformed in it is skipped rather than raised on: a
    broken third-party skill must not take the whole window down, and the
    remaining eighteen are still worth showing.
    """
    found: list[Capability] = []
    for tool in tools or ():
        try:
            function = tool.get("function", tool)
            name = function.get("name")
            if not name:
                continue
            description = function.get("description") or ""
        except AttributeError:
            # Not a mapping at all - a malformed registry entry.
            continue
        group, label = _GROUPS.get(name, (OTHER, name.replace("_", " ")))
        found.append(
            Capability(
                tool=name,
                group=group,
                label=label,
                description=_first_sentence(description),
                consent_key=gated_by(name, description),
                example=_EXAMPLES.get(name, ""),
            )
        )
    return found


def startup_suggestions(capabilities: list[Capability]) -> list[str]:
    """The prompts offered on an empty transcript.

    Derived from what loaded, not hardcoded: an entry survives only if its skill
    is present. When nothing matches, the list is empty rather than padded with
    suggestions the app cannot honour.
    """
    available = {c.example for c in capabilities if c.example}
    ordered = [s for s in _SUGGESTION_ORDER if s in available]
    if not ordered and available:
        ordered = sorted(available)[:4]
    return ordered


def help_prompt() -> str:
    """The prompt that opens the capability list rather than answering in text."""
    return _HELP_SUGGESTION


def capability_for(capabilities: list[Capability], tool: str) -> Capability | None:
    for capability in capabilities:
        if capability.tool == tool:
            return capability
    return None
