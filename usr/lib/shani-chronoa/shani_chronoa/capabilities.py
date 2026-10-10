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
    # Driver development: scaffolding text is ungated (it creates files in the
    # user's own home, like write_text_file); *building* runs a toolchain over a
    # tree, which is a different act and gets its own key.
    "driver_build": "driver-build-enabled",
    # Writing a device register can erase an EEPROM's calibration or drive a
    # power device into a state it cannot be talked out of. Reading one is not
    # gated - it is the same data class as reading a sysfs file.
    "device_i2c": "i2c-write-enabled",
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
    # Pressing a key is the same act, and `press_key` already enforced it - but
    # through `config.input_control_enabled` with no `_CONSENT_KEY`, so neither
    # this table nor the module scan could see it. It was the one skill of the
    # five input controls that the generated capability list reported as "runs
    # when asked, with no switch and no prompt", which is the opposite of what
    # it does: it refuses unless this key is on. The doc reads this table, so a
    # gate that lives only in a property is a gate nobody can see - including
    # the test that checks every gated skill consults its gate.
    "press_key": "input-control-enabled",
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
    # `systemd-analyze verify` - the command that answers "why won't it start"
    # - is deliberately **not** here. It reads a configuration file, starts
    # nothing and reports only what `verify` prints, so gating it would make
    # the first question of troubleshooting wait on a permission the person
    # granting it does not connect to the question. It is in READ_ONLY_TOOLS
    # instead, with `list_services` and `read_logs`. A key that lived only in
    # this table would refuse every call while looking configurable: it would
    # have to be in the schema and on screen in Settings to be a real gate.
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
    # The **read** key. This skill has two: reading a conflict needs this, and
    # taking a side or aborting also needs `git-write-enabled`, which the
    # module checks itself. `GATED` maps one tool to one key, so the stricter
    # of the two belongs here and the escalation has to live in the code - a
    # tuple in this dict is not a second key, it is a broken entry.
    "resolve_conflict": "git-sense-enabled",
    # Recording a change is gated, not asked every time: a commit is undone
    # with `git reset --soft HEAD~1` and the working tree is untouched by it.
    "git_commit": "git-write-enabled",
    "git_branch": "git-write-enabled",
    # Publishing is the other half and gets its own key, so turning on
    # local commits never turns on pushing. Destructive, so it asks.
    "git_push": "git-push-enabled",
    "todo_list": "todo-list-enabled",
    "manage_goals": "goals-enabled",
    "manage_triggers": "trigger-control-enabled",
    # The 2026-10-07 matrix skills. Each changing one has its own key; reading
    # the same setting needs none (the gate is checked on the 'set'/'cancel'
    # path only, as set_timezone does).
    "default_apps": "default-apps-enabled",
    "print_queue": "print-control-enabled",
    "set_hostname": "hostname-control-enabled",
    "set_locale": "locale-control-enabled",
    "speed_test": "speed-test-enabled",
    # The `sessions` sense's own key, not a new one: login history is other
    # people's presence, the same agreement whether volunteered or asked for.
    "login_history": "sessions-sense-enabled",
    # The sense-backed matrix skills share their sense's key, as git_inspect
    # does (see sense_reading.py). Where a skill also reads something no sense
    # covers (shani-deploy, systemd-analyze, lsblk, boltctl, distrobox), only
    # the sense half follows the switch. security_status's firewall half
    # follows `firewall-sense-enabled`; one key per tool here, so the primary
    # one is named.
    "snapshot_status": "snapshots-sense-enabled",
    # btrfs compression saving: the same subject the filesystems sense reports
    # (mounts and real room), so it follows that sense's switch - measured on a
    # slot, compsize walks extents and reports the ratio.
    "compression_savings": "filesystems-sense-enabled",
    # Disk activity follows the storage sense: the same subject that sense
    # reports, so it wears the same consent rather than opening a new switch.
    "disk_activity": "storage-sense-enabled",
    "security_status": "security-sense-enabled",
    "list_containers": "containers-sense-enabled",
    "boot_report": "boots-sense-enabled",
    "disk_health": "storage-sense-enabled",
    "temperatures": "hwmon-sense-enabled",
    "usb_devices": "usb-sense-enabled",
    "crash_report": "coredumps-sense-enabled",
    # The `web` sense's own key, not a new one: driving a browser is
    # the same agreement web_search asks for when it reads a page -
    # reaching the web - so one switch covers both.
    "browse": "web-sense-enabled",
    # The Siri/Google parity skills (2026-10-08). Each entry names the key the
    # skill's own description names, because `GATED` is documentation of what the
    # skills already consult - not a place to invent a policy. `news` and `maps`
    # share the `web` sense's key with `get_weather`, which they are read as.
    # `toggle_wifi` shares `airplane_mode`'s radio key: same agreement, one
    # switch, so a machine cannot permit turning one radio off while refusing
    # the other.
    "news": "web-sense-enabled",
    "maps": "web-sense-enabled",
    "take_photo": "vision-sense-enabled",
    "toggle_wifi": "radio-control-enabled",
    # Reading and writing a calendar are two agreements, so two switches -
    # `calendar_events` above is the read half and this is the write half. The
    # skill also requires the read key to move or cancel an event, because
    # finding the event to change it is the reading half.
    "calendar_edit": "calendar-write-enabled",
    # Reading a wearable's characteristics reads facts about the person wearing
    # it, so it is its own switch rather than the `bluetooth-control-enabled`
    # one. That key is about being willing to have a device disconnected;
    # noticing that a device is paired and asking a watch for its battery are
    # different agreements, and on a heart-rate strap they are not close.
    "bluetooth_gatt": "bluetooth-gatt-enabled",
    # Making a paired wearable buzz: the same switch, because it reaches the same
    # device over the same link, and it sends only fixed, known-safe commands.
    "find_device": "bluetooth-gatt-enabled",
    # The MoYoung watch itself: health data, measurements, settings, messages.
    "watch": "bluetooth-gatt-enabled",
    "phone_remote": "phone-remote-enabled",
    "nfc": "nfc-enabled",
    "fm_radio": "fm-radio-enabled",
    # Placing and answering calls. Its own switch because a call rings *someone
    # else* - the same reason `phone-messages-send-enabled` exists, and the same
    # reason the skill confirms before dialling.
    "bluetooth_call": "bluetooth-call-enabled",
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
    "arrange_window": ("Processes and windows", "Minimize, maximize, move or resize a window"),
    "press_key": ("Processes and windows", "Press a key"),
    "list_wifi_networks": ("System", "WiFi networks"),
    "connect_wifi": ("System", "Join a WiFi network"),
    "print_file": ("System", "Print a file"),
    "reminders": ("Time and reminders", "Add, list, complete, and remove reminders"),
    "notes": ("Files", "Keep your own notes: add, list, search and remove"),
    "list_services": ("Services and logs", "System services"),
    "check_units": ("Services and logs", "Verify systemd units"),
    "control_service": ("Services and logs", "Start or stop a service"),
    "read_logs": ("Services and logs", "Read the system log"),
    "create_archive": ("Files", "Make or extract an archive"),
    "find_and_replace": ("Files", "Find and replace"),
    "list_percepts": ("What Chronoa knows", "What it perceives"),
    "compute_hash": ("Files", "Checksum a file"),
    "manage_mount": ("Files", "Mount or unmount"),
    "get_battery_status": ("Power and screen", "Battery status"),
    "set_brightness": ("Power and screen", "Screen brightness"),
    "get_volume": ("Sound", "Check the volume"),
    "set_volume": ("Sound", "Change the volume"),
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
    "browse": ("Web", "Use a web page: click, type and read it"),
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
    "read_video": ("Files", "Read a video's length, size, and format"),
    "read_audio": ("Files", "Read an audio file's length, format, and bitrate"),
    "read_image": ("Files", "Read a picture's dimensions, format, and colour"),
    "create_video": ("Imagine", "Make a slideshow or colour clip"),
    "capture_video": ("Eyes", "Record a short video from the camera"),
    "create_document": ("Files", "Create a markdown, text, or HTML document"),
    "convert_document": ("Files", "Convert a document between md, txt, and html"),
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
    "edit_video": ("Imagine", "Trim, resize, rotate, or mute a video"),
    "edit_audio": ("Imagine", "Trim, normalize, change volume or speed"),
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
    # The action half, gated separately and destructively: `cleanup_report`
    # only ever reads, and a report you cannot act on is the gap this closes.
    "cleanup_apply": ("Files", "Clear caches and unused Flatpak runtimes"),
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
    # Opens one TCP connection to one address and sends nothing. Not a
    # scan: one host, one port, one connect - the same shape as ping_host.
    "check_port": ("System", "Whether a host accepts connections on a port"),
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
    # Reading what a device says about itself, one layer under `bluetooth_devices`
    # which lists and connects them. Its own switch: on a wearable the values are
    # about the person wearing it.
    "bluetooth_gatt": ("Devices", "Read a watch or band's battery, sensors and firmware"),
    "find_device": ("Devices", "Make a lost watch or band buzz so you can find it"),
    "watch": ("Devices", "Steps, sleep, stress, heart rate, SpO2 and settings on your watch"),
    "phone_remote": ("Devices", "Press the phone camera shutter, media keys, or type on the phone"),
    "nfc": ("Devices", "Read an NFC tag, or write a link to a sticker"),
    "fm_radio": ("Devices", "Listen to FM radio through a USB receiver"),
    # PipeWire's `org.pipewire.Telephony`, which is where ofono's role ended up.
    "bluetooth_call": ("Devices", "Call through a paired phone, on this computer's speakers"),
    "set_mic_mute": ("Sound", "Mute or unmute the microphone input"),
    "set_keyboard_layout": ("Pointer and keyboard", "Change the keyboard layout"),
    # Reads GNOME's two keybinding schemas rather than a copy of them, and says
    # the scheme does not apply on a desktop that does not use it. A read, so
    # ungated on the precedent of `list_windows` and `skill_machine`.
    # Where this entry sits in the table does not affect CAPABILITIES.md: the
    # generator sorts sections by descending skill count, then by name
    # (`gen_capabilities.py:220`), not by the order groups are first named here.
    # Adding the sixth entry does move "Pointer and keyboard" above "Imagine" in
    # that file, and that is the sort doing its job rather than a stray row.
    "list_shortcuts": ("Pointer and keyboard",
                       "What the keyboard shortcuts on this machine are"),
    # Reads a crontab, /etc/cron.d and both systemd timer sets - all three,
    # because a machine can have any one and none of the others.
    "scheduled_tasks": ("System",
                        "What is scheduled to run: cron jobs and systemd timers"),
    # Reads fprintd's database and nothing else: `fprintd-enroll` and
    # `fprintd-delete` are in the same package and deliberately not reachable.
    "fingerprint_status": ("Privacy controls",
                           "Whether fingerprint login is set up, and which fingers"),
    "lock_screen": ("Power and screen", "Lock this session"),
    "empty_trash": ("Files", "Permanently empty the desktop trash"),
    "search_documents": ("Files", "Search inside your files with the desktop's own index"),
    "calendar_events": ("Time and reminders", "Read what is on your calendar"),
    "phone": ("Devices", "Find, ping or send a file to your paired phone"),
    "airplane_mode": ("Devices", "Report the radios, or turn airplane mode on or off"),
    "charger_info": ("Power and screen", "Say what is charging this machine, and how fast"),
    "firmware_updates": ("System", "List firmware updates for this machine's devices"),
    "extract_archive": ("Files", "Unpack a tar, zip, 7z, rar or cab archive"),
    # Read-only: it opens the archive and writes nothing.
    "list_archive": ("Files", "List what is inside an archive, without extracting it"),
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
    # Reads all three sides git holds for a conflicted file - base, yours,
    # theirs - and can take one whole side or abandon the operation. No
    # automatic merge: that judgement belongs to the person.
    "resolve_conflict": ("Code and git",
                         "What is conflicting in a git repository, and resolve it"),
    "git_commit": ("Code and git", "Commit the files you name"),
    "git_branch": ("Code and git", "Create or switch branch"),
    "git_push": ("Code and git", "Push a branch to a named remote"),
    "project_outline": ("Code and git", "Outline a code project: files, classes and functions"),
    "todo_list": ("Code and git", "Keep a list of tasks to do"),
    "manage_goals": ("Code and git", "Save a multi-step goal to run later"),
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
    # The 2026-10-07 matrix skills.
    "snapshot_status": ("System", "Can this machine roll back, and which slot is it on"),
    "compression_savings": ("System", "How much space btrfs compression is saving"),
    "disk_activity": ("System", "How hard the disks are working right now"),
    # Read-only: `readelf` parses the file and never runs it, so it is ungated
    # by precedent with `get_file_info` and `explain_command`.
    "inspect_binary": ("System", "What a program is, and what it needs to run"),
    "process_detail": ("System", "What one running process is waiting on"),
    # Read-only: cpupower frequency-info reports, and changes nothing.
    "cpu_frequency": ("System", "The CPU governor, its speed, and its hardware range"),
    "cpu_per_core": ("System", "Which processor cores are busy right now"),
    # Downloading writes a new file and replaces nothing, so it is ungated by
    # precedent with `convert_document`.
    "download_file": ("Everyday tools", "Download a file from a web address"),
    # **Ungated, by `move_or_copy_file`'s precedent.** It writes into the
    # destination folder, so it is in the WRITE family rather than READ_ONLY -
    # but it copies files a person named and never deletes, which is exactly
    # what `move_or_copy_file` already does without a consent key. The
    # dangerous half (deleting at the destination) is refused outright
    # rather than gated, so there is no switch that turns it on.
    "sync_folder": ("Files", "Sync two folders, showing what would change first"),
    "security_status": ("System", "Secure Boot, TPM and firewall status"),
    "list_containers": ("Apps", "Distroboxes and containers"),
    "list_vms": ("Apps", "Virtual machines"),
    "boot_report": ("System", "Why booting is slow, and whether it shut down cleanly"),
    "disk_health": ("Devices", "Drive health and disk encryption"),
    "temperatures": ("Devices", "Temperatures and fan speeds"),
    "usb_devices": ("Devices", "What is plugged in, and Thunderbolt docks"),
    "driver_info": ("Devices", "Which driver each device uses"),
    # Driver work (2026-10-10). Beside driver_info because that answers "which
    # driver is bound right now" and these answer "can I write one, and how do I
    # talk to hardware without one".
    "driver_status": ("Devices", "Whether this machine can build a kernel driver"),
    "driver_scaffold": ("Devices", "Write an out-of-tree kernel module project"),
    "driver_build": ("Devices", "Compile a kernel module with Kbuild"),
    "device_probe": ("Devices", "User-space driver routes: I2C buses, video, FUSE"),
    "device_i2c": ("Devices", "Read or write an I2C device register"),
    "list_fonts": ("Appearance", "Installed fonts, and which one is used"),
    "photo_metadata": ("Photos and video", "When, where and with what camera a photo was taken"),
    "crash_report": ("Services and logs", "What crashed recently"),
    "login_history": ("System", "Who logged in recently"),
    "audio_output": ("Sound", "Switch speakers, headphones and microphones"),
    "default_apps": ("Apps", "Which app opens a kind of file, and the default browser"),
    "pdf_pages": ("Files", "Merge, split or take pages out of PDFs"),
    "print_queue": ("Devices", "The print queue, and cancelling a job"),
    "set_hostname": ("System", "This computer's name"),
    "set_locale": ("Appearance", "System language and date, number and money formats"),
    "speed_test": ("Web", "Internet speed"),
    # `browse` is deliberately NOT repeated here. It appeared twice in this
    # table - once as "Use a web page: click, type and read it" and once as
    # "Drive a web browser" - and a duplicate dict key is silently resolved to
    # the last one, so the better label had been dead with no error anywhere.
    # pyflakes reports it; nothing in the suite did. The label that survives is
    # the first one, at the top of this table.
    # The Siri and Google Assistant parity skills (2026-10-08). Grouped beside
    # the closest sibling that already had a heading, rather than by where the
    # feature came from: an alarm is a timer with a time of day, weather is
    # already filed under Web, and a Wi-Fi switch sits with the other radios.
    "alarm": ("Time and reminders", "Set, change, snooze or delete an alarm"),
    "calendar_edit": ("Time and reminders", "Add, move or cancel a calendar event"),
    # `read` half is calendar_events, which is already in this group; this is
    # the half that changes someone's day.
    "routines": ("Everyday tools", "Save a phrase that runs a whole request"),
    "maps": ("Web", "Find a place, get directions, or look up what is nearby"),
    "news": ("Web", "Today's headlines, on a topic or in general"),
    "take_photo": ("Photos and video", "Take a photo or selfie with the webcam"),
    # The Wi-Fi radio alone. airplane_mode switches every radio at once and
    # connect_wifi joins a network; neither is "turn the Wi-Fi off", which is
    # what this is and what the voice asks for most often of the three.
    "toggle_wifi": ("Devices", "Turn the Wi-Fi radio on or off"),
    "open_settings": ("System", "Open the system Settings at a page"),
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
    "notification-enabled": "Spoken replies and notifications",
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
    "sessions-sense-enabled": "Who is on this machine",
    # The Settings sense rows' own titles, so Help sends people to a row that exists.
    "snapshots-sense-enabled": "Rollback points",
    "security-sense-enabled": "Firmware security",
    "containers-sense-enabled": "Containers",
    "boots-sense-enabled": "Boot history",
    "storage-sense-enabled": "Disks",
    "hwmon-sense-enabled": "Temperatures, fans and power",
    "usb-sense-enabled": "USB devices",
    "coredumps-sense-enabled": "Crashes",
    "default-apps-enabled": "Let Chronoa change which apps open files",
    "print-control-enabled": "Let Chronoa cancel print jobs",
    "hostname-control-enabled": "Let Chronoa rename this computer",
    "locale-control-enabled": "Let Chronoa change the language and formats",
    "speed-test-enabled": "Let Chronoa run speed tests",
    "todo-list-enabled": "Let Chronoa keep a task list",
    "goals-enabled": "Let Chronoa keep multi-step goals",
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


@dataclass(frozen=True)
class EverydayTask:
    """A job a person wants done, and the skills Chronoa does it with.

    The help window used to answer "what can you do?" with 184 tool names,
    which tells nobody how Chronoa helps with their day. These are the jobs,
    each a request you can send as it stands. Every skill named here must exist
    (`test_everyday_tasks.py` fails otherwise), so a card cannot promise a tool
    Chronoa does not have.
    """

    title: str
    how: str
    prompt: str
    skills: tuple


EVERYDAY_TASKS: tuple = (
    EverydayTask(
        "Plan a trip",
        "Finds the flight on the site itself, checks the weather, works out the "
        "cost in your currency, writes the itinerary and reminds you to check in.",
        "Find the cheapest flight from Boston to London on blazedemo.com, check "
        "London's weather, tell me the price in rupees, write me an itinerary and "
        "remind me tomorrow at 9 to check in.",
        ("browse", "get_weather", "convert_currency", "create_document", "reminders")),
    EverydayTask(
        "Get on with the day",
        "Your calendar, the weather and the things you must not forget, in one answer.",
        "What's on my calendar today, what's the weather like, and remind me at 5 "
        "to call Mum.",
        ("calendar_events", "get_weather", "reminders")),
    EverydayTask(
        "Do it on a website",
        "Opens the page in its own browser window where you can watch, fills in "
        "forms and clicks through - and asks you before anything that pays.",
        "Go to wikipedia.org, search for Shaniwar Wada and tell me the three "
        "most interesting facts.",
        ("browse",)),
    EverydayTask(
        "Find and tidy your files",
        "Finds what you downloaded or changed, moves it where it belongs, and can "
        "undo the last change.",
        "Find the PDFs I downloaded this week and move them into Documents/Receipts.",
        ("find_recently_modified", "find_files", "move_or_copy_file", "undo_last_change")),
    EverydayTask(
        "Read it for me",
        "Reads PDFs, documents and pictures of text, then summarises or "
        "translates what matters.",
        "Summarise the newest PDF in my Downloads and translate the summary into Hindi.",
        ("read_document", "pdf_pages", "translate_text", "find_recently_modified")),
    EverydayTask(
        "Write it up",
        "Turns notes into a tidy document, a converted file or an archive to send.",
        "Write a one-page packing list for a week in London and save it as a document.",
        ("create_document", "convert_document", "create_archive")),
    EverydayTask(
        "Fix the internet",
        "Checks the connection, Wi-Fi, speed and name lookups, and says what is "
        "actually wrong.",
        "My internet feels slow - find out why.",
        ("check_internet", "list_wifi_networks", "speed_test", "dns_lookup", "ping_host")),
    EverydayTask(
        "Keep the computer healthy",
        "Disk space, updates, battery, temperature and what is filling the disk.",
        "Is my laptop healthy? Check disk space, updates, battery and temperature.",
        ("disk_usage", "cleanup_report", "check_updates", "get_battery_status", "temperatures")),
    EverydayTask(
        "Arrange your screen",
        "Puts windows side by side, maximises, minimises or moves them to another "
        "workspace.",
        "Put my browser and my text editor side by side.",
        ("list_windows", "arrange_window", "focus_window")),
    EverydayTask(
        "Hands busy? Just say it",
        "Press the microphone and talk: timers, conversions and answers spoken "
        "back while you cook or work.",
        "Set a timer for 12 minutes and tell me when the pasta is done.",
        ("set_timer", "speak", "convert_units")),
    EverydayTask(
        "Do it every time",
        "Rules that act on their own - when a drive is plugged in, the battery "
        "gets low or a service fails.",
        "Every time my battery drops below 20%, switch to power saver and tell me.",
        ("manage_triggers", "set_power_profile", "notify")),
    EverydayTask(
        "Fix up photos",
        "Crops, resizes, converts and brightens pictures, or makes a short video "
        "from them.",
        "Shrink the photos in Pictures/Trip so each is under 1 MB.",
        ("edit_image", "convert_media", "photos")),
    EverydayTask(
        "Help with code",
        "Explains a project, what changed in git and what is left to do.",
        "Explain this project's layout and what changed in git this week.",
        ("project_outline", "git_inspect", "todo_list")),
)


def task_needs(task: "EverydayTask", config) -> list:
    """The switches still off for `task`, by their Settings names ([] = ready)."""
    keys = sorted({GATED[s] for s in task.skills if s in GATED})
    shut = []
    for key in keys:
        try:
            on = bool(config.get_bool(key, False)) if config is not None else False
        except Exception:  # noqa: BLE001 - fails closed, like Capability.gate_is_open
            on = False
        if not on:
            shut.append(GATE_NAMES.get(key, key))
    return shut


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
    # Clearing a cache directory is a recursive `rm -rf` of the user's own files
    # with no undo ring and no trash - `trash-empty-enabled` is in this set for
    # the same reason, so this is too. Not `file-delete-enabled`: widening an
    # existing switch to cover something its prompt never named is exactly the
    # defect that made a session grant cover six other tools.
    "cleanup-enabled",
    # Pushing publishes to a remote, so a session grant must not cover it.
    # Destructive here means "asks first, always", which is the property a
    # person assumes about a push without having to read the skill.
    "git-push-enabled",
    # A register write is the hardware equivalent of an irreversible delete: an
    # EEPROM's data area has no undo, and the denylist in the skill cannot cover
    # every device. So a session grant must not cover it either.
    "i2c-write-enabled",
})

#: Tools that only observe. Kept as an allowlist rather than "anything ungated",
#: because not needing consent does not mean changing nothing: `open_application`
#: and `speak` are ungated and both act.
READ_ONLY_TOOLS = frozenset({
    "charger_info", "firmware_updates",
    # `device_i2c` reads by default; its write half is the gated direction, and
    # being in this set is what stops `EXPLORE` mode from reaching it.
    "device_i2c",
    "driver_status",
    "project_outline",
    "calendar_events",
    # Gated like calendar_events, and only reads: the one write a wearable can be
    # sent (the buzz) is its own tool, `find_device`, so it is not in this set.
    "bluetooth_gatt",
    "search_documents",
    "get_datetime", "get_volume", "get_battery_status", "get_clipboard",
    "list_apps", "list_directory", "find_files", "search_file_contents",
    "read_text_file", "open_file", "disk_usage", "system_info",
    "list_processes", "list_windows", "focus_window", "list_wifi_networks",
    # Reads two GSettings schemas and writes nothing, so without this it ends
    # every answer with "(unverified - this action reports success but nothing
    # observed it)" - a hedge about a tool that cannot report success at all.
    "list_shortcuts",
    # Reads a crontab and both systemd timer sets, and schedules nothing. A
    # machine with a user crontab and no timers still has work coming, and vice
    # versa, so the three sources are reported separately rather than merged
    # into one number that would be wrong half the time.
    "scheduled_tasks",
    # Reads the enrolled-fingerprint list; enrolls and deletes nothing, so it
    # has no post-condition to verify and no reason to withhold the answer.
    "fingerprint_status",
    "list_services", "read_logs", "check_updates", "compute_hash",
    # Runs systemd-analyze verify and changes nothing.
    "check_units",
    "list_percepts", "recommend_model", "calculate", "system_info",
    "get_weather", "get_location", "get_world_time", "convert_units",
    "convert_currency", "lookup_wikipedia", "define_word", "solve_math",
    "spell_word", "explain_command", "check_internet", "data_usage",
    "date_math", "random_pick", "my_ip_address", "read_document", "read_video", "read_audio", "read_image", "create_document", "convert_document",
    "encode_text", "convert_color", "calendar_month", "cleanup_report", "tailscale_status",
    # Read-only, and deliberately not gated: a user must be able to see what
    # the machine will do, and what is armed, without first being granted
    # permission to do any of it.
    "get_file_info", "directory_tree", "compare_files",
    "find_recently_modified", "list_capabilities",
    # The parity reads: they report and change nothing on this machine, so
    # `EXPLORE` mode (which refuses anything outside this set) can still use
    # them. `news` and `maps` are also in OPEN_WORLD_TOOLS - the two are not in
    # conflict, they answer different questions - and both consult the web sense,
    # which is what stops them in privacy mode.
    "news", "maps",
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
    # The 2026-10-07 matrix skills that only read. Eight are backed by a sense
    # and share its switch (they are in GATED too, like `calendar_events`, so
    # `tool_annotations` takes the gated branch and never claims read-only for
    # them); the rest read the kernel, a config file or a read-only command.
    "snapshot_status", "security_status", "list_containers", "list_vms",
    "boot_report", "disk_health", "temperatures", "usb_devices",
    "driver_info", "list_fonts", "photo_metadata", "crash_report",
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
    "set_timer", "reminders", "open_application", "speak", "ask_user",
    "media_control", "scan_document", "accessibility",
    "generate_password", "do_not_disturb", "convert_media", "edit_image", "edit_video", "edit_audio", "create_video", "capture_video",
    "find_emoji", "stopwatch", "bluetooth_devices",
    # writes only new files (a saved result, a chart); the source is never written
    "analyze_table", "qr_code", "generate_image", "photo_video", "recording",
    # makes sound and leaves a rendering on disk, exactly as `speak` makes sound.
    # It was missing from this set while `sing` existed, so cli_matrix classified
    # the skill as read-only ("skill", not "actuator"), the MCP read_only_hint
    # was withheld, and the post-condition column was never computed for it -
    # the inventory disagreed with the code by one entry.
    "sing",
    # Switching the default speaker is set_volume's kind of change; pdf_pages
    # writes only new files, as convert_document does.
    "audio_output", "pdf_pages",
    # The Siri/Google parity skills (2026-10-08), and the reason a gate test
    # had to be added rather than the set left as it was. `calendar_edit`
    # creates, moves and deletes events and was in neither this set nor
    # READ_ONLY_TOOLS, so `cli_matrix` classified it as neither actuator nor
    # reader, the MCP read-only hint was not withheld from an MCP client, and
    # the post-condition column was never computed for it - the inventory
    # disagreed with the code, which is the `sing` entry's defect reached from
    # the other direction.
    # NOTE: `calendar_edit`, `toggle_wifi` and `take_photo` are deliberately NOT
    # here, and adding them is a mistake worth naming because it is the
    # obvious-looking move. All three are consent-gated, and the invariant above
    # says gated tools do not belong in this set: `tool_annotations` has a
    # separate gated branch that already answers `read_only_hint: False`, and the
    # two branches disagree about `idempotent_hint`. A gated tool in both sets
    # reaches both at once, and `test_a_known_actuator_may_say_it_is_not_read_only`
    # fails. So "it acts" is not the test for membership - "it acts and nothing
    # else already says so" is.
    #
    # `alarm` and `routines` are ungated, so they belong here: neither is
    # described by a consent key, and without this entry `cli_matrix` classified
    # `alarm` as read-only even though it creates a systemd unit.
    "alarm",
    "routines",
})

#: Tools that reach outside this machine.
OPEN_WORLD_TOOLS = frozenset({
    "web_search", "scan_network", "connect_wifi", "install_model",
    "translate_text", "print_file",
    "get_weather", "get_location", "convert_currency", "lookup_wikipedia",
    "define_word", "get_world_time",
    "speed_test",
    # The parity skills that reach the network, with `browse` and `speed_test`.
    # `news` fetches publisher RSS and `maps` queries OpenStreetMap and
    # Overpass: both are reads that leave this machine, so both are refused in
    # privacy mode for the same reason `get_weather` is - which is also the
    # gate each of them consults.
    "news", "maps",
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
