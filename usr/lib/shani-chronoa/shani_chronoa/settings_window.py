"""Settings window: real Adwaita pages, every sense reachable, no hardcoded theme.

Three things were wrong with the previous version, and only the first was
cosmetic.

**The consent model was unreachable.** Seventeen senses ship, each behind its
own `*-sense-enabled` key, and the settings window mentioned none of them -
zero. A user could install Chronoa, be told a sense is turned off, and have no
way to turn it on except `gsettings set` from a terminal. The switches here are
generated from the registry and the consent table rather than hand-listed, so a
sense added tomorrow appears without anyone editing this file, and one whose
key is missing from the installed schema is shown as ungrantable rather than
silently missing.

**It hardcoded a dark palette.** `window { background-color: #1a1a2e; }` and
hand-picked entry colours meant a user with a light GTK theme got a dark
dialog, and the window fought Adwaita rather than joining it. There are no
colours here now: the window uses whatever the desktop theme provides, which
is the only version that looks right on the light, dark and high-contrast
themes a user may already have chosen.

**It hand-rolled its rows out of `Gtk.Box`.** Every switch and entry was a
label plus a widget in a box, so there were no Adwaita group semantics, no
keyboard navigation the way a settings dialog is expected to behave, and no
accessibility roles. These are `Adw.SwitchRow` / `Adw.EntryRow` /
`Adw.PreferencesGroup`, which is what they should have been.

On top of that: a search entry that filters as you type, because twenty-odd
rows in one scroll is not navigable; API-key rows that can reveal what you
pasted instead of leaving you unable to check it; and a Models section showing
which model is *actually* in effect and why, via `models.py`, so the hardware
tier's guess is visible as a guess rather than presented as a decision.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # type: ignore

from shani_chronoa import models, pipewire
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
    "bluetooth": (
        "Bluetooth",
        "Adapters, and whether rfkill has blocked them",
    ),
    "rfsense": (
        "Movement",
        "Sense motion from Wi-Fi signal strength, not what moved",
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
    "resources": (
        "Running out of things",
        "Zombies, swap in use, and file-descriptor pressure - none look busy",
    ),
    "network": (
        "Network and DNS",
        "Every interface, its link speed, whether it is wireless, and the resolvers",
    ),
    "printing": (
        "Printers and scanners",
        "Which printers are set up, and which scanners are plugged in",
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
     "Files, folders and the web - what makes it able to act rather than only answer",
     ["filesystem", "web"]),
    ("Is anything broken",
     "Which system services systemd has marked as failed, and why",
     ["services"]),
    ("Plugged in and running out",
     "What is attached by USB, and the ways a machine runs out of something "
     "without ever looking busy",
     ["usb", "resources"]),
    ("The screen",
     "Connected monitors, what mode they are in, and the backlight",
     ["display"]),
    ("Network and wireless",
     "Interfaces and resolvers, audio devices, Bluetooth, and motion from Wi-Fi signal",
     ["network", "audio", "bluetooth", "rfsense"]),
    ("Disks and room",
     "What is mounted, how much of it is left, and whether the clock can be "
     "trusted - so a reminder means what it says",
     ["filesystems", "timebase"]),
    ("The machine itself",
     "Processor load, battery, disks and their health, graphics, temperature, fans, and arrays",
     ["cpu", "power", "storage", "gpu", "hwmon", "thermalgrid"]),
    ("Security and privacy",
     "Firmware security, connected hardware, which software can act as "
     "administrator, and what is already using your camera",
     ["security", "devices", "privilege", "capture"]),
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


class SettingsWindow(Gtk.Window):
    """Every setting Chronoa has, in one searchable window."""

    def __init__(self, app) -> None:
        super().__init__(application=app, title="Chronoa Settings")
        self.app = app
        self.set_default_size(560, 720)
        if app.window:
            self.set_transient_for(app.window)
        # (widget, lowercase text to match against) for the search filter.
        self._searchable = []
        # Sense switches, so their real state can be re-derived after a change
        # instead of trusting what the user just clicked.
        self._sense_rows = {}
        self._build_ui()

    # -- construction --------------------------------------------------------

    def _build_ui(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=True)
        self._search = Gtk.SearchEntry(hexpand=True)
        self._search.set_placeholder_text("Search settings")
        self._search.connect("search-changed", self._on_search)
        header.pack_start(self._search)
        # Titlebar only. Adding it to `outer` as well is a second parent, and
        # GTK rejects that with "gtk_widget_get_parent (child) == NULL" - a
        # window can only ever have one.
        self.set_titlebar(header)

        page = Adw.PreferencesPage()
        self._build_senses(page)
        self._build_privacy(page)
        self._build_voice(page)
        self._build_models(page)
        self._build_system(page)

        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(page)
        scrolled.set_vexpand(True)
        outer.append(scrolled)
        self.set_child(outer)

    def _group(self, page, title, description="") -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=title, description=description or None, margin_top=14, margin_bottom=14
        )
        page.add(group)
        self._searchable.append((group, f"{title} {description}".lower()))
        group._needle_extra = []  # rows to reveal if only they match
        return group

    def _switch(self, group, title, subtitle, active, on_toggle, enabled=True, tooltip=""):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or None, active=bool(active))
        if not enabled:
            row.set_subtitle("no consent key in the installed schema - this cannot be granted")
            row.set_sensitive(False)
        if tooltip:
            row.set_tooltip_text(tooltip)
        row.connect("notify::active", lambda r, *_: on_toggle(r.get_active()))
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

    def _entry(self, group, title, subtitle, value, on_changed, secret=False):
        row = Adw.EntryRow(title=title)
        if subtitle:
            row.set_tooltip_text(subtitle)
        entry = Gtk.Entry(text=str(value or ""), hexpand=True)
        if secret:
            entry.set_visibility(False)
        entry.connect("changed", lambda e: on_changed(e.get_text()))
        row.add_suffix(entry)
        if secret:
            reveal = Gtk.ToggleButton(icon_name="view-reveal-symbolic", valign=Gtk.Align.CENTER)
            reveal.add_css_class("flat")
            reveal.set_tooltip_text("Show this key")
            reveal.connect("toggled", lambda b: entry.set_visible(b.get_active()))
            row.add_suffix(reveal)
        group.add(row)
        group._needle_extra.append((row, f"{title}".lower()))
        return row

    def _info_row(self, group, title, subtitle):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

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

    # -- sections ------------------------------------------------------------

    def _build_privacy(self, page) -> None:
        config = self.app.config
        group = self._group(
            page, "Privacy and network",
            "Privacy mode is the master switch: with it on, Chronoa keeps speech, "
            "screen and sensed data on this machine and sends nothing to a cloud provider.",
        )
        self._switch(group, "Privacy mode (local only)", "Master switch for leaving this machine",
                     config.privacy_mode, lambda a: self._app_toggle("toggle-privacy", a))
        self._switch(
            group, "Cloud fallback when Ollama is unavailable",
            "Separate opt-in on purpose - this never turns on from one switch alone",
            config.cloud_fallback_enabled,
            lambda a: self._app_toggle("toggle-cloud-fallback", a))

        # The gate every actuator passes through. `triggers.py` refuses any
        # action without it and names it, so a user whose armed rules do
        # nothing had no way to turn it on except `gsettings set` from a
        # terminal - the same trap the seventeen sense switches were in.
        self._switch(
            group, "Let Chronoa act on this machine",
            "Off: Chronoa can answer but cannot run actions. Every trigger "
            "rule needs this, so an armed rule does nothing until it is on.",
            self._read_bool("input-control-enabled"),
            lambda a: self._set_bool("input-control-enabled", a),
            tooltip="triggers.py refuses every actuator while this is off",
        )

        # Every new destructive action needs a row here, or the key exists in
        # the schema and nowhere a person can reach it - which is the same trap
        # as a refusal that names a setting with no switch.
        for label, key, blurb, tip in (
            ("Let Chronoa delete files", "file-delete-enabled",
             "Off: Chronoa can read, search, create and move files but never "
             "delete one. Deletion here is permanent and does not use the trash.",
             "delete_file refuses while this is off"),
            ("Let Chronoa stop processes", "process-kill-enabled",
             "Off: Chronoa can list running processes but cannot stop one. A "
             "wrong process id can take down unsaved work.",
             "kill_process refuses while this is off"),
            ("Let Chronoa close windows", "window-close-enabled",
             "Off: Chronoa can list and focus windows but cannot ask one to "
             "close, which can discard unsaved work.",
             "close_window refuses while this is off"),
            ("Let Chronoa mount disks", "mount-control-enabled",
             "Off: Chronoa can list what is mounted but cannot mount or "
             "unmount anything. Mounting runs code from a device that was not "
             "there a moment ago.",
             "manage_mount refuses while this is off"),
            ("Let Chronoa change system services", "service-control-enabled",
             "Off: Chronoa can list services and read their logs but cannot "
             "start, stop or restart one. These are root-owned units, and the "
             "wrong one can take down something another person is using.",
             "control_service refuses while this is off"),
            ("Let Chronoa edit many files at once", "bulk-edit-enabled",
             "Off: find-and-replace runs as a dry run and only lists what it "
             "would change. Writing one named file still works without this.",
             "find_and_replace refuses to write while this is off"),
            ("Let Chronoa change WiFi", "wifi-connect-enabled",
             "Off: Chronoa can list nearby networks but cannot join or leave "
             "one. Changing the connection changes what this machine can reach.",
             "connect_wifi refuses while this is off"),
        ):
            self._switch(group, label, blurb, self._read_bool(key),
                         (lambda k: (lambda a: self._set_bool(k, a)))(key),
                         tooltip=tip)

        free = self._group(
            page, "Free cloud providers",
            "Optional. These work without a key at a lower rate limit; a key raises it. "
            "Applies when the cloud fallback next activates, not to a running session.",
        )
        for label, key in (
            ("LLM7 API key", "llm7-api-key"),
            ("Kilo Gateway API key", "kilo-api-key"),
            ("BlockRun API key", "blockrun-api-key"),
        ):
            self._entry(free, label, "", config.get(key, ""),
                        lambda text, k=key: config.set(k, text.strip()), secret=True)

        byok = self._group(
            page, "Cloud providers that require a key",
            "Anthropic, OpenAI, Google and Groq all rejected an unauthenticated request "
            "when tested live, so a key here is mandatory rather than optional. Tried ahead "
            "of the free providers when set.",
        )
        for label, key in (
            ("Anthropic (Claude) API key", "anthropic-api-key"),
            ("OpenAI API key", "openai-api-key"),
            ("Google Gemini API key", "google-api-key"),
            ("Groq API key", "groq-api-key"),
        ):
            self._entry(byok, label, "", config.get(key, ""),
                        lambda text, k=key: config.set(k, text.strip()), secret=True)

    def _build_voice(self, page) -> None:
        config, app = self.app.config, self.app
        group = self._group(page, "Voice", "How Chronoa listens and speaks.")
        self._switch(group, "Wake-word activation", "Start listening without being clicked",
                     app._wake_word_active, lambda a: self._app_toggle("toggle-wake-word", a))
        self._entry(group, "Wake-word model", "openWakeWord model file",
                    config.wake_word_model, lambda t: config.set("wake-word-model", t.strip()))
        self._switch(
            group, "Speak answers aloud",
            "Read replies and timers back through Piper. The notify skill also "
            "refuses while this is off, which is why it can look like a skill "
            "that does nothing.",
            config.get_bool("notification-enabled", True),
            lambda a: self._set_bool("notification-enabled", a),
        )
        self._switch(
            group, "Interrupt while replying (barge-in)",
            "No echo cancellation, so speaker output can self-interrupt; best with headphones",
            config.barge_in_vad_enabled, lambda a: self._app_toggle("toggle-barge-in-vad", a))
        self._entry(group, "Speech language (whisper.cpp)", "e.g. en, or auto",
                    config.language, lambda t: config.set("language", t.strip()))
        self._device_picker(group, "input", "Microphone",
                            config.audio_input_device)
        self._device_picker(group, "output", "Speakers",
                            config.audio_output_device)

    def _device_picker(self, group, kind: str, title: str, current: str) -> None:
        """Choose the microphone or the speakers, from the live graph.

        The app has always honoured these two keys and resolves them against
        the graph before every capture, because `pw-record` and `pw-play`
        *silently ignore* an unknown target and use the default device instead
        - a chosen headset that is unplugged looks honoured while the recording
        comes from the laptop. All of that machinery was unreachable, because
        there was no control here to set either key.
        """
        if not pipewire.is_available():
            row = Adw.ActionRow(
                title=title,
                subtitle="No PipeWire graph to read. Connect a device and reopen this window.",
            )
            group.add(row)
            return

        try:
            devices = pipewire.list_inputs() if kind == "input" else pipewire.list_outputs()
        except Exception as exc:  # noqa: BLE001 - a broken graph must not break settings
            group.add(Adw.ActionRow(title=title, subtitle=f"Could not read devices: {exc}"))
            return

        names = [d.name for d in devices]
        # A ComboRow displays whatever its model holds, and a PipeWire node name
        # is `alsa_input.pci-0000_00_1f.3.analog-stereo` - so the model carries
        # the readable label and the node name is kept behind it. A value list
        # of raw node names would be a settings page nobody can choose from.
        labels = ["System default"] + [d.label for d in devices]
        values = [""] + names

        # Gtk.StringList, not Adw.StringList: the latter is libadwaita 1.6 and
        # this system has 1.5, so naming it would be an AttributeError on the
        # machine this actually ships to.
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
        selected = names.index(current) + 1 if current in names else 0
        row.set_selected(selected)
        if current and current not in names:
            row.set_subtitle(
                f"Saved as '{current}', which is not connected - the system "
                "default is being used until it is"
            )
        else:
            row.set_subtitle("Which device this capture and playback uses")
        row._values = values
        row.connect("notify::selected", self._on_device_selected, kind)
        group.add(row)

    def _on_device_selected(self, row, kind: str) -> None:
        index = row.get_selected()
        values = getattr(row, "_values", None)
        value = values[index] if values and 0 <= index < len(values) else ""
        self.app.config.set(f"audio-{kind}-device", value)

    def _read_bool(self, key: str) -> bool:
        # get_bool(), not get() == "true": ChronoaConfig.get() is documented
        # for string keys and returns its default for a boolean one, so the
        # comparison would be False forever and this gate would render itself
        # permanently off while the setting was on.
        try:
            return self.app.config.get_bool(key, False)
        except Exception:  # noqa: BLE001 - an absent key is simply off
            return False

    def _set_bool(self, key: str, value: bool) -> None:
        self.app.config.set(key, "true" if value else "false")
        self._refresh_sense_switches()

    def _build_models(self, page) -> None:
        config = self.app.config
        group = self._group(
            page, "Models",
            "A pin wins over the hardware tier. Blank means no pin, so the tier decides.",
        )
        self._entry(group, "Text and tool-calling model", "Blank = hardware tier decides",
                    config.model, lambda t: config.set("model", t.strip()))
        self._entry(group, "Vision model", "Blank = hardware tier decides",
                    config.vision_model, lambda t: config.set("vision-model", t.strip()))
        self._entry(group, "Whisper model (speech to text)", "Blank = hardware tier decides",
                    config.whisper_model, lambda t: config.set("whisper-model", t.strip()))
        self._entry(group, "Ollama host", "Where the local model server is",
                    config.ollama_host, lambda t: config.set("ollama-host", t.strip()))
        self._entry(group, "Piper voice", "TTS voice name",
                    config.piper_voice, lambda t: config.set("piper-voice", t.strip()))

        # What is actually in effect, and why. A tier that guesses should look
        # like a guess, not be presented as a decision.
        effective = self._group(
            page, "In effect right now",
            "Resolved by Chronoa's own resolver. A pin is your choice; anything "
            "else is the hardware tier's guess.",
        )
        for task in models.tasks():
            chosen = models.resolve(task, config=config)
            self._info_row(
                effective, task,
                f"{chosen} - {models.describe(task)}" if chosen
                else "unknown - no pin and no tier default",
            )
            effective._needle_extra[-1][0].set_tooltip_text(models.explain(task, config=config))

    def _build_system(self, page) -> None:
        config = self.app.config
        group = self._group(page, "System")
        self._switch(group, "Start on login", "Launch Chronoa when you log in",
                     config.auto_start, lambda a: self._app_toggle("toggle-auto-start", a))
        self._switch(group, "Debug logging", "Verbose logs, including tool calls",
                     config.debug_mode, lambda a: self._app_toggle("toggle-debug", a))

    # -- behaviour -----------------------------------------------------------

    def _app_toggle(self, action: str, active: bool) -> None:
        """Route a switch through the app's own action.

        The same path the keyboard shortcut uses, rather than a second one that
        can drift from it.
        """
        current = {
            "toggle-privacy": self.app.config.privacy_mode,
            "toggle-cloud-fallback": self.app.config.cloud_fallback_enabled,
            "toggle-wake-word": self.app._wake_word_active,
            "toggle-barge-in-vad": self.app.config.barge_in_vad_enabled,
            "toggle-auto-start": self.app.config.auto_start,
            "toggle-debug": self.app.config.debug_mode,
        }.get(action)
        if current is not None and bool(current) != bool(active):
            self.app.activate_action(action, None)

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        """Filter groups and rows as you type.

        A group stays visible when it matches *or* when a row inside it does,
        so a search never leaves an empty heading on screen looking like a bug -
        and a matching row inside a non-matching group is revealed rather than
        hidden behind a heading that does not contain the needle.
        """
        needle = (entry.get_text() or "").strip().lower()
        for group, haystack in self._searchable:
            group_matches = not needle or needle in haystack
            row_hits = [
                (row, text) for row, text in group._needle_extra
                if needle and needle in text
            ]
            group.set_visible(bool(group_matches) or bool(row_hits))
            for row, _text in group._needle_extra:
                # A row is visible when the group matched outright, or when it
                # is itself the hit.
                row.set_visible(bool(group_matches) or bool(row_hits and (row, _text) in row_hits))
