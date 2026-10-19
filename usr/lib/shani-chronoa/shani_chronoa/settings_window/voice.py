"""Voice: listening, the wake phrase, speech output, speed and pause, speech models and audio devices."""

import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from shani_chronoa import pipewire


def _stt_model_is_ready(config) -> str:
    """The installed STT model filename, or "" when there is none."""
    from shani_chronoa import stt_provision
    try:
        path = stt_provision.model_path(
            stt_provision.resolve_key(config.whisper_model or "base")
        )
    except Exception:  # noqa: BLE001 - an unusable row is better than a crash
        return ""
    return path.name if path.is_file() else ""


class VoicePage:
    """Voice: listening, the wake phrase, speech output, speed and pause, speech models and audio devices. - a part of SettingsWindow, which mixes it in."""


    def _set_speech_rate(self, value: float) -> None:
        self.app.config.set("speech-rate", str(value))
        if getattr(self.app, "tts", None) is not None:
            self.app.tts.rate = value

    def _hand_config_to_dispatcher(self) -> None:
        """Give the live dispatcher the window's own settings object.

        Not decoration. Two `ChronoaConfig` instances are two views of one store
        and do not see each other's writes - measured on the keyfile backend
        this suite runs on: writing `tts-pitch` through one and reading it
        through another returned 0.0 while the writer saw 3.0. So a dispatcher
        that kept the config it built for itself would ignore every setting this
        window changes until the app was restarted, and a switch that reads as
        on while nothing follows it is worse than no switch.
        """
        live = getattr(self.app, "tts", None)
        if live is not None and hasattr(live, "use_config"):
            live.use_config(self.app.config)

    def _set_piper(self, active: bool) -> None:
        """Whether Piper is in the chain, through the same path as every other
        switch - and the dispatcher is handed the new config immediately, or
        the switch would draw itself and the voice would not change until a
        restart."""
        from shani_chronoa import tts

        self._set_bool(tts.PiperTTS.PIPER_KEY, active)
        self._hand_config_to_dispatcher()

    def _set_kokoro(self, active: bool) -> None:
        """The neural-voice opt-in, through the same path as every other switch."""
        from shani_chronoa import tts

        self._set_bool(tts.PiperTTS.KOKORO_KEY, active)
        self._hand_config_to_dispatcher()

    def _set_timbre(self, key: str, value: float, row=None) -> None:
        """Save one timbre value and restate the row against what will now happen.

        The second half is the honest-degradation rule. The note depends on
        whether `sox` is installed *and* on whether a transform is set at all,
        so on a machine without it a row built with the defaults looks entirely
        ordinary and only discovers the problem when a value moves - which is
        exactly the moment the user has to be told. Nothing else in this window
        reports a setting that did not take effect.
        """
        from shani_chronoa import tts

        self.app.config.set(key, str(value))
        self._hand_config_to_dispatcher()
        if row is None:
            return
        problem = tts.PiperTTS(config=self.app.config).timbre_problem()
        base = getattr(row, "_timbre_base", row.get_subtitle() or "")
        row.set_subtitle(base + (f" Not applied: {problem}." if problem else ""))

    def _timbre_row(self, group, title: str, subtitle: str, key: str,
                    default: float, bounds, step: float) -> None:
        """One timbre slider, wired to `_set_timbre` with its own subtitle."""
        row = self._number(group, title, subtitle,
                           self.app.config.get_double(key, default),
                           *bounds, step, lambda v: self._set_timbre(key, v, row))
        # Kept on the row itself rather than in a dict beside it, the way
        # `_device_picker` keeps its own `values` on the row: it belongs to that
        # one widget and dies with it.
        row._timbre_base = subtitle

    def _build_voice(self, page) -> None:
        self._section_family = "voice"
        config, app = self.app.config, self.app
        group = self._group(page, "Voice", "How Chronoa listens and speaks.")
        self._switch(group, "Wake-word activation", "Start listening without being clicked",
                     app._wake_word_active, lambda a: self._app_toggle("toggle-wake-word", a))
        self._entry(group, "Wake phrase", "What you say to start listening, e.g. \"hey chronoa\"",
                    config.wake_phrase, self._set_wake_phrase)
        self._switch(
            # Named for both things the key does. It was "Speak answers aloud",
            # and the same key also gates desktop notifications - so the Help
            # window told people to turn on "Speak answers aloud" to send a
            # notification, which reads as a wrong answer even though it is not.
            group, "Spoken replies and notifications",
            "Read replies and timers aloud, and let Chronoa show desktop "
            "notifications. Off: replies are on screen only and timers are silent.",
            config.get_bool("notification-enabled", True),
            lambda a: self._set_bool("notification-enabled", a),
        )
        self._switch(
            group, "Talk over a reply to interrupt it",
            "No echo cancellation, so speaker output can self-interrupt; best with headphones",
            config.barge_in_vad_enabled, lambda a: self._app_toggle("toggle-barge-in-vad", a))
        model_ready = _stt_model_is_ready(config)
        self._action_button(
            group, "Download speech model now",
            "Fetch the speech model and verify it against a pinned SHA-256"
            if not model_ready else
            f"A speech model is already installed ({model_ready})",
            "download-speech-model",
            sensitive=not model_ready,
        )
        self._switch(
            group, "Download speech model on first use",
            "Fetches a whisper.cpp model from HuggingFace once, verifies it "
            "against a pinned SHA-256, and refuses it if it does not match; "
            "off by default",
            config.model_download_enabled,
            lambda a: self._app_toggle("toggle-model-download", a))
        self._switch(group, "Listen for a follow-up", "Keep the microphone open for six seconds after a spoken reply",
                     config.get_bool("follow-up-enabled", True), lambda a: self._set_bool("follow-up-enabled", a))
        self._switch(group, "Sound cues", "A short sound when listening starts or a request was not understood",
                     config.get_bool("sound-cues-enabled", True), lambda a: self._set_bool("sound-cues-enabled", a))
        # The named character, above the individual timbre sliders, because it is
        # the control most people will actually use and the sliders are the ones
        # to reach for when it is not enough.
        try:
            from shani_chronoa import voice_style
            # `get`: ChronoaConfig has no `get_string`, and the AttributeError
            # used to skip this whole row - the style choice never appeared.
            chosen = config.get("voice-style", "natural")
            row = self._choice(
                group, "Voice",
                "A whole character rather than one slider at a time. Applied in "
                "the same pass as the timbre settings below, so they add up "
                "rather than fight. “Natural” leaves the voice exactly as the "
                "engine made it.",
                voice_style.preset_names_for(), chosen,
                lambda name: config.set("voice-style", name))
            # The subtitle names what the selection will actually do, built from
            # the same function that builds the effects.
            def _describe(r, _p, _row=row):
                try:
                    style = voice_style.resolve_preset(
                        voice_style.preset_names_for()[r.get_selected()])
                    said = voice_style.describe_effects(style)
                    r.set_subtitle(
                        said or "Exactly as the voice was synthesised."
                        if voice_style.is_neutral(style) else said)
                except Exception:
                    r.set_subtitle("")
            row.connect("notify::selected", _describe)
            _describe(row, None)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "the voice style row could not be built", exc_info=True)

        self._switch(group, "Say what it is doing",
                     "A short tone when Chronoa starts and stops hearing, seeing, "
                     "speaking or acting. Off by default: it is a cue, not a "
                     "notification you asked for.",
                     config.get_bool("organ-audible-enabled", False),
                     lambda a: self._set_bool("organ-audible-enabled", a))
        self._number(group, "Speaking speed", "1.0 is normal; lower is slower",
                     config.get_double("speech-rate", 1.0), 0.5, 2.0, 0.1, self._set_speech_rate)
        self._number(group, "Pause that ends a request (seconds)",
                     "Raise it if Chronoa cuts you off mid-sentence",
                     config.get_double("end-of-speech-pause", 1.2), 0.5, 3.0, 0.1,
                     lambda v: config.set("end-of-speech-pause", str(v)))
        self._entry(group, "Speech language (whisper.cpp)", "e.g. en, or auto",
                    config.language, lambda t: config.set("language", t.strip()))
        self._device_picker(group, "input", "Microphone",
                            config.audio_input_device)
        self._device_picker(group, "output", "Speakers",
                            config.audio_output_device)
        self._build_voice_output(page)

    def _clone_rows(self, group, config) -> None:
        """The recorded-voice switch, and one removable row per agreement.

        **There is deliberately no "add a voice" control here, and that is the
        decision rather than an omission.** Registering a voice means asserting
        that a specific person agreed to be built from a specific recording, and
        a control that could be pressed by anything other than a person at the
        machine would put that assertion behind a prompt injection. So
        registration is a deliberate act with `voice_clone.register()`, and this
        panel's job is to show what has been asserted and to let it be withdrawn
        - which is the half that matters after the fact.

        The rows are read from the registry, so this cannot offer a voice nobody
        agreed to. **A removed voice disappears on the next window build** rather
        than leaving a stale row: `revoke()` deletes the record and there is no
        cached copy for the panel to be reading.
        """
        from shani_chronoa import voice_clone

        # Read through the dispatcher's own property rather than from
        # `config` directly: `cloned_voice` resolves the key and strips it, and
        # a panel that read the raw setting would have two answers to "which
        # voice is chosen" - the same two-answers problem `speaking with` above
        # avoids by asking `engine()`.
        from shani_chronoa import tts as _tts
        problem = voice_clone.clone_problem()
        self._switch(
            group, voice_clone.SWITCH_LABEL,
            "Permits building a voice out of a recording of a specific person. "
            "On its own this does nothing: a voice also has to be registered "
            "below, naming the speaker and the basis of their agreement, and "
            "that agreement is bound to the exact recording it was made for - "
            "so replacing the file makes it stop applying rather than quietly "
            "covering the new audio. "
            + (f"The engine itself cannot run here: {problem}"
               if problem else
               "The engine needs the KittenTTS runtime, which Chronoa does not "
               "package, so this switch alone will not make a voice speak."),
            config.get_bool(voice_clone.CONSENT_KEY, False),
            self._set_clone,
        )

        entries = voice_clone.voices()
        picker = Adw.ComboRow(title="Speak with which recorded voice")
        picker.set_subtitle("A person's decision, and deliberately not an argument "
                            "the model can pass: a reply steered by a document "
                            "must not be able to ask for somebody's voice.")
        # **The labels go in the model and the values live beside it**, because
        # `Gtk.StringList` is a list of strings and nothing on this build can
        # attach a value to an entry. Measured on libadwaita 1.9.1: there is no
        # `ComboRow.append`, `set_active`, `get_active_id` or `get_selected_item
        # ().get_value()`, and `Adw.ComboRowItem` is not in the introspection at
        # all - the per-item value slot the API looks like it has is not
        # reachable. `set_selected` by index is the whole of it, so the index is
        # the handle and `values` is what it indexes into.
        #
        # Kept as a pair rather than deriving one from the other because the
        # ordinary-voices entry has a label and an *empty* value, so a single
        # list cannot be both without a sentinel the model would display.
        values = [""] + [name for name, _consent in entries]
        labels = Gtk.StringList()
        labels.append("The ordinary voices")
        for name, _consent in entries:
            labels.append(name)
        picker.set_model(labels)

        # Only applied when there is something to select: an empty model
        # reports `get_selected()` as G_MAXUINT rather than 0, so the default
        # would be written as if it were a position.
        current = _tts.PiperTTS().cloned_voice
        if values:
            picker.set_selected(
                values.index(current) if current in values else 0)

        def _chosen(row) -> None:
            # Bounds-checked rather than trusted. The index comes from GTK, and
            # an empty or shrunk model answers G_MAXUINT - which is a real value
            # the picker would otherwise write into the setting as a voice name.
            index = row.get_selected()
            if 0 <= index < len(values):
                self._set_clone_voice(values[index])

        picker.connect("notify::selected", lambda r, *_: _chosen(r))
        picker.set_sensitive(bool(entries))
        group.add(picker)

        # Held so `_forget_voice` can take a row off the page. An attribute
        # rather than a closure because the Remove button outlives this call.
        self._clone_rows_built = []
        for name, consent in entries:
            row = Adw.ActionRow()
            # **Markup off, not escaped.** `Adw.ActionRow`'s title and subtitle
            # are both Pango markup, and escaping for a row that switches markup
            # off prints the entity instead - `model.py` is the recorded
            # precedent for exactly this. A speaker's name and a consent basis
            # are both free text somebody typed, so the plain path is the
            # correct one rather than the lazy one.
            row.set_use_markup(False)
            row.set_title(f"Recorded voice: {name}")
            subtitle = (
                f"{consent.speaker}, on the basis of: {consent.basis}. "
                f"Agreed on {time.strftime('%d %b %Y', time.localtime(consent.registered_at))}"
                f" for {os.path.basename(consent.clip)}.")
            # **And whether the recording is still usable.** The row named the
            # file; it did not say whether the file was there. A recording
            # that was deleted, emptied, or replaced with something enormous
            # left the row reading like an agreement that was fine.
            clip_problem = voice_clone.clip_problem(consent.clip)
            if clip_problem:
                subtitle += f" The recording cannot be used: {clip_problem}"
            row.set_subtitle(subtitle)
            remove = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
            remove.add_css_class("destructive-action")
            remove.connect("clicked", lambda _b, v=name: self._forget_voice(v))
            row.add_suffix(remove)
            group.add(row)
            self._clone_rows_built.append(row)
        if not entries:
            self._info_row(
                group, "No recorded voices",
                "None has been agreed to. Cloning a voice needs a person to "
                "record their agreement - Chronoa will not build someone's voice "
                "from a recording they did not agree to.",
            )

    def _set_clone(self, active: bool) -> None:
        """The consent switch's own writer, kept beside the row that calls it."""
        self.app.config.set("voice-clone-enabled", "true" if active else "false")

    def _set_clone_voice(self, voice: str) -> None:
        """Which registered voice to speak with, written from the picker."""
        from shani_chronoa import tts
        self.app.config.set(tts.PiperTTS.CLONE_VOICE_KEY, voice or "")

    def _forget_voice(self, voice: str) -> None:
        """Withdraw an agreement, and take its row off this page.

        The row is removed **here** rather than by rebuilding the page, because
        `VoicePage` has no rebuild hook - inventing one would be a new mechanism
        for a page that has never needed one. Revoking and then reading the gate
        in the same session is refused, since the registry is read per call and
        nothing caches a copy of it.
        """
        from shani_chronoa import voice_clone

        if not voice_clone.revoke(voice):
            return
        title = f"Recorded voice: {voice}"
        for row in list(getattr(self, "_clone_rows_built", ())):
            if row.get_title() != title:
                continue
            # **The parent is the `ListBox`, not the group.** `Adw.Preferences-
            # Group` wraps its rows, and measured on libadwaita 1.9.1 they sit
            # eleven levels below the group rather than directly inside it - so
            # `group.remove(row)` does not exist and the ListBox is the object
            # that can take one out. Stated because it looks wrong and is not:
            # the obvious `row.get_parent() is group` is the shape this file
            # already had to correct for `common.rows_of`.
            parent = row.get_parent()
            if parent is not None:
                parent.remove(row)

    def _build_voice_output(self, page) -> None:
        """Which engine speaks, and what its voice sounds like.

        A second group rather than four more rows in the one above, because the
        group above is how Chronoa listens and when it answers, and this is a
        different question. Read out of `tts.PiperTTS` rather than restated
        here: the engine name, the setting key and the SoX availability all
        have to agree with the code that acts on them, and a copy in a GTK row
        is a copy that goes stale silently.
        """
        from shani_chronoa import tts

        config = self.app.config
        # The window's own config, handed in rather than built again: the
        # dispatcher keeps whatever it is given for the life of the object, and
        # two `ChronoaConfig` instances do not see each other's writes.
        dispatcher = tts.PiperTTS(config=config)
        group = self._group(page, "Voice output",
                            "Which engine speaks, and what its voice sounds like.")
        self._info_row(
            group, "Speaking with",
            dispatcher.engine() or "no speech engine - replies are not spoken aloud",
        )
        self._switch(
            group, "Use Piper when it is installed",
            "The second voice in the chain, and the one most replies use "
            "when the neural voice above is off. Piper had no switch at all "
            "before this - only a row choosing which of its voices to use - so "
            "it could not be declined, only left uninstalled. On by default, "
            "which is what already happened. Turn it off and replies keep the "
            "lighter voice below it.",
            config.get_bool(tts.PiperTTS.PIPER_KEY, True),
            self._set_piper,
        )
        self._clone_rows(group, config)
        self._switch(
            group, "Use the Kokoro neural voice",
            "Sounds like a person where the voices below sound like a "
            "synthesiser, but it needs longer than the reply's own audio to "
            "produce it - measured at about 6 seconds for 5 seconds of audio "
            "on a four-core laptop - so on slower hardware every answer can open "
            "with seconds of silence. Its model is downloaded on first use. "
            "Turn it off and Piper below speaks instead.",
            config.get_bool(tts.PiperTTS.KOKORO_KEY, True),
            self._set_kokoro,
        )
        self._timbre_row(
            group, "Pitch (semitones)",
            "0 is the voice as synthesised; -6 lower, +6 higher. Pitch only - the "
            "reply keeps its length",
            "tts-pitch", 0.0, tts.PITCH_RANGE, 1.0)
        self._timbre_row(
            group, "Tempo",
            "1.0 is as synthesised. Faster or slower at the same pitch",
            "tts-tempo", 1.0, tts.TEMPO_RANGE, 0.1)
        self._timbre_row(
            group, "Rate (speed and pitch together)",
            "1.0 is as synthesised. Higher is shorter and higher, lower is longer "
            "and lower",
            "tts-rate", 1.0, tts.RATE_RANGE, 0.1)

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

    # -- behaviour -----------------------------------------------------------

    def _set_wake_phrase(self, text: str) -> None:
        """Save the phrase and hand it to the live listener: the next utterance uses it."""
        phrase = text.strip()
        if not phrase:
            return
        self.app.config.set("wake-phrase", phrase)
        self.app.wakeword.set_phrase(phrase)
