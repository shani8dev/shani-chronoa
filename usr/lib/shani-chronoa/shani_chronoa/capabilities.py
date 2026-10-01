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
    "delete_file": "file-delete-enabled",
    "kill_process": "process-kill-enabled",
    "close_window": "window-close-enabled",
    "connect_wifi": "wifi-connect-enabled",
    "control_service": "service-control-enabled",
    "find_and_replace": "bulk-edit-enabled",
    "manage_mount": "mount-control-enabled",
    # One key for both directions of a single-file edit. `undo_last_change` is
    # the strictly safer half - it restores what an edit recorded - so two
    # switches would imply the user can allow the risky direction and refuse
    # the safe one, which is not a choice anyone makes on purpose.
    "edit_file": "file-edit-enabled",
    "undo_last_change": "file-edit-enabled",
    # The `git` sense's own key, not a skill-specific one: the sense and this
    # skill report the same facts, and separate keys would let a fresh install
    # ship one open and the other shut.
    "git_inspect": "git-sense-enabled",
    "todo_list": "todo-list-enabled",
    "manage_triggers": "trigger-control-enabled",
}

# The per-event-type trigger gates are *not* in `GATED` above, and deliberately
# so. `GATED` is one key per tool, and `manage_triggers` arms all six event
# types; listing any one of their keys here would tell a user that switching
# "watch files" on lets them arm `unithealth` rules, which is not what that
# switch does. They gate a rule rather than a skill, so they are labels the
# refusal can name - see `GATE_NAMES` - and nothing in the menu is bound to
# them.

# Tool name -> (group heading, short menu label). A tool missing from this table
# is not dropped: `find_capabilities()` files it under `OTHER` so an unexpected
# skill is still discoverable.
_GROUPS: dict[str, tuple[str, str]] = {
    "list_directory": ("Files", "List a folder"),
    "find_files": ("Files", "Find files by name"),
    "search_file_contents": ("Files", "Search inside files"),
    "read_text_file": ("Files", "Read a text file"),
    "write_text_file": ("Files", "Write a text file"),
    "create_directory": ("Files", "Create a folder"),
    "move_or_copy_file": ("Files", "Move or copy"),
    "delete_file": ("Files", "Delete"),
    "open_file": ("Files", "Open a file"),
    "list_processes": ("Processes and windows", "Running processes"),
    "kill_process": ("Processes and windows", "Stop a process"),
    "list_windows": ("Processes and windows", "Open windows"),
    "focus_window": ("Processes and windows", "Focus a window"),
    "close_window": ("Processes and windows", "Close a window"),
    "press_key": ("Processes and windows", "Press a key"),
    "list_wifi_networks": ("System", "WiFi networks"),
    "connect_wifi": ("System", "Join a WiFi network"),
    "print_file": ("System", "Print a file"),
    "add_reminder": ("Time and reminders", "Add a reminder"),
    "list_services": ("Services and logs", "System services"),
    "control_service": ("Services and logs", "Start or stop a service"),
    "read_logs": ("Services and logs", "Read the system log"),
    "create_archive": ("Files", "Make or extract an archive"),
    "find_and_replace": ("Files", "Find and replace"),
    "list_percepts": ("What Chronoa knows", "What it perceives"),
    "compute_hash": ("Files", "Checksum a file"),
    "manage_mount": ("Files", "Mount or unmount"),
    "translate_text": ("Everyday tools", "Translate text"),
    "get_battery_status": ("Power and screen", "Battery status"),
    "set_brightness": ("Power and screen", "Screen brightness"),
    "get_volume": ("Sound", "Output volume"),
    "set_volume": ("Sound", "Output volume"),
    "media_control": ("Sound", "Play, pause and skip media"),
    "set_mute": ("Sound", "Mute and unmute"),
    "speak": ("Sound", "Speak a reply aloud"),
    "get_datetime": ("Time and reminders", "Date and time"),
    "get_world_time": ("Time and reminders", "Time somewhere else"),
    "set_timer": ("Time and reminders", "Timer"),
    # With timers, not alone: a notification is how a reminder surfaces, and a
    # heading with one row under it is a worse help screen.
    "notify": ("Time and reminders", "Send a notification"),
    "get_clipboard": ("Clipboard", "Read the clipboard"),
    "set_clipboard": ("Clipboard", "Copy something"),
    "screenshot": ("Screen", "Screenshot"),
    "open_application": ("Apps", "Open an app"),
    "web_search": ("Web", "Look something up"),
    "get_weather": ("Web", "Weather"),
    "get_location": ("Web", "Where this computer is"),
    "convert_currency": ("Web", "Currency"),
    "lookup_wikipedia": ("Web", "Who or what something is"),
    "define_word": ("Web", "What a word means"),
    "move_pointer": ("Pointer and keyboard", "Move the pointer"),
    "click_pointer": ("Pointer and keyboard", "Click"),
    "type_text": ("Pointer and keyboard", "Type text"),
    "recommend_model": ("Local models", "What model fits this machine"),
    "install_model": ("Local models", "Download a model"),
    "set_privacy": ("Privacy controls", "Mute the microphone, disable a camera, or blank the screen"),
    "set_power_profile": ("Power and screen", "Power profile"),
    "calculate": ("Everyday tools", "Calculation"),
    "convert_units": ("Everyday tools", "Convert units"),
    "solve_math": ("Everyday tools", "Algebra and calculus"),
    "ask_user": ("Everyday tools", "Ask a clarifying question"),
    "list_apps": ("Apps", "List installed applications"),
    "check_updates": ("System", "Check for waiting package updates"),
    "disk_usage": ("System", "Report filesystem and directory space use"),
    "system_info": ("System", "Describe this machine"),
    "scan_network": ("System", "Find other devices on the local network"),
    "toggle_bluetooth": ("Devices", "Turn the Bluetooth adapter on or off"),
    "set_mic_mute": ("Sound", "Mute or unmute the microphone input"),
    "set_keyboard_layout": ("Pointer and keyboard", "Change the keyboard layout"),
    "lock_screen": ("Power and screen", "Lock this session"),
    "empty_trash": ("Files", "Permanently empty the desktop trash"),
    "extract_archive": ("Files", "Unpack a tar or zip archive"),
    "trash_file": ("Files", "Move a file or folder to the trash, recoverably"),
    "set_theme": ("Appearance", "Switch the desktop between light and dark"),
    "set_wallpaper": ("Appearance", "Change the desktop wallpaper"),
    "set_scaling": ("Appearance", "Change the text size"),
    "toggle_night_light": ("Appearance", "Turn the blue-light filter on or off"),
    "set_screensaver": ("Power and screen", "Change when the screen blanks and locks"),
    "set_sleep_inhibit": ("Power and screen",
                           "Hold the machine awake for a bounded time"),
    "set_timezone": ("Time and reminders", "Report or change the system timezone"),
    "git_inspect": ("Code and git", "What changed in a git repository"),
    "todo_list": ("Code and git", "Keep a list of tasks to do"),
    "compare_files": ("Files", "Compare two files"),
    "get_file_info": ("Files", "File size, age and permissions"),
    "directory_tree": ("Files", "Show a folder's shape"),
    "find_recently_modified": ("Files", "What changed today"),
    "edit_file": ("Files", "Change one exact piece of text"),
    "undo_last_change": ("Files", "Undo Chronoa's last change to a file"),
    "manage_triggers": ("Code and git", "Arm an automatic rule"),
    "list_capabilities": ("What Chronoa knows", "What it can do right now"),
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
    "Appearance",
    "Devices",
    "Power and screen",
    "Clipboard",
    "Screen",
    "Apps",
    "Files",
    "Code and git",
    "Processes and windows",
    "Services and logs",
    "What Chronoa knows",
    "System",
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
    # A gate whose switch lives in the settings window is named here with the
    # label that row actually carries, not a second wording: the Help window
    # tells the user to switch on this exact string, so a label Settings does
    # not also show sends them looking for a switch that is not there.
    "notification-enabled": "Speak answers aloud",
    "vision-sense-enabled": "Take and describe photos",
    "web-sense-enabled": "Web search",
    "location-sense-enabled": "Location",
    "microphone-sense-enabled": "Transcribe speech",
    "file-delete-enabled": "Let Chronoa delete files",
    "process-kill-enabled": "Let Chronoa stop processes",
    "window-close-enabled": "Let Chronoa close windows",
    "wifi-connect-enabled": "Let Chronoa change WiFi",
    "service-control-enabled": "Let Chronoa change system services",
    "bulk-edit-enabled": "Let Chronoa edit many files at once",
    "mount-control-enabled": "Let Chronoa mount disks",
    "bluetooth-control-enabled": "Let Chronoa switch Bluetooth",
    "mic-control-enabled": "Let Chronoa mute the microphone",
    "screen-lock-enabled": "Let Chronoa lock this session",
    "trash-empty-enabled": "Let Chronoa empty the trash",
    "appearance-control-enabled": "Let Chronoa change the desktop look",
    "timezone-control-enabled": "Let Chronoa change the timezone",
    "idle-timeout-enabled": "Let Chronoa change when the screen blanks",
    "sleep-inhibit-enabled": "Let Chronoa hold the machine awake",
    "file-edit-enabled": "Let Chronoa edit your files",
    "git-sense-enabled": "Git working trees",
    "todo-list-enabled": "Let Chronoa keep a task list",
    "trigger-control-enabled": "Let Chronoa arm automatic rules",
    # The five trigger event types. Phrased as the thing the user is agreeing
    # to rather than as the event type, because a refusal from `triggers.py`
    # quotes this string verbatim and "fswatch" is not a word a user has ever
    # had to learn. "watch files" also says *when* it happens, which is the
    # part a user is actually being asked about.
    "fswatch-sense-enabled": "Let Chronoa watch files for changes",
    "failure-sense-enabled": "Let Chronoa act on system failures",
    "expiry-sense-enabled": "Let Chronoa watch stored deadlines",
    "containerrun-sense-enabled": "Let Chronoa act on container runs",
    "unithealth-sense-enabled": "Let Chronoa watch system units",
    # A sense gate is named here with the label its settings row actually
    # carries, not a second wording. The Help window tells the user to switch on
    # this exact string, so any label the settings window does not also show
    # sends them looking for a switch that is not there under that name.
    "filesystem-sense-enabled": "Files and folders",
    "sandbox-seccomp-enabled": "Restrict tools to a seccomp sandbox",
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
    "get_weather": "What's the weather today?",
    "get_world_time": "What time is it in Tokyo?",
    "convert_units": "How many miles is 100 km?",
    "solve_math": "What is the integral of x squared?",
    "media_control": "Pause the music",
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


#: Consent keys whose capability cannot be undone. This is the set that earns a
#: client-side "this will change something permanently" warning, so it is written
#: out explicitly rather than inferred from a name pattern - an inferred rule that
#: silently widened would put a false warning on a read, which trains people to
#: dismiss the ones that matter.
DESTRUCTIVE_CONSENT_KEYS = frozenset({
    "file-delete-enabled",
    "process-kill-enabled",
    "window-close-enabled",
    "service-control-enabled",
    "mount-control-enabled",
    "trash-empty-enabled",
    "bulk-edit-enabled",
    # `undo_last_change` shares 'file-edit-enabled' and is deliberately NOT in
    # this set: restoring is the recoverable direction of the same permission,
    # and a client warning on it would be warning on the safe half.
    "file-edit-enabled",
    "trigger-control-enabled",
})

#: Tools that only observe. Kept as an allowlist rather than "anything ungated",
#: because not needing consent does not mean changing nothing: `open_application`
#: and `speak` are ungated and both act.
READ_ONLY_TOOLS = frozenset({
    "get_datetime", "get_volume", "get_battery_status", "get_clipboard",
    "list_apps", "list_directory", "find_files", "search_file_contents",
    "read_text_file", "open_file", "disk_usage", "system_info",
    "list_processes", "list_windows", "focus_window", "list_wifi_networks",
    "list_services", "read_logs", "check_updates", "compute_hash",
    "list_percepts", "recommend_model", "calculate", "system_info",
    "get_weather", "get_location", "get_world_time", "convert_units",
    "convert_currency", "lookup_wikipedia", "define_word", "solve_math",
    # Read-only, and deliberately not gated: a user must be able to see what
    # the machine will do, and what is armed, without first being granted
    # permission to do any of it.
    "get_file_info", "directory_tree", "compare_files",
    "find_recently_modified", "list_capabilities",
})

#: Tools that change the machine but need no consent key.
#:
#: Their absence was the real gap this closes. `READ_ONLY_TOOLS` only ever
#: granted a *positive* read-only hint, and every other ungated tool fell through
#: to all-None - which the MCP library then drops entirely, so a client saw no
#: annotation at all for sixteen tools that demonstrably act: `write_text_file`,
#: `create_directory`, `move_or_copy_file`, `set_volume`, `open_application` and
#: the rest. "I do not know" is honest, but for these it was also needlessly
#: uninformative: they are certainly not read-only, and that is a true statement
#: rather than a guess.
#:
#: `destructive_hint` stays None for all of them. Whether an action is hard to
#: undo is a separate question, and guessing it is the failure this module was
#: written to avoid.
#: Ungated actuators. A consent-gated tool does NOT belong here even though it acts:
#: `tool_annotations` already gives any gated tool `read_only_hint: False` from the gate,
#: and the gated branch is the one that also claims `idempotent_hint: False`. Putting a
#: gated tool in both sets makes it the first tool to hit both branches at once, and the
#: two disagree about idempotency. `set_sleep_inhibit` was that tool; keeping the sets
#: disjoint is what stops the question arising.
MUTATING_TOOLS = frozenset({
    "write_text_file", "create_directory", "move_or_copy_file",
    "create_archive", "extract_archive", "set_clipboard", "set_volume",
    "set_mute", "set_brightness", "set_power_profile", "set_privacy",
    "set_timer", "add_reminder", "open_application", "speak", "ask_user",
    "media_control",
})

#: Tools that reach outside this machine.
OPEN_WORLD_TOOLS = frozenset({
    "web_search", "scan_network", "connect_wifi", "install_model",
    "translate_text", "print_file",
    "get_weather", "get_location", "convert_currency", "lookup_wikipedia",
    "define_word", "get_world_time",
})


def tool_annotations(tool: str, description: str) -> dict:
    """MCP tool annotations, derived from what this module already knows.

    The MCP specification lets a server tell the *host* what a tool does before
    it is called, so a client can show a warning for a destructive action instead
    of passing the call straight through. Chronoa already knows this: it has a
    consent key per risky action, and a skill that needs one is by definition
    changing something.

    Every hint is left as None unless this module can actually justify it. None
    is the honest value - a client is expected to treat an absent hint as
    unknown, whereas a guessed `read_only_hint: false` is a positive safety
    claim that this project has repeatedly been wrong about in the other
    direction.

    Not a substitute for the consent check. The key still gates the call; this
    only lets the host show the user what they are about to allow.
    """
    key = gated_by(tool, description)
    if key is not None:
        return {
            "destructive_hint": True if key in DESTRUCTIVE_CONSENT_KEYS else None,
            "read_only_hint": False,
            "idempotent_hint": False,
            "open_world_hint": True if tool in OPEN_WORLD_TOOLS else None,
        }
    read_only = tool in READ_ONLY_TOOLS
    mutates = tool in MUTATING_TOOLS
    return {
        # Not destructiveness - that stays unclaimed - but "it reads, so it
        # cannot change anything", which is true and which a client needs.
        "destructive_hint": False if read_only else None,
        "read_only_hint": True if read_only else False if mutates else None,
        # Repeating a write is not safe, but "not idempotent" is a claim about
        # the effect, so it is left unclaimed rather than inferred.
        "idempotent_hint": True if read_only else None,
        "open_world_hint": True if tool in OPEN_WORLD_TOOLS else None,
    }


def tool_title(tool: str) -> str:
    """The human-facing label for a tool, for hosts that show one."""
    entry = _GROUPS.get(tool)
    return entry[1] if entry else tool
