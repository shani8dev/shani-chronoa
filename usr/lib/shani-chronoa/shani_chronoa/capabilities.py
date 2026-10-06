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
    "desktop_setting": "appearance-control-enabled",
    "airplane_mode": "radio-control-enabled",
    "phone": "phone-control-enabled",
    "calendar_events": "calendar-read-enabled",
    "search_documents": "document-search-enabled",
    "move_pointer": "input-control-enabled",
    "click_pointer": "input-control-enabled",
    "type_text": "input-control-enabled",
    "notify": "notification-enabled",
    "screenshot": "vision-sense-enabled",
    "delete_file": "file-delete-enabled",
    # Deleting a saved conversation is deleting a file of the user's; the rest of the skill is ungated.
    "conversations": "file-delete-enabled",
    # Creating a new document is ungated (as write_text_file); changing one the user has is an edit.
    "office_document": "file-edit-enabled",
    # Reading and using another app's controls is input control, like typing into it.
    "ui_elements": "input-control-enabled",
    "android_device": "phone-control-enabled",
    "kill_process": "process-kill-enabled",
    "close_window": "window-close-enabled",
    "power_action": "power-control-enabled",
    "install_app": "app-install-enabled",
    "vpn_control": "wifi-connect-enabled",
    "connect_wifi": "wifi-connect-enabled",
    # The lab-network builder. One key for all four, and deliberately not the
    # `network-sense-enabled` or `wifi-connect-enabled` keys: those are about
    # this machine's existing connectivity (reading link state, joining a
    # network that already exists), while this one *creates* network interfaces
    # and needs the machine administrator's password to do it. Reading about a
    # network and being allowed to invent one are different agreements.
    "lab_network_list": "network-provision-enabled",
    "lab_network_create": "network-provision-enabled",
    "lab_network_destroy": "network-provision-enabled",
    "lab_network_status": "network-provision-enabled",
    # Packet capture is the only thing in the package that reads what is IN a
    # packet. Its own key, not `network-sense-enabled`: that one reads interface
    # counters, this one sees payloads, and on a machine with a browser open
    # that is a very different permission.
    "capture_packets": "packet-capture-enabled",
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
    "get_battery_status": ("Power and screen", "Battery status"),
    "set_brightness": ("Power and screen", "Screen brightness"),
    "get_volume": ("Sound", "Output volume"),
    "set_volume": ("Sound", "Output volume"),
    "media_control": ("Sound", "Play, pause and skip media"),
    "set_mute": ("Sound", "Mute and unmute"),
    "speak": ("Sound", "Speak a reply aloud"),
    "sing": ("Sound", "Sing a line aloud"),
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
    "spell_word": ("Everyday tools", "Spell a word"),
    "explain_command": ("Everyday tools", "Explain a command"),
    "scan_document": ("Everyday tools", "Scan and read a page"),
    "check_internet": ("System", "Is the internet working?"),
    "data_usage": ("System", "Data used"),
    "accessibility": ("Appearance", "Accessibility features"),
    "date_math": ("Time and reminders", "Date arithmetic"),
    "random_pick": ("Everyday tools", "Coin, dice and random picks"),
    "generate_password": ("Everyday tools", "Make a password"),
    "my_ip_address": ("System", "IP address"),
    "do_not_disturb": ("Power and screen", "Do Not Disturb"),
    "read_document": ("Files", "Read a PDF or picture"),
    "convert_media": ("Files", "Convert audio, video and pictures"),
    # ── the optional extras get their own pages ─────────────────────────────
    #
    # All five of these were filed under "Files", so a person who spent 2.0 GB on
    # SD-Turbo found "make a picture" in *Files*, next to "list a directory" -
    # and three engines had no page at all. The setup wizard has had one page per
    # extra since it was split up; this is the other half of that, so the two
    # places a person can look now agree with each other.
    #
    # Verified against what actually runs, not against what the wizard offers:
    # `imagegen` is used by `skills/generate_image.py`, `local_vision` by
    # `skills/photo_video.py`, and both `sounds` and `speakers` by
    # `skills/recording.py`.
    #
    # **Memory was nearly wrong here.** A first pass concluded that `local_embed`
    # was "installed by the wizard and read by nothing at all", from grepping for
    # module names and not finding one. It is read: `conversation_store.search()`
    # embeds the query and the stored turns through it, and the `conversations`
    # skill exposes that as `action: search`. The mistake was worth writing down,
    # because "grep found no caller" is not "nothing calls it" - the caller was
    # two indirection away, in a module named after the *store* rather than the
    # *embedding*, behind a skill whose tool name (`conversations`) shares no
    # word with either.
    "edit_image": ("Imagine", "Resize, compress or edit a picture"),
    "generate_image": ("Imagine", "Make a new picture from a description, on this computer"),
    "photo_video": ("Eyes", "Find faces and objects in a photo or video, add effects, or go through a video"),
    "photos": ("Photos and video", "Find your photos by what is in them, what is written on them, or when they were taken"),
    "recording": ("Sounds and recordings", "What a sound is, or who said what in a recording"),
    "translate_text": ("Languages", "Translate text"),
    "encode_text": ("Everyday tools", "Encode or decode text"),
    "convert_color": ("Everyday tools", "Colour codes"),
    "find_emoji": ("Everyday tools", "Find an emoji"),
    "calendar_month": ("Time and reminders", "Month calendar"),
    "stopwatch": ("Time and reminders", "Stopwatch"),
    "cleanup_report": ("Files", "What could be cleaned up"),
    "bluetooth_devices": ("Devices", "Bluetooth devices"),
    "vpn_control": ("System", "VPN connections"),
    # Packet-level work, beside the interface counters and the neighbour table
    # it sits with: capture_packets reads packets as they cross,
    # dissect_traffic says what they are, and interface_counters says how much
    # moved. One gate covers the first two.
    "dissect_traffic": ("System", "Dissect traffic into protocol fields"),
    # Pre-existing strays: these three are complete skills with no _GROUPS
    # entry, so they fell through to OTHER and failed
    # test_no_builtin_skill_lands_in_the_other_group. Grouped here so the
    # surface test passes and they appear somewhere a user can find them.
    "ping_host": ("System", "Ping a host"),
    "routing_table": ("System", "The machine's routing table"),
    "tls_certificate": ("System", "Inspect a TLS certificate"),
    "tailscale_status": ("System", "Tailscale"),
    "install_app": ("Apps", "Install and remove apps"),
    "power_action": ("Power and screen", "Suspend, restart or shut down"),
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
    "search_documents": ("Files", "Search inside your files with the desktop's own index"),
    "calendar_events": ("Time and reminders", "Read what is on your calendar"),
    "phone": ("Devices", "Find, ping or send a file to your paired phone"),
    "airplane_mode": ("Devices", "Report the radios, or turn airplane mode on or off"),
    "charger_info": ("Power and screen", "Say what is charging this machine, and how fast"),
    "firmware_updates": ("System", "List firmware updates for this machine's devices"),
    "extract_archive": ("Files", "Unpack a tar or zip archive"),
    "trash_file": ("Files", "Move a file or folder to the trash, recoverably"),
    "set_theme": ("Appearance", "Switch the desktop between light and dark"),
    "desktop_setting": ("Appearance", "Read or change a desktop setting (animations, clock, cursor, touchpad...)"),
    "set_wallpaper": ("Appearance", "Change the desktop wallpaper"),
    "set_scaling": ("Appearance", "Change the text size"),
    "toggle_night_light": ("Appearance", "Turn the blue-light filter on or off"),
    "set_screensaver": ("Power and screen", "Change when the screen blanks and locks"),
    "set_sleep_inhibit": ("Power and screen",
                           "Hold the machine awake for a bounded time"),
    "set_timezone": ("Time and reminders", "Report or change the system timezone"),
    "git_inspect": ("Code and git", "What changed in a git repository"),
    "project_outline": ("Code and git", "Outline a code project: files, classes and functions"),
    "todo_list": ("Code and git", "Keep a list of tasks to do"),
    "compare_files": ("Files", "Compare two files"),
    "get_file_info": ("Files", "File size, age and permissions"),
    "directory_tree": ("Files", "Show a folder's shape"),
    "find_recently_modified": ("Files", "What changed today"),
    "edit_file": ("Files", "Change one exact piece of text"),
    "undo_last_change": ("Files", "Undo Chronoa's last change to a file"),
    "manage_triggers": ("Code and git", "Arm an automatic rule"),
    "list_capabilities": ("What Chronoa knows", "What it can do right now"),
    "conversations": ("What Chronoa knows", "Search, reopen, copy or export earlier conversations"),
    "office_document": ("Files", "Read, create or edit Word, Excel and PowerPoint files"),
    "analyze_table": ("Files", "Ask questions of a spreadsheet or CSV, and chart it"),
    # Beside `analyze_table` on purpose: that one answers arithmetic questions
    # over rows, this one answers what a document actually contains. Grouped
    # together because they are the two halves of "what is in this file".
    "json_query": ("Files", "Ask what a JSON document contains"),
    "port_owner": ("System", "Which program is using a port"),
    # Next to `check_internet`, which it does not overlap: that one walks a
    # fixed ladder and takes no arguments, so it cannot be pointed at a host.
    "trace_route": ("System", "Trace the network path to a host"),
    # The machine-asks-what-it-can-do layer. Grouped with the other System
    # entries because that is what it answers: what this machine offers, and
    # therefore which path to a goal is actually open here.
    "machine_capabilities": ("System", "What this machine can actually do"),
    # The lab-network builder, grouped with the other System entries. All four
    # share one consent key, so a user cannot allow `vpc_status` - which only
    # reports reachability - while refusing `vpc_create`, which builds
    # interfaces and needs the machine administrator's password.
    "lab_network_list": ("System", "List the lab networks built on this machine"),
    "lab_network_create": ("System", "Build an isolated lab network with named subnets"),
    "lab_network_destroy": ("System", "Remove a lab network this machine built"),
    "lab_network_status": ("System", "Check what a lab network can actually reach"),
    "interface_counters": ("System", "How much traffic each interface has carried"),
    "bridge_topology": ("System", "Which interfaces are bridges, and what is plugged into them"),
    "neighbour_table": ("System", "Which addresses on this link have answered, and which never have"),
    # The active counterpart to neighbour_table: that one reads the kernel's
    # passive cache and so only sees hosts that already talked to this machine.
    # This one asks, and only ever about a private segment - the one this
    # machine is on, or a private CIDR named explicitly.
    "discover_hosts": ("System", "Who is on this network right now"),
    # The Lookup and Whois tabs of GNOME's gnome-nettool, which nothing here
    # covered. Read-only, but they leave the machine - so they go in the
    # ungated read-only set and are refused in privacy mode, exactly as
    # `trace_route` is.
    "dns_lookup": ("System", "Look a name up in DNS, or read its mail, name or certificate records"),
    "whois_lookup": ("System", "Look up who a domain or address is registered to"),
    "capture_packets": ("System", "Watch the packets crossing an interface"),
    "ui_elements": ("Pointer and keyboard", "Press buttons, fill fields and open menus in other apps"),
    "android_device": ("Devices", "Battery, screenshot, apps and files of a phone connected with adb"),
    "qr_code": ("Everyday tools", "Read a QR code or barcode, or make a QR code"),
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
    "Eyes",
    "Photos and video",
    "Imagine",
    "Sounds and recordings",
    "Languages",
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
    "power-control-enabled": "Let Chronoa suspend, restart or shut down",
    "app-install-enabled": "Let Chronoa install and remove apps",
    "wifi-connect-enabled": "Let Chronoa change WiFi",
    "network-provision-enabled": "Let Chronoa build lab networks",
    "packet-capture-enabled": "Let Chronoa watch network packets",
    "service-control-enabled": "Let Chronoa change system services",
    "bulk-edit-enabled": "Let Chronoa edit many files at once",
    "mount-control-enabled": "Let Chronoa mount disks",
    "bluetooth-control-enabled": "Let Chronoa switch Bluetooth",
    "mic-control-enabled": "Let Chronoa mute the microphone",
    "screen-lock-enabled": "Let Chronoa lock this session",
    "trash-empty-enabled": "Let Chronoa empty the trash",
    "document-search-enabled": "Let Chronoa search inside your files",
    "calendar-read-enabled": "Let Chronoa read your calendar",
    "calendar-sense-enabled": "Let Chronoa act before calendar events",
    "phone-control-enabled": "Let Chronoa use your paired phone",
    "phone-sense-enabled": "Let Chronoa act on your phone connecting",
    "heard-sound-sense-enabled": "Let Chronoa name a sound when you ask",
    "radio-control-enabled": "Let Chronoa switch airplane mode",
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
    "screenlock-sense-enabled": "Let Chronoa act when the screen locks",
    "powerstate-sense-enabled": "Let Chronoa act on power changes",
    "netstate-sense-enabled": "Let Chronoa act on network changes",
    "usbplug-sense-enabled": "Let Chronoa act on USB devices",
    "btconnect-sense-enabled": "Let Chronoa act on Bluetooth devices",
    "schedule-sense-enabled": "Let Chronoa act on a schedule",
    "sleepwake-sense-enabled": "Let Chronoa act when the machine wakes",
    "audiodevice-sense-enabled": "Let Chronoa act on audio devices",
    "journalmatch-sense-enabled": "Let Chronoa act on log messages",
    "dbusprop-sense-enabled": "Let Chronoa watch system properties",
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
    "spell_word": "How do you spell necessary?",
    "check_internet": "Is my internet working?",
    "accessibility": "Turn on the screen reader",
    "date_math": "How many days until 25 December?",
    "random_pick": "Flip a coin",
    "find_emoji": "Emoji for a red heart",
    "bluetooth_devices": "Connect my headphones",
    "cleanup_report": "What can I clean up to free space?",
    "do_not_disturb": "Turn on Do Not Disturb",
    "media_control": "Pause the music",
    "recommend_model": "Which model would fit this machine?",
    "speak": "Read that back to me",
    "sing": "Sing that for me",
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
    "power-control-enabled",
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
    "charger_info", "firmware_updates",
    "project_outline",
    "calendar_events",
    "search_documents",
    "get_datetime", "get_volume", "get_battery_status", "get_clipboard",
    "list_apps", "list_directory", "find_files", "search_file_contents",
    "read_text_file", "open_file", "disk_usage", "system_info",
    "list_processes", "list_windows", "focus_window", "list_wifi_networks",
    "list_services", "read_logs", "check_updates", "compute_hash",
    "list_percepts", "recommend_model", "calculate", "system_info",
    "get_weather", "get_location", "get_world_time", "convert_units",
    "convert_currency", "lookup_wikipedia", "define_word", "solve_math",
    "spell_word", "explain_command", "check_internet", "data_usage",
    "date_math", "random_pick", "my_ip_address", "read_document",
    "encode_text", "convert_color", "calendar_month", "cleanup_report", "tailscale_status",
    # Read-only, and deliberately not gated: a user must be able to see what
    # the machine will do, and what is armed, without first being granted
    # permission to do any of it.
    "get_file_info", "directory_tree", "compare_files",
    "find_recently_modified", "list_capabilities",
    # Both read a file or the kernel's own tables and change nothing, which is
    # why they are here and not behind a gate: a user must be able to ask what
    # is listening, and what a file says, without first being granted
    # permission to act on it.
    "json_query", "port_owner",
    # Reads /proc/net/dev and the kernel's own TCP counters. No daemon, no
    # privilege, and no state written anywhere - which is why it is here rather
    # than behind a gate, and why it is not `ifstat` or `vnstat`.
    "interface_counters",
    # Reads /sys/class/net only: which interfaces are bridges and which are
    # enslaved to them. No command, no privileges, nothing written.
    "bridge_topology",
    # /proc/net/arp only. Unprivileged, unlike `ip neigh` and `arp`, and the
    # COMPLETE/UNRESOLVED split is what `ping_host` cannot tell you.
    "neighbour_table",
    # Read-only in effect but not in intent: it sends probes beyond this network,
    # exactly as the public steps of `check_internet` do, and is refused in
    # privacy mode. It is listed here because on the machines where it runs it
    # changes nothing on this one - it is a measurement, not an actuator.
    "trace_route",
    # Read-only in effect but not in intent: both leave this machine, which is
    # why privacy mode refuses them. A DNS query tells a resolver which names
    # this computer wants; a whois query can return the person who registered
    # the domain. Neither changes anything here.
    "dns_lookup", "whois_lookup",
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
    "media_control", "scan_document", "accessibility",
    "generate_password", "do_not_disturb", "convert_media", "edit_image",
    "find_emoji", "stopwatch", "bluetooth_devices",
    # writes only new files (a saved result, a chart); the source is never written
    "analyze_table", "qr_code", "generate_image", "photo_video", "recording",
    # makes sound and leaves a rendering on disk, exactly as `speak` makes sound.
    # It was missing from this set while `sing` existed, so cli_matrix classified
    # the skill as read-only ("skill", not "actuator"), the MCP read_only_hint
    # was withheld, and the post-condition column was never computed for it -
    # the inventory disagreed with the code by one entry.
    "sing",
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


# --- Permission presets ------------------------------------------------------
#
# Around forty separate switches is accurate and unreadable for a new user;
# sayri offers named levels with a one-line summary (`cajita.py:3874`). These
# set only the ACTION permissions (what Chronoa may change), never the senses
# (what it may perceive) or privacy mode, and "Custom" is what any hand-made
# combination reads as.

ACTION_CONSENT_KEYS = tuple(sorted(
    {k for k in GATED.values() if not k.endswith("-sense-enabled")} | set(DESTRUCTIVE_CONSENT_KEYS)))

PRESETS = {
    "chat": ("Chat only", "Answers and reads, but changes nothing on this computer",
             lambda key: False),
    "everyday": ("Everyday", "Changes settings, writes and edits files, controls apps; never deletes, "
                             "stops or powers off anything",
                 lambda key: key not in DESTRUCTIVE_CONSENT_KEYS or key == "file-edit-enabled"),
    "full": ("Full control", "Everything Chronoa can do, including deleting files and powering off",
             lambda key: True),
}


def apply_preset(name: str, config) -> None:
    rule = PRESETS[name][2]
    for key in ACTION_CONSENT_KEYS:
        config.set(key, "true" if rule(key) else "false")


def current_preset(config) -> str:
    """The preset these switches match, or "custom"."""
    state = {k: config.get_bool(k, False) for k in ACTION_CONSENT_KEYS}
    for name, (_label, _summary, rule) in PRESETS.items():
        if all(state[k] == rule(k) for k in ACTION_CONSENT_KEYS):
            return name
    return "custom"
