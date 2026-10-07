"""Voice: listening, the wake phrase, speech output, speed and pause, speech models and audio devices."""


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
                     config.get_bool("follow-up-enabled", False), lambda a: self._set_bool("follow-up-enabled", a))
        self._switch(group, "Sound cues", "A short sound when listening starts or a request was not understood",
                     config.get_bool("sound-cues-enabled", False), lambda a: self._set_bool("sound-cues-enabled", a))
        # The named character, above the individual timbre sliders, because it is
        # the control most people will actually use and the sliders are the ones
        # to reach for when it is not enough.
        try:
            from shani_chronoa import voice_style
            chosen = config.get_string("voice-style", "natural")
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
            group, "Use the Kokoro neural voice",
            "Off by default: it sounds like a person where the voices below sound "
            "like a synthesiser, but it needs longer than the reply's own audio to "
            "produce it, so every answer would open with seconds of silence. It "
            "also needs its model, downloaded from Settings.",
            config.get_bool(tts.PiperTTS.KOKORO_KEY, False),
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
