"""The Senses page: every sense with its consent switch, grouped, plus the suggested ones."""


import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from shani_chronoa.config import _SENSE_CONSENT_KEYS
from shani_chronoa.senses import discover_senses


# What each sense is called, and said in a line, in the settings window.
#
# A sense's `description` is the LLM's *tool documentation*, not interface copy.
# Deriving the row from it gave raw module names as titles and subtitles
# ranging from one clause to a 300-character run-on, and reading the rendered
# window is the only reason that was noticed. So the visible text is written
# here and the schema description stays as the tooltip.
#
# An unlisted sense falls back to a title-cased name, and a test fails until it
# is listed: adding a sense should mean deciding what to call it, not
# inheriting its filename.
SENSE_LABELS = {
    "vision": (
        "Take and describe photos",
        "Take a photo when asked, if a camera is attached",
    ),
    "ocr": (
        "Read text in images",
        "Extract words from a picture you point it at",
    ),
    "filesystem": (
        "Files and folders",
        "Find, read and write files on this machine",
    ),
    "web": (
        "Web search",
        "Look things up online, and open pages",
    ),
    "memory": (
        "Remembering",
        "Keep facts you ask it to, on this machine only",
    ),
    "hearing": (
        "Transcribe speech",
        "Hear you through the microphone",
    ),
    "privilege": (
        "Who holds sensitive access",
        "Report which software can act as administrator",
    ),
    "display": (
        "Screen, brightness and monitors",
        "Report connected outputs, and set the backlight",
    ),
    "idle": (
        "Whether anyone is at this machine",
        "How long since anyone last used the keyboard or mouse",
    ),
    "accessibility": (
        "Which applications are open",
        "Which applications the desktop is showing, read over the "
        "accessibility bus",
    ),
    "bluetooth": (
        "Bluetooth",
        "Adapters, and whether rfkill has blocked them",
    ),
    "rfsense": (
        "Movement",
        "Sense motion from Wi-Fi signal strength, not what moved",
    ),
    "location": (
        "Location",
        "Where this computer is, from a GPS receiver or GNOME's location service",
    ),
    "thermalgrid": (
        "Infrared array",
        "Thermal grid sensors on the I2C bus, if one is wired up",
    ),
    "modelfit": (
        "Which models fit",
        "What this machine can run, and what is installed",
    ),
    "power": (
        "Battery",
        "Charge, how worn the battery is, and whether a charger is plugged in",
    ),
    "storage": (
        "Disks",
        "Which drives this machine has, how big, and how worn the NVMe ones are",
    ),
    "cpu": (
        "Processor and load",
        "How busy the machine is, what memory is free, and the power governor",
    ),
    "gpu": (
        "Graphics",
        "Which GPU this machine has, its driver, and how much video memory",
    ),
    "security": (
        "Firmware security",
        "Secure Boot, TPM presence, lockdown mode and the security modules in effect",
    ),
    "devices": (
        "Connected hardware",
        "What is on the PCI and USB buses, and whether any device has no driver",
    ),
    "audio": (
        "Audio devices",
        "What the machine can play and record, and at what volume",
    ),
    "capture": (
        "Cameras and microphones",
        "Which capture devices exist, and what is holding them open",
    ),
    "hwmon": (
        "Temperatures, fans and power",
        "Every sensor the firmware exposes, and whether a fan has stopped",
    ),
    "filesystems": (
        "Filesystems and room",
        "What is mounted, and how much space and how many files remain",
    ),
    "services": (
        "Service health",
        "Which system services systemd has marked as failed, with the reason",
    ),
    "timebase": (
        "Clock trustworthiness",
        "Whether the system clock is synchronised, which every reminder and "
        "timer depends on",
    ),
    "usb": (
        "USB devices",
        "What is plugged into a port, with its class and the speed it negotiated",
    ),
    "coredumps": (
        "Crashes",
        "Programs that actually crashed, and whether crashes are recorded at all",
    ),
    "firewall": (
        "Firewall",
        "Whether packets are being filtered, not just whether a firewall is installed",
    ),
    "resources": (
        "Running out of things",
        "Zombies, swap in use, and file-descriptor pressure - none look busy",
    ),
    "faults": (
        "Recent errors",
        "What the system journal has complained about recently",
    ),
    "updates": (
        "Waiting updates",
        "Updates waiting, and how old the database behind that count is",
    ),
    "snapshots": (
        "Rollback points",
        "Btrfs rollback points and the subvolume currently booted",
    ),
    "sessions": (
        "Who is on this machine",
        "Logged-in sessions, and what runs as root outside the service tree",
    ),
    "network": (
        "Network and DNS",
        "Every interface, its link speed, whether it is wireless, and the resolvers",
    ),
    # Beside the row above, and separate from the Settings>Actions switch that
    # lets Chronoa *build* lab networks. That one changes the machine's
    # networking and needs your password; this one only reports which isolated
    # networks already exist. Reading about a network and being allowed to invent
    # one are different agreements, so they are different switches.
    # Not `list_wifi_networks`: that scans for nearby networks, which says where
    # the machine is. This reads the link it is already joined and nothing else.
    "dnsresolvers": (
        "How this machine resolves names",
        "Which resolvers are used, whether queries are encrypted, and whether answers are validated",
    ),
    "wirelesslink": (
        "This machine's own WiFi link",
        "Signal strength, negotiated speed, retries, and the regulatory domain capping transmit power",
    ),
    "labnetworks": (
        "Lab networks on this machine",
        "Which isolated lab networks exist, and whether each one's namespaces are still there",
    ),
    "printing": (
        "Printers and scanners",
        "Which printers are set up, and which scanners are plugged in",
    ),
    # The machine's own immutable and current facts. `git` is the exception and
    # the only one of this batch that is off by default - see its own entry.
    "hardware": (
        "Machine identity",
        "The make and model of this machine, from the firmware",
    ),
    "kernel": (
        "Kernel and boot",
        "Which kernel is running, and whether this is a container",
    ),
    "cgroup": (
        "Resource limits",
        "The memory, CPU and process limits this process is held to",
    ),
    "containers": (
        "Containers",
        "Which containers are running, and which were killed",
    ),
    "listeners": (
        "Listening ports",
        "Which programs are listening on this machine's ports",
    ),
    "stale": (
        "Outdated programs",
        "Programs still running a version that has been replaced",
    ),
    "boots": (
        "Boot history",
        "When it last booted, and whether it shut down cleanly",
    ),
    # Off by default, unlike the seven above: filenames, the branch and the
    # unpushed count are the user's work product. Same reasoning as
    # `accessibility` and `idle` - see `config._SENSE_CONSENT_KEYS`.
    "git": (
        "Git working trees",
        "Whether your working tree is clean, and how far it has drifted",
    ),
}


# The senses, grouped by what a person would be trying to do, and the order
# they are offered in.
#
# Seventeen switches in one alphabetical list is not a settings page, it is the
# registry printed out. Nothing in it answers the only question someone opening
# this has - "which of these do I turn on?" - and the honest answer depends on
# what they want the assistant to be able to do, not on where its modules sort.
#
# `SUGGESTED` is the everyday baseline, and deliberately excludes the senses
# that observe the room or the machine: those are opt-in on their own merits.
SENSE_CATEGORIES = [
    ("Talking to Chronoa",
     "Hearing you, and remembering what you asked it to keep",
     ["hearing", "memory"]),
    ("Looking at things",
     "Reading text out of pictures, and describing what a camera sees",
     ["vision", "ocr"]),
    ("Getting work done",
     "Files, folders, git, the web and where you are - what makes it able to "
     "act rather than only answer",
     ["filesystem", "git", "web", "location"]),
    ("Is anything broken",
     "Failed services, errors the system has logged, updates that are waiting, "
     "and the running code and containers that have died - the ways a machine "
     "says it needs attention",
     ["services", "faults", "updates", "coredumps", "stale", "containers"]),
    ("Plugged in and running out",
     "What is attached by USB, and the ways a process runs out of something "
     "without the machine ever looking busy - including the limits it is held "
     "to rather than the memory it can see",
     ["usb", "resources", "cgroup"]),
    ("The screen",
     "Connected monitors, what mode they are in, the backlight, and which "
     "applications the desktop is currently showing",
     ["display", "accessibility", "idle"]),
("Network and wireless",
      "Interfaces and resolvers, the WiFi link in use, which lab networks "
      "exist, audio devices, "
      "Bluetooth, and motion from Wi-Fi signal",
      ["network", "dnsresolvers", "wirelesslink", "labnetworks", "audio", "bluetooth", "rfsense"]),
    ("Disks and room",
     "What is mounted, how much of it is left, and whether the clock can be "
     "trusted - so a reminder means what it says",
     ["filesystems", "timebase", "snapshots"]),
    ("The machine itself",
     "Which machine this is, the kernel it is running, its processor load, "
     "battery, disks and their health, graphics, temperature, fans, arrays, and "
     "when it last booted",
     ["hardware", "kernel", "boots",
      "cpu", "power", "storage", "gpu", "hwmon", "thermalgrid"]),
    ("Security and privacy",
     "Firmware security, connected hardware, which software can act as "
     "administrator, what is already using your camera, which ports are open "
     "to the network, and who else is currently on this machine",
     ["security", "devices", "privilege", "capture", "sessions", "firewall",
      "listeners"]),
    ("Printers and scanners",
     "Whether anything is set up to print, and anything is there to scan",
     ["printing"]),
    ("Model capability",
     "What this machine can run, and whether the configured model fits",
     ["modelfit"]),
]

# The everyday baseline, offered as a named action. `memory` is already on by
# default, so the useful part is hearing plus the two that let the assistant do
# something. Nothing that watches the room or the machine is in here.
SUGGESTED = ["hearing", "filesystem", "web", "display"]


class SensesPage:
    """The Senses page: every sense with its consent switch, grouped, plus the suggested ones. - a part of SettingsWindow, which mixes it in."""


    # -- the consent surface, generated from the registry --------------------

    def _build_senses(self, page) -> None:
        """One switch per registered sense, carrying that sense's own description.

        Generated rather than hand-listed. A hand-written list of seventeen
        rows is a list that is wrong the moment a sense is added, and wrong
        silently - which is precisely what the previous version was.
        """
        try:
            registry = discover_senses()
        except Exception as exc:  # noqa: BLE001 - one broken sense must not blank the window
            group = self._group(page, "Senses", "could not be loaded")
            self._info_row(group, "Sense registry failed to load", str(exc))
            return

        config = self.app.config
        enabled_first = sorted(n for n in registry if config.sense_allowed(n))
        disabled = sorted(n for n in registry if not config.sense_allowed(n))

        self._build_suggested(page, registry, config, enabled_first)

        for cat_title, cat_desc, names in SENSE_CATEGORIES:
            present = [n for n in names if n in registry]
            if not present:
                continue
            ordered = [n for n in enabled_first if n in present] + [
                n for n in disabled if n in present
            ]
            on = sum(1 for n in ordered if config.sense_allowed(n))
            group = self._group(
                page, cat_title,
                f"{cat_desc}. {on} of {len(ordered)} on.",
            )
            for name in ordered:
                self._add_sense_row(group, registry, config, name)

    def _add_sense_row(self, group, registry, config, name: str) -> None:
        sense = registry[name]
        key = _SENSE_CONSENT_KEYS.get(name)
        description = sense.schema.get("function", {}).get("description", "")
        title, summary = SENSE_LABELS.get(
            name, (name.replace("_", " ").capitalize(), "")
        )
        subtitle = summary or (description.split(". ")[0].rstrip(".") if description else "")
        if sense.is_ambient():
            subtitle += f" - polled every {int(sense.poll_interval)}s"
        row = self._switch(
            group, title, subtitle,
            bool(key) and config.sense_allowed(name),
            lambda active, n=name: self._set_sense(n, active),
            enabled=bool(key),
            tooltip=description or None,
        )
        self._sense_rows[name] = row
        # The needle carries the module name and the schema text as well as the
        # title, so someone who knows this code can type "hwmon" and find the
        # row titled "Hardware sensors".
        group._needle_extra.append((row, f"{name} {description}".lower()))

    def _build_suggested(self, page, registry, config, enabled_first) -> None:
        """One action for the everyday case, and only after a confirmation.

        Turning sensing on is a consent decision, so a bulk version of it gets
        the same care an individual toggle does: the dialog names every sense
        that will change before anything is written, the change is additive
        only (it never turns anything *off*, so it cannot quietly revoke a
        choice), and the button is gone once there is nothing left to suggest.
        """
        missing = [n for n in SUGGESTED
                   if n in registry and n not in enabled_first]
        group = self._group(
            page, "Getting started",
            "Everything here is off by default. A sense that is off is never "
            "invoked at all - the check happens before anything is read.",
        )
        if not missing:
            self._info_row(
                group, "Suggested setup is on",
                ", ".join(SENSE_LABELS.get(n, (n, ""))[0] for n in SUGGESTED
                          if n in registry) + " are already enabled.",
            )
            return
        names = ", ".join(SENSE_LABELS.get(n, (n, ""))[0] for n in missing)
        row = Adw.ActionRow(
            title="Turn on the usual ones",
            subtitle=f"Would enable: {names}. Nothing else changes.",
            activatable=True,
        )
        row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"Turn on the suggested senses: {names}"],
        )
        row.connect("activated", self._on_suggest_activated, missing)
        group.add(row)
        self._suggested_row = row

    def _on_suggest_activated(self, _row, names: list) -> None:
        dialog = Adw.AlertDialog(
            heading="Turn these on?",
            body=(
                "Chronoa will be able to use:\n\n"
                + "\n".join(f"  •  {SENSE_LABELS.get(n, (n, ''))[0]}" for n in names)
                + "\n\nNothing will be turned off, and you can change any of "
                "these afterwards."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Turn on")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_suggest_response, names)
        dialog.present(self.get_root() or self)
        self._suggest_dialog = dialog

    def _on_suggest_response(self, dialog, response: str, names: list) -> None:
        if response != "apply":
            return
        for name in names:
            key = _SENSE_CONSENT_KEYS.get(name)
            if key:
                self.app.config.set(key, "true")
        self._refresh_sense_switches()

    def _set_sense(self, name: str, active: bool) -> None:
        key = _SENSE_CONSENT_KEYS.get(name)
        if not key:
            return
        self.app.config.set(key, "true" if active else "false")
        self._refresh_sense_switches()

    def _refresh_sense_switches(self) -> None:
        """Re-derive each switch from the config rather than from the click.

        A switch showing a state the system no longer agrees with is the most
        misleading thing this window can do, and a rejected write is exactly
        when that happens.
        """
        config = self.app.config
        for name, row in self._sense_rows.items():
            if _SENSE_CONSENT_KEYS.get(name):
                row.set_active(config.sense_allowed(name))
