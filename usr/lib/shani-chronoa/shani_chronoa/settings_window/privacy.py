"""Privacy and permissions: privacy mode, cloud fallback, API keys, permission presets and every action permission."""


import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import threading

from shani_chronoa import capabilities





#: What each mode does, in one sentence, including that it lasts this run. The
#: subtitle is the only place a user reads before picking, so it has to carry
#: the whole consequence including its duration.
_MODE_SUBTITLES = {
    "default": "Each risky call asks the first time it happens. Back to Normal when Chronoa restarts.",
    "dont_ask": "Everything not already allowed is refused on the spot - no prompts. Back to Normal when Chronoa restarts.",
    "explore": "Any tool that changes something is refused, and reads still work. Back to Normal when Chronoa restarts.",
}


class PrivacyPage:
    """Privacy and permissions: privacy mode, cloud fallback, API keys, permission presets and every action permission. - a part of SettingsWindow, which mixes it in."""


    def _presets_row(self, group) -> None:
        """One choice for every action permission at once (capabilities.PRESETS); the switches below show the result."""
        names = list(capabilities.PRESETS)
        labels = [capabilities.PRESETS[n][0] for n in names] + ["Custom"]
        current = capabilities.current_preset(self.app.config)
        row = Adw.ComboRow(title="What Chronoa may change")
        row.set_model(Gtk.StringList.new(labels))
        row.set_selected(names.index(current) if current in names else len(names))
        row.set_subtitle(capabilities.PRESETS[current][1] if current in names else "Your own combination of the switches below")

        def changed(r, _pspec):
            index = r.get_selected()
            if index >= len(names):
                return
            capabilities.apply_preset(names[index], self.app.config)
            r.set_subtitle(capabilities.PRESETS[names[index]][1] + " - reopen Settings to see each switch")
        row.connect("notify::selected", changed)
        group.add(row)
        group._needle_extra.append((row, "permission preset what chronoa may change chat everyday full control"))

    def _mode_row(self, group) -> None:
        """The dispatch posture in named terms (T2.6): a session that never asks
        (DONT_ASK) or may only look (EXPLORE), not a prompt-help guessing game.

        Deliberately **not persisted**. Persisting `EXPLORE` would leave the
        assistant unable to change anything with no visible cause after a
        restart - a switch whose effect outlives it and whose label does not.
        Persisting `DONT_ASK` would make "never ask" the silent default for
        every unattended login. So the mode lasts this run and every subtitle
        says so, which is the difference between a promise and a surprise.
        """
        from shani_chronoa import permissions
        names = [permissions.Mode.DEFAULT, permissions.Mode.DONT_ASK, permissions.Mode.EXPLORE]
        labels = ["Normal", "No questions asked (everything risky is a no)",
                  "Explore first (see only, touch nothing)"]
        current = permissions.get_mode()
        row = Adw.ComboRow(title="How Chronoa treats a risky call")
        row.set_model(Gtk.StringList.new(labels))
        row.set_selected(names.index(current) if current in names else 0)
        row.set_subtitle(_MODE_SUBTITLES[current])

        def changed(r, _pspec):
            index = r.get_selected()
            if index >= len(names):
                return
            try:
                permissions.set_mode(names[index])
            except ValueError:  # the model is a fixed list; this is a bug, not a user error
                r.set_selected(0)
                return
            r.set_subtitle(_MODE_SUBTITLES[names[index]])
        row.connect("notify::selected", changed)
        group.add(row)
        group._needle_extra.append((row, "permission mode dont_ask explore no questions refused look but do not touch"))

    # -- sections ------------------------------------------------------------

    def _build_privacy(self, page) -> None:
        self._section_family = "privacy"
        config = self.app.config
        group = self._group(
            page, "Privacy and network",
            "Privacy mode is the master switch: with it on, Chronoa keeps speech, "
            "screen and sensed data on this machine and sends nothing to a cloud provider.",
        )
        self._presets_row(group)
        self._mode_row(group)
        self._switch(group, "Privacy mode (local only)", "Master switch for leaving this machine",
                     config.privacy_mode, lambda a: self._app_toggle("toggle-privacy", a))
        self._switch(
            # Not "when Ollama is unavailable": the gate is the configured local
            # brain answering (`self.llm.is_available()`), and that is llama.cpp
            # by default now - so the old label named a program most installs
            # never run.
            group, "Cloud fallback when no local model answers",
            "Separate opt-in on purpose - this never turns on from one switch alone",
            config.cloud_fallback_enabled,
            lambda a: self._app_toggle("toggle-cloud-fallback", a))

        # **Two switches, not one, and not `cloud-fallback-enabled`.** Turning
        # the cloud fallback on says prompts may leave this machine as text. It
        # says nothing about sending a *recording*, and one switch cannot express
        # that difference - so somebody happy with a text fallback is not thereby
        # opted into uploading their voice.
        self._switch(
            group, "Cloud speech recognition",
            "Off: with no local speech model installed, Chronoa cannot listen at "
            "all. On: recordings are uploaded to a provider to be transcribed. "
            "Needs an API key - no provider accepts an anonymous recording.",
            self._read_bool("cloud-stt-enabled"),
            lambda a: self._set_bool("cloud-stt-enabled", a),
            tooltip="cloud_voice.CloudSTT - re-checked on every recording, so "
                    "turning privacy mode on mid-dictation stops the next one",
        )
        self._switch(
            group, "Cloud speech synthesis",
            "Off: replies are spoken by Kokoro, Piper, RHVoice or espeak-ng, all "
            "on this machine. On: if none of those can speak, the reply is sent "
            "to a provider to be spoken. Needs an API key.",
            self._read_bool("cloud-tts-enabled"),
            lambda a: self._set_bool("cloud-tts-enabled", a),
            tooltip="cloud_voice.CloudTTS - the LAST link of the voice chain, so "
                    "turning this on cannot displace a local voice",
        )

        # **The two switches above say "needs an API key" and stop there.**
        # `cloud_voice.probe_capabilities()` is what answers the next question -
        # *which* provider, for speech in and for speech out - and it had zero
        # callers, so the measured table was a fact inside a maintenance function
        # nobody could reach. It is a **button and not a row**, because it makes
        # real requests to up to five providers with a 30 s timeout each, and a
        # probe that can take two minutes cannot run while the window is drawn.
        probe = Gtk.Button(label="Check which providers can do speech",
                           valign=Gtk.Align.CENTER)
        probe.connect("clicked", lambda b: self._probe_cloud_speech(b))
        row = Adw.ActionRow(
            title="What those two switches can actually reach",
            subtitle="Cloud speech is not one service. Press the button to "
                     "re-measure it on this machine, now.",
            activatable=False)
        row.add_suffix(probe)
        group.add(row)
        group._needle_extra.append((row, "cloud speech which providers can do speech check"))

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
            ("Let Chronoa install and remove apps", "app-install-enabled",
             "Off: Chronoa can search Flathub and list your apps, but cannot install "
             "or remove one. Installs are per user and need an exact app ID.",
             "install_app refuses while this is off"),
            ("Let Chronoa suspend, restart or shut down", "power-control-enabled",
             "Off: Chronoa cannot suspend, restart or shut down. On, restart and "
             "shut down wait one minute and can be cancelled.",
             "power_action refuses while this is off"),
            ("Let Chronoa close windows", "window-close-enabled",
             "Off: Chronoa can list and focus windows but cannot ask one to "
             "close, which can discard unsaved work.",
             "close_window refuses while this is off"),
            ("Let Chronoa mount disks", "mount-control-enabled",
             "Off: Chronoa can list what is mounted but cannot mount or "
             "unmount anything. Mounting runs code from a device that was not "
             "there a moment ago.",
             "manage_mount refuses while this is off"),
            ("Let Chronoa change when the screen blanks", "idle-timeout-enabled",
             "Off: Chronoa can report the current idle timeout and whether the "
             "screen locks, but cannot change either. Its own key rather than "
             "sharing the lock skill's, because whether the machine locks itself "
             "is a standing policy decision, not a one-off action.",
             "set_screensaver refuses while this is off"),
            ("Let Chronoa hold the machine awake", "sleep-inhibit-enabled",
             "Off: Chronoa can report what is holding the machine awake, but cannot "
             "take a hold of its own. Every hold is bounded and lapses on its own, "
             "so turning this on cannot leave the machine unable to sleep.",
             "set_sleep_inhibit refuses while this is off"),
            ("Let Chronoa change the desktop look", "appearance-control-enabled",
             "Off: Chronoa can report whether the desktop is set to light or dark "
             "but cannot change it. Restyling a desktop unasked, mid-document, is "
             "disruptive in a way that reading it is not.",
             "set_theme refuses to change it while this is off"),
            ("Let Chronoa change the timezone", "timezone-control-enabled",
             "Off: Chronoa can report the current timezone and list what is "
             "available, but cannot change it. It is a system-wide change that "
             "moves every timestamp at once, and it needs root.",
             "set_timezone refuses while this is off"),
            ("Let Chronoa change which apps open files", "default-apps-enabled",
             "Off: Chronoa can report which app opens a kind of file, and the "
             "default browser, but cannot change them. A change alters what "
             "every later double-click opens.",
             "default_apps refuses to change a default while this is off"),
            ("Let Chronoa cancel print jobs", "print-control-enabled",
             "Off: Chronoa can show the print queue but cannot cancel a job. A "
             "cancelled job cannot be resumed.",
             "print_queue refuses to cancel while this is off"),
            ("Let Chronoa rename this computer", "hostname-control-enabled",
             "Off: Chronoa can report this computer's name but cannot change it. "
             "Other devices on the network see the new name.",
             "set_hostname refuses while this is off"),
            ("Let Chronoa change the language and formats", "locale-control-enabled",
             "Off: Chronoa can report the system language and date, number and "
             "money formats, but cannot change them. A change applies to every "
             "program from the next login.",
             "set_locale refuses to change while this is off"),
            ("Let Chronoa run speed tests", "speed-test-enabled",
             "Off: Chronoa cannot measure your connection speed. A test moves "
             "several megabytes to Cloudflare, which costs money on a metered "
             "connection. Privacy mode refuses it regardless.",
             "speed_test refuses while this is off"),
            ("Let Chronoa switch Bluetooth", "bluetooth-control-enabled",
             "Off: Chronoa can report which Bluetooth devices are paired and "
             "whether the adapter is on, but cannot turn it off. Separate from "
             "the bluetooth sense's own permission, because noticing a headset "
             "and agreeing to have it disconnected mid-call are different "
             "things.",
             "toggle_bluetooth refuses to switch while this is off"),
            ("Let Chronoa mute the microphone", "mic-control-enabled",
             "Off: Chronoa can report whether the microphone is muted but "
             "cannot change it. Output volume and mute need no such permission; "
             "this covers the input side, which is the more privacy-relevant of "
             "the two.",
             "set_mic_mute refuses while this is off"),
            ("Let Chronoa lock this session", "screen-lock-enabled",
             "Off: Chronoa cannot lock the screen. Its own permission rather "
             "than sharing input control, because it does not act on the "
             "interface - it ends the session's access to the machine.",
             "lock_screen refuses while this is off"),
            ("Let Chronoa switch airplane mode", "radio-control-enabled",
             "Off: Chronoa can say which radios are on but cannot switch them.",
             "airplane_mode refuses to switch while this is off"),
            ("Keep Chronoa's automatic rules running in the background", "background-mode-enabled",
             "Off: rules and senses stop when the window closes. On, a background service keeps them "
             "running (never the microphone).",
             "the background service stays disabled while this is off"),
            ("Talk to Chronoa with a keyboard shortcut", "global-shortcut-enabled",
             "Off: start listening with the microphone button or the wake phrase only. On, Chronoa asks "
             "the desktop for a system-wide shortcut (Ctrl+Alt+Space by default) the next time it starts.",
             "no shortcut is requested while this is off"),
            ("Let Chronoa use your paired phone", "phone-control-enabled",
             "Off: Chronoa cannot see, ring or send anything to your phone.",
             "phone refuses while this is off"),
            ("Let Chronoa read your phone's text messages", "phone-messages-read-enabled",
             "Off: Chronoa can still see, ring and ping your phone, but not read what anyone said to you. "
             "This is a separate permission on purpose - knowing a phone is paired and can be rung is "
             "nothing like reading your messages.",
             "reading messages is refused while this is off"),
            ("Let Chronoa send texts and place calls from your phone", "phone-messages-send-enabled",
             "Off: Chronoa can read your messages but cannot send anything or open the dialler. A message "
             "leaves this machine and appears on the other end, so it needs its own agreement. Chronoa "
             "drafts the text and asks you before anything goes.",
             "sending a message or calling is refused while this is off"),
            ("Let Chronoa call through your paired phone", "bluetooth-call-enabled",
             "Off: Chronoa cannot place, answer or end calls, and cannot hear a call through this "
             "computer. Your phone stays the radio either way - this is about who can start a call. "
             "Chronoa asks you to confirm before it places one.",
             "placing, answering or ending a call is refused while this is off"),
            ("Let Chronoa read your Bluetooth devices", "bluetooth-gatt-enabled",
             "Off: Chronoa can still list your paired devices and connect to them, but cannot ask "
             "one what it says about itself - no watch battery, no heart-rate reading, no firmware "
             "version. Its own switch because on a wearable those are facts about whoever is wearing "
             "it, which is not the same agreement as being willing to have a headset disconnected.",
             "reading a device is refused while this is off"),
            ("Let Bluetooth remotes control music here", "bluetooth-media-remote-enabled",
             "Off: a headset's or watch's play and skip buttons do nothing on this computer. On: "
             "paired devices control whatever is playing here.",
             "Bluetooth media control is off while this is off"),
            ("Let my phone pair this computer as a Bluetooth keyboard", "phone-remote-enabled",
             "Off: this computer is not discoverable as a keyboard. On: it advertises as 'Chronoa "
             "Remote'; pair it from the phone once, and Chronoa can press the phone's camera shutter, "
             "media and volume keys and type text on it. Nearby phones can see it while this is on.",
             "the phone remote is refused while this is off"),
            ("Keep my watch connected to this computer", "watch-companion-enabled",
             "Off: the watch is reached only when you ask something of it. On: Chronoa keeps it "
             "connected so find-my-phone rings here, its music and camera buttons work with this "
             "computer and the song playing shows on it. The watch takes one connection, so while "
             "this is on the Da Fit app on your phone cannot reach it.",
             "the watch companion stays off while this is off"),
            ("Show what this computer is playing on my watch", "watch-nowplaying-enabled",
             "Off (the default): nothing is pushed to the watch. On: the current track is sent every "
             "five seconds so it appears on the watch's music screen. Only while the companion above "
             "is on, and it does not affect the watch's music buttons, which keep working either way.",
             "what is playing is not pushed to the watch while this is off"),
            ("Send this computer's weather to my watch", "watch-weather-enabled",
             "Off (the default): the watch's weather request is not answered. On: the forecast for your "
             "home place is sent, including the week ahead. This reads a location and reaches a weather "
             "service; the watch asks on every connection, so it is off until you want it.",
             "the watch's weather request is not answered while this is off"),
            ("Let Chronoa tune an FM radio", "fm-radio-enabled",
             "Off: Chronoa cannot tune a USB radio receiver (an RTL-SDR dongle), scan the FM band or "
             "play a station. A laptop has no FM radio of its own; without a dongle this is reported "
             "as having no receiver.",
             "tuning the FM radio is refused while this is off"),
            ("Let Chronoa read and write NFC tags", "nfc-enabled",
             "Off: Chronoa cannot read a tag tapped against a reader here, nor put a link on a "
             "sticker. Its own switch because an NFC tag carries whatever somebody chose to "
             "write on it - and tags sit in public places, so reading one is often reading a "
             "stranger's link. Writing is narrower still: only NFC Forum Ultralight stickers, "
             "never a bank card or a transit pass, which rewriting would break. Most laptops have "
             "no NFC reader at all, and that is reported as having none.",
             "reading or writing an NFC tag is refused while this is off"),
            ("Let Chronoa act on your phone connecting", "phone-sense-enabled",
             "Off: an automatic rule cannot trigger on your phone connecting or running low.",
             "a phone trigger is refused while this is off"),
            ("Let Chronoa name a sound when you ask", "heard-sound-sense-enabled",
             "Off: Chronoa will not listen when asked what it hears. On, it records a few seconds at a time, "
             "names what it heard and keeps none of the audio.",
             "the heard-sound sense is refused while this is off"),
            ("Let an automatic rule listen for sounds like the doorbell", "sound-sense-enabled",
             "Off: an automatic rule cannot trigger on a sound. On, a rule records a few seconds at a time and "
             "keeps only what was recognised.",
             "a sound trigger is refused while this is off"),
            ("Let Chronoa read your calendar", "calendar-read-enabled",
             "Off: Chronoa cannot tell you what is on your calendar.",
             "calendar_events refuses while this is off"),
            ("Let Chronoa change your calendar", "calendar-write-enabled",
             "Off: Chronoa can read your calendar but cannot add, move or cancel anything. "
             "Moving and cancelling also need the switch above, because finding the event to "
             "change it is the reading half.",
             "calendar_edit refuses while this is off"),
            ("Let Chronoa act before calendar events", "calendar-sense-enabled",
             "Off: an automatic rule cannot trigger before a calendar event starts.",
             "a calendar trigger is refused while this is off"),
            ("Let Chronoa search inside your files", "document-search-enabled",
             "Off: Chronoa can find files by name but not by what is in them. On, it asks the "
             "desktop's own search index - it reads nothing itself.",
             "search_documents refuses while this is off"),
            ("Let Chronoa empty the trash", "trash-empty-enabled",
             "Off: Chronoa can list what is in the trash but cannot empty it. "
             "Not the same permission as deleting files: the trash is already "
             "recoverable, and emptying it is what makes it not.",
             "empty_trash refuses while this is off"),
            ("Let Chronoa change system services", "service-control-enabled",
             "Off: Chronoa can list services and read their logs but cannot "
             "start, stop or restart one. These are root-owned units, and the "
             "wrong one can take down something another person is using.",
             "control_service refuses while this is off"),
            ("Let Chronoa edit many files at once", "bulk-edit-enabled",
             "Off: find-and-replace runs as a dry run and only lists what it "
             "would change. Writing one named file still works without this.",
             "find_and_replace refuses to write while this is off"),
            ("Let Chronoa record commits and branches", "git-write-enabled",
             "Off: Chronoa can still report what changed in a repository, the diff, "
             "and how far the branch is from its upstream - it just cannot record "
             "anything. On, it commits only the files you name: nothing is swept "
             "up from the rest of the tree, because a sweep turns a bounded "
             "request into an unbounded one. A commit is undoable with git reset.",
             "git_commit and git_branch refuse while this is off"),
            ("Let Chronoa push a branch to a remote", "git-push-enabled",
             "Off by default and separate from recording commits, because a local "
             "commit you regret is one reset away while a pushed one may already be "
             "in somebody's history. On, Chronoa asks before every push - a "
             "standing yes does not carry over - and the remote must be one the "
             "repository already has and must be named. Force-pushing is refused.",
             "git_push refuses to push while this is off"),
            ("Let Chronoa clear caches and unused Flatpak runtimes", "cleanup-enabled",
             "Off: Chronoa still reports what is taking the space and the safe "
             "way to free it, but you clear it yourself. Turning this on lets it "
             "empty your cache folder - apps rebuild what they need, so do it "
             "with them closed - and remove Flatpak runtimes no app uses. It "
             "cannot trim the system log: that needs administrator rights, which "
             "Chronoa does not ask for from inside a tool call.",
             "cleanup_apply refuses to remove anything while this is off"),
            ("Let Chronoa change WiFi", "wifi-connect-enabled",
             "Off: Chronoa can list nearby networks but cannot join or leave "
             "one. Changing the connection changes what this machine can reach.",
             "connect_wifi refuses while this is off"),
            # Beside WiFi, and worded so the difference is the point: that one
            # changes which network this machine joins, this one builds a new
            # one. It also needs your machine administrator's password, which
            # joining a WiFi network does not - so it is a separate agreement
            # rather than a wider version of the switch above.
            ("Let Chronoa build lab networks", "network-provision-enabled",
             "Off: Chronoa cannot create or remove the isolated lab networks it "
             "builds out of network namespaces. On: it still shows you the exact "
             "plan before changing anything, still needs your password to apply "
             "it, and can only ever remove networks it built itself. Nothing "
             "reaches beyond this machine - every address is private.",
             "lab_network_create and lab_network_destroy refuse while this is off"),
            # Its own row, and the wording is the point: the network sense and
            # interface_counters read the kernel's *counters*, this reads what is
            # inside the packets. Putting it under the network row would read as
            # "it already knows about the network", which is exactly the
            # misunderstanding that matters here.
            ("Let Chronoa watch network packets", "packet-capture-enabled",
             "Off: Chronoa cannot watch packets crossing an interface. This is "
             "the one permission that reads what is inside network traffic "
             "rather than just counting it - most of what crosses an encrypted "
             "browser session is unreadable, but anything sent in the clear is "
             "visible. On: it still reports summaries only (who sent to whom, "
             "which protocol, how large), never the contents, and every capture "
             "stops on its own after a set number of packets or seconds.",
             "capture_packets refuses while this is off"),
            # The Help window names a switch for every gate it reports as off
            # ("Off. Switch on “…” in Settings to use this"), so a gate with no
            # row here is a promise the settings window does not keep - the user
            # is sent to a switch that does not exist and the skill stays
            # unreachable with no way to enable it. The gates skills and
            # triggers.py read had no row at all; the senses sharing this table
            # get theirs from the generated Senses group above, which is why a
            # grep of this file alone cannot find the missing ones.
            ("Let Chronoa edit your files", "file-edit-enabled",
             "Off: Chronoa can read and search your files but cannot write to "
             "them. Turning this on lets a tool call change your work.",
             "edit_file and undo_last_change refuse while this is off"),
            ("Let Chronoa compile a kernel module", "driver-build-enabled",
             "Off: Chronoa can write a driver's source for you but cannot hand a "
             "compiler your files. A built module is still neither installed nor "
             "loaded - this layout has nowhere to keep one, and an unsigned one is "
             "refused when Secure Boot is on.",
             "driver_build refuses while this is off"),
            ("Let Chronoa write to a device", "i2c-write-enabled",
             "Off: Chronoa can read device registers but cannot change them. "
             "Reading a sensor is ordinary inspection; writing to the wrong "
             "register can erase an EEPROM's stored calibration for good.",
             "device_i2c refuses every write while this is off"),
            ("Let Chronoa keep a task list", "todo-list-enabled",
             "Off: Chronoa cannot keep a to-do list between turns.",
             "todo_list refuses while this is off"),
            ("Let Chronoa keep multi-step goals", "goals-enabled",
             "Off: Chronoa cannot save a plan to run later. Saving runs nothing; "
             "each step asks when it is run from the Goals panel.",
             "manage_goals refuses while this is off"),
            ("Let Chronoa arm automatic rules", "trigger-control-enabled",
             "Off: Chronoa cannot create rules that act on their own, such as "
             "running something when a file changes.",
             "manage_triggers refuses while this is off"),
            ("Let Chronoa watch files for changes", "fswatch-sense-enabled",
             "Off: an automatic rule cannot trigger on a file being written.",
             "a fswatch trigger is refused while this is off"),
            ("Let Chronoa act on system failures", "failure-sense-enabled",
             "Off: an automatic rule cannot trigger on a service failing.",
             "a failure trigger is refused while this is off"),
            ("Let Chronoa watch stored deadlines", "expiry-sense-enabled",
             "Off: an automatic rule cannot trigger when a stored deadline "
             "passes.",
             "an expiry trigger is refused while this is off"),
            ("Let Chronoa act on container runs", "containerrun-sense-enabled",
             "Off: an automatic rule cannot trigger on a container starting or "
             "stopping.",
             "a container-run trigger is refused while this is off"),
            ("Let Chronoa watch system units", "unithealth-sense-enabled",
             "Off: an automatic rule cannot trigger on a systemd unit changing "
             "state.",
             "a unit-health trigger is refused while this is off"),
            ("Let Chronoa act when the screen locks", "screenlock-sense-enabled",
             "Off: an automatic rule cannot trigger on the screen locking or unlocking.",
             "a screenlock trigger is refused while this is off"),
            ("Let Chronoa act on power changes", "powerstate-sense-enabled",
             "Off: an automatic rule cannot trigger on unplugging, plugging in or a battery level.",
             "a powerstate trigger is refused while this is off"),
            ("Let Chronoa act on network changes", "netstate-sense-enabled",
             "Off: an automatic rule cannot trigger on the network going up or down.",
             "a netstate trigger is refused while this is off"),
            ("Let Chronoa act on USB devices", "usbplug-sense-enabled",
             "Off: an automatic rule cannot trigger on a USB device being plugged in.",
             "a usbplug trigger is refused while this is off"),
            ("Let Chronoa act on Bluetooth devices", "btconnect-sense-enabled",
             "Off: an automatic rule cannot trigger on a Bluetooth device connecting.",
             "a btconnect trigger is refused while this is off"),
            ("Let Chronoa act on a schedule", "schedule-sense-enabled",
             "Off: an automatic rule cannot run at a set time.",
             "a schedule trigger is refused while this is off"),
            ("Let Chronoa act when the machine wakes", "sleepwake-sense-enabled",
             "Off: an automatic rule cannot trigger on the machine waking from sleep.",
             "a sleepwake trigger is refused while this is off"),
            ("Let Chronoa act on audio devices", "audiodevice-sense-enabled",
             "Off: an automatic rule cannot trigger on headphones or a microphone being connected.",
             "a audiodevice trigger is refused while this is off"),
            ("Let Chronoa act on log messages", "journalmatch-sense-enabled",
             "Off: an automatic rule cannot trigger on a log message.",
             "a journalmatch trigger is refused while this is off"),
            ("Let Chronoa watch system properties", "dbusprop-sense-enabled",
             "Off: an automatic rule cannot trigger on a system property changing.",
             "a dbusprop trigger is refused while this is off"),
            ("Restrict tools to a seccomp sandbox", "sandbox-seccomp-enabled",
             "Off: tools run without a seccomp filter. Turning this on drops "
             "the syscalls a tool may make, at the cost of some tools refusing "
             "to run.",
             "the sandbox executor adds the seccomp filter when this is on"),
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
            self._entry(free, label, "", config.api_key_value(key),
                        lambda text, k=key: config.set_api_key(k, text.strip()), secret=True)

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
            ("OpenRouter API key (free models work with a free key)", "openrouter-api-key"),
            ("OpenCode Zen API key", "opencode-zen-api-key"),
        ):
            self._entry(byok, label, "", config.api_key_value(key),
                        lambda text, k=key: config.set_api_key(k, text.strip()), secret=True)

        # **A model server of your own, which had no field anywhere.** The three
        # keys are in the schema, `app/brain.py` puts the endpoint first in the
        # cloud chain when it is set, and the "Answering now" panel displays it -
        # but nothing in the UI could set it, so LM Studio, vLLM, llama-server on
        # another machine or a company gateway were reachable only by
        # `gsettings set` (found by auditing schema keys against UI code,
        # 2026-10-08). Same gates as the rest of this page: privacy mode off and
        # the cloud fallback on.
        custom = self._group(
            page, "Your own model server",
            "Any OpenAI-compatible server - LM Studio, vLLM, llama-server on another "
            "machine. Tried first when the cloud fallback is on.",
        )
        self._entry(custom, "Server address", "e.g. http://192.168.1.20:8080/v1",
                    config.get("custom-llm-base-url", ""),
                    lambda text: config.set("custom-llm-base-url", text.strip()))
        self._entry(custom, "Model name", "as the server lists it",
                    config.get("custom-llm-model", ""),
                    lambda text: config.set("custom-llm-model", text.strip()))
        self._entry(custom, "API key", "only if the server asks for one",
                    config.api_key_value("custom-llm-api-key"),
                    lambda text: config.set_api_key("custom-llm-api-key", text.strip()),
                    secret=True)

        # Weather needs a place. Without GPS or GeoClue (measured on the dev box:
        # neither installed) "what's the weather" and the watch's weather screen
        # had nothing to look up, so a named place stands in.
        home = self._group(page, "Home place",
                           "Used for weather when this computer cannot locate itself.")
        self._entry(home, "Town or city", "e.g. Pune", config.get("home-place", ""),
                    lambda text: config.set("home-place", text.strip()))

        # **The inbound channel, which had no switch at all.** `gateway.py` is
        # complete, `_export_gateways()` runs at startup - and measured with an
        # AST search, nothing in the tree ever called `Registry.register()`, so
        # `names()` was always empty and nothing was ever exported. A feature
        # with no way to turn it on. Appended last so every switch above keeps
        # the position a person already knows it in.
        self._gateway_rows(group)

    def _probe_cloud_speech(self, button: Gtk.Button) -> None:
        """Re-measure which providers really have the audio routes, live.

        Off the main loop: five providers at up to 30 s each. Every verdict is
        kept distinct - "I could not ask" and "it does not have one" are different
        answers, and collapsing them is what made the table wrong three times
        before it was right.
        """
        button.set_sensitive(False)

        def work(report) -> None:
            from shani_chronoa import cloud_voice
            report("Asking the providers which speech routes they have...")
            try:
                found = cloud_voice.probe_capabilities()
            except Exception as exc:  # noqa: BLE001 - a failed probe is a report
                report(f"Could not measure: {type(exc).__name__}")
                return
            report(_capability_sentence(found))
            button.set_sensitive(True)

        threading.Thread(
            target=lambda: _probe_worker(work, button), daemon=True).start()

    def _gateway_rows(self, group) -> None:
        """The `gateways` entry, and a live row of what is actually listening.

        The status row is not decoration. The whole failure here was a setting
        that could be set and still do nothing, so the row that answers "what did
        Chronoa make of what I typed?" is the point of the section - and it names
        the entries that were **ignored**, because an entry the parser refused is
        the case a person would otherwise debug for an hour.
        """
        def changed(text):
            self._set_string("gateways", text)
            app = getattr(self, "app", None)
            if app is not None and hasattr(app, "_reload_gateways"):
                # Live, not on restart: a channel you just named should work now.
                app._reload_gateways()
            self._refresh_gateway_status()

        self._entry(
            group, "Inbound channels",
            "Comma-separated names, each optionally ':execute'. Empty means "
            "nothing is listening on the session bus at all.",
            self._read_string("gateways", ""), changed)
        status = Adw.ActionRow(title="Listening for",
                               subtitle="nothing is on the bus")
        status.set_subtitle_selectable(True)
        group.add(status)
        group._needle_extra.append((status, "inbound channels listening bus"))
        self._gateway_status = status
        self._refresh_gateway_status()

    def _refresh_gateway_status(self) -> None:
        """Say what is registered, and what was ignored."""
        from shani_chronoa import gateway as gateway_module
        row = getattr(self, "_gateway_status", None)
        if row is None:
            return
        entries, errors = gateway_module.parse_config(
            self._read_string("gateways", ""))
        text = gateway_module.describe(entries)
        app = getattr(self, "app", None)
        registry = getattr(app, "_gateways", None) if app is not None else None
        registered = registry.names() if registry is not None else []
        if registered:
            text = (f"{gateway_module.describe(entries)} - on the bus as "
                    f"{', '.join(registered)}")
        if errors:
            text += f". Ignored: {'; '.join(errors)}"
        row.set_subtitle(text)

def _probe_worker(work, button: Gtk.Button) -> None:
    """Run probe `work` on this thread and put every report on the button."""
    def report(text: str) -> None:
        GLib.idle_add(lambda: (setattr(button, "label", str(text)[:90]), False)[1])
    try:
        work(report)
    except Exception as exc:  # noqa: BLE001 - a worker must still say something
        report(f"Failed: {type(exc).__name__}")
        GLib.idle_add(lambda: (button.set_sensitive(True), False)[1])


def _capability_sentence(found: dict) -> str:
    """The measured table, as one readable line.

    **Read against the real shape.** `probe_capabilities()` returns
    `{pid: {"base_url", "stt": {...}, "tts": {...}}}` where each side carries
    `{"verdict", "status", "detail"}` and the verdicts are **`needs-key` and
    `needs-paid`, hyphenated**. My first renderer invented keys of my own
    (`stt_yes`, `needs_key`) and would have printed "none" for everything while
    looking like a measurement - which is the failure this page exists to catch.

    All five verdicts are kept apart. "I could not ask" and "it does not have
    one" are different answers: the first is a network problem, the second a fact
    about the provider.
    """
    yes_in, yes_out, keyed, paid, unreachable, absent = [], [], [], [], [], []
    for pid in sorted(found or {}):
        entry = found[pid] or {}
        for side, bucket in (("stt", yes_in), ("tts", yes_out)):
            verdict = str((entry.get(side) or {}).get("verdict") or "unknown")
            if verdict == "yes":
                bucket.append(pid)
            elif verdict == "needs-key":
                keyed.append(pid)
            elif verdict == "needs-paid":
                paid.append(pid)
            elif verdict == "unreachable":
                unreachable.append(pid)
            else:
                absent.append(f"{pid}/{side}")
    def listed(rows: list) -> str:
        return ", ".join(rows) if rows else "none"
    return (f"Speech in: {listed(yes_in)}. Speech out: {listed(yes_out)}. "
            f"Needs a key: {listed(sorted(set(keyed)))}. "
            f"Needs payment: {listed(sorted(set(paid)))}. "
            f"Could not be asked: {listed(sorted(set(unreachable)))}. "
            f"No such route: {listed(sorted(set(absent)))}.")
