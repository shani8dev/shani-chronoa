"""The voice loop: listening, the wake phrase, transcription, speaking replies sentence by sentence, barge-in and follow-ups."""

import asyncio
import logging
import os
import threading
import time
from typing import Optional

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gio', '2.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib  # type: ignore



from shani_chronoa import markdown_lite, speech, cues
from shani_chronoa import speech_gate, stt, stt_provision
from shani_chronoa.gui import AssistantState


logger = logging.getLogger(__name__)

def _is_echo(heard: str, reply: str) -> bool:
    """Whether a transcript is mostly the assistant's own reply picked up by the microphone."""
    import re as _re
    words = _re.findall(r"[a-z0-9']+", heard.lower())
    said = set(_re.findall(r"[a-z0-9']+", reply.lower()))
    return bool(words) and sum(w in said for w in words) / len(words) >= 0.8


#: How long an ordinary spoken turn may run before it is cut off, and how long
#: of silence ends one.
#:
#: **Twenty seconds is right for a question and wrong for a sentence of dictation,
#: and that difference is the whole of dictation.** A turn is meant to be "what
#: time is it", so a cap that stops a rambling question is a feature. But talking
#: *at* the machine - a paragraph of notes, a message to be turned into text, a
#: meeting - needs minutes, and 20 seconds cuts it in the first sentence. So the
#: two are separate ceilings rather than one compromise number, chosen when the
#: turn starts.
TURN_SECONDS = 20.0
DICTATION_SECONDS = 300.0

#: Silence that ends a turn. A question ends at a natural pause; a paragraph of
#: speech has pauses inside it, and 1.2s would cut every one of them.
TURN_SILENCE_SECONDS = 1.2
DICTATION_SILENCE_SECONDS = 2.5


class VoiceMixin:
    """The voice loop: listening, the wake phrase, transcription, speaking replies sentence by sentence, barge-in and follow-ups. - a part of ChronoaApplication, which mixes it in."""


    def _end_listening_now(self) -> None:
        if self._listening:
            self.recorder.cancel_auto_stop()

    def _stt_backend(self) -> str:
        """The configured STT backend, tolerating a config without the key.

        `stubbed_app`-style configs and any older caller that predates the
        `stt-backend` gsetting have no such attribute. `getattr` rather than
        `self.config.stt_backend` because a missing key must mean "the
        behaviour that predates it", never an AttributeError at startup.
        """
        return getattr(self.config, "stt_backend", "whisper") or "whisper"

    def _stt_backend_label(self) -> str:
        """A human name for the backend, for status text and log lines.

        **From the object, not from the setting.** The configured backend is only
        a hint about what will be *tried*; `_build_stt` may hand back a cloud
        engine because nothing local is installed, and this line used to report
        "Whisper.cpp" on such a machine - naming a program that was never run.
        """
        live = getattr(self, "stt", None)
        own = getattr(live, "label", None)
        if own:
            return str(own)
        return "Parakeet" if self._stt_backend() == stt.BACKEND_PARAKEET \
            else "Whisper.cpp"

    def _build_stt(self):
        """Construct the STT for the configured backend.

        The single place a backend is chosen, shared by startup and by the
        post-download rebuild - so the model a download installs is the model
        the next utterance reads, whichever engine was selected.
        """
        # An explicit `whisper-model` is honoured as chosen. Only the
        # auto-selected one falls back to a model that is actually installed.
        model = self.config.whisper_model or stt.installed_model(self.hardware.get_whisper_model())
        if self._stt_backend() == stt.BACKEND_PARAKEET:
            model = stt_provision.PARAKEET_DEFAULT_MODEL
        local = stt.build_stt(
            model=model,
            language=self.config.language,
            backend=self._stt_backend(),
        )
        # **Local first, cloud only when local cannot listen.** The same shape as
        # `_maybe_enable_cloud_fallback` for the LLM, and the reason it is a
        # *selection* rather than a setting: installing a local model later must
        # take precedence without anybody turning a switch back off, so the
        # choice is re-made by `_build_stt` on the next utterance rather than
        # latched at startup.
        #
        # `CloudSTT.is_available()` is the whole gate - switch on, privacy off,
        # and a configured provider that actually has the route - and it is
        # re-read on every `transcribe()` as well, because a microphone stays
        # open long enough for privacy mode to be turned on mid-dictation.
        if not local.is_available():
            from shani_chronoa import cloud_voice
            cloud = cloud_voice.CloudSTT(language=self.config.language)
            if cloud.is_available():
                logger.info("No local speech-recognition model; using a cloud "
                            "provider, which means recordings are uploaded")
                return cloud
        return local

    def _toggle_listening(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle speech listening mode (the orb button / its accelerator).

        Recording auto-stops on silence (see `_start_listening`) - pressing
        the orb again while already listening just ends the turn early
        instead of waiting out the silence timeout; `_on_recording_done`
        handles finishing up either way.
        """
        if self._listening:
            self.recorder.cancel_auto_stop()
        else:
            self._begin_listening()

    def _begin_listening(self, keep_reply: bool = False) -> None:
        """Start a listening turn, however it was triggered (button or wake word).

        `keep_reply`: listening for the answer to a question this turn asked.
        The turn's own reply is still to come, so it must not be silenced - doing
        that stopped the turn's speech queue, and the reply after the answer was
        never spoken (measured 2026-10-08).

        Interrupts any in-progress TTS playback first - this is Chronoa's
        barge-in: starting to talk again while the assistant is still
        speaking cuts it off immediately instead of waiting it out.
        """
        was_speaking = self.window is not None and self.window.get_state() is AssistantState.SPEAKING
        if not keep_reply:
            self._silence_reply()  # the rest of a sentence-by-sentence reply, not only the one playing
            self.player.stop()
        self._listening = True
        if self.window:
            # Route the handover through INTERRUPTING so the stop is visible
            # as its own moment; cutting straight from speaking to listening
            # reads as a dropped frame rather than a deliberate interrupt.
            if was_speaking:
                self.window.set_state(AssistantState.INTERRUPTING)
                GLib.timeout_add(180, self._settle_into_listening)
            else:
                self.window.set_state(AssistantState.LISTENING)
            self.window.set_status("")
        cues.play("listening", self.config)
        self._start_listening()

    def dictate(self, action=None, param=None) -> None:
        """Talk *at* the machine: one long turn, no wake word, no button held.

        Added because an ordinary turn is capped at twenty seconds, which is right
        for "what time is it" and wrong for anything longer - a paragraph of
        notes, a message to be written down, a meeting. A cap that stops a
        rambling question becomes the thing that stops dictation in the middle of
        its first sentence.

        So this is a different ceiling and a different silence, not a longer
        version of the same one:

        - **five minutes**, because a paragraph is not twenty seconds;
        - **2.5 seconds** of silence to finish, because 1.2 cuts at every pause
          *inside* a paragraph rather than at its end;
        - **no wake word**, because you are already talking and pressing nothing.

        It still goes through the same capture, the same transcription and the
        same turn, so there is no second audio path to keep honest - and it is
        stopped the same ways: the stop button, Esc, or the silence.

        A dictation longer than the cap is cut and transcribed as far as it got,
        which is reported rather than hidden: the transcript arrives and the
        status line says it was cut.
        """
        if self._listening:
            # `_toggle_listening`, not a `_stop_listening` that does not exist -
            # the first version of this called one and would have raised
            # `AttributeError` the first time anybody pressed the key twice,
            # which is exactly when dictation most needs to work. Going through
            # the microphone button's own handler also means dictation is
            # stopped by exactly the code that stops a question.
            self._dictating = False
            self._listen_max_seconds = TURN_SECONDS
            self._listen_silence_seconds = None      # the setting, not a constant
            self._toggle_listening(None, None)
            return GLib.SOURCE_REMOVE
        self._follow_up_of = ""
        self._dictating = True
        self._listen_max_seconds = DICTATION_SECONDS
        self._listen_silence_seconds = DICTATION_SILENCE_SECONDS
        if self.window:
            self.window.set_status("Dictating - stop whenever you have finished")
        try:
            self._begin_listening()
        finally:
            # Back to `None`, which means "use `end-of-speech-pause`". Restoring
            # the *constant* instead would silently override the person's own
            # setting for every turn after the first dictation - and that is the
            # kind of thing that only shows up as "the mic got twitchy one day".
            self._listen_max_seconds = TURN_SECONDS
            self._listen_silence_seconds = None
        return GLib.SOURCE_REMOVE

    def _settle_into_listening(self) -> bool:
        """Finish the interrupt handover, unless something else took over."""
        if self.window and self._listening and self.window.get_state() is AssistantState.INTERRUPTING:
            self.window.set_state(AssistantState.LISTENING)
        return GLib.SOURCE_REMOVE

    def _stop_speaking(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Stop: the reply being spoken, or the turn still being thought about (Esc / the Stop button).

        Cancelling the turn's future raises CancelledError inside
        `Assistant.handle`, whose `close_interrupted_turn("interrupted")`
        already leaves the history valid; before this, a slow or wrong turn
        could only be waited out, for up to MAX_TURN_SECONDS (assistd's
        InterruptTurn is the same verb).
        """
        self.player.stop()
        self._silence_reply()
        future = getattr(self, "_turn_future", None)
        if future is not None and not future.done():
            future.cancel()
            logger.info("Turn cancelled by the user")
            if self.window:
                self.window.set_orb_state("idle")
                self.window.set_state(AssistantState.IDLE)
                self.window.set_status("Stopped")
            return
        if self.window and self.window.get_state() is AssistantState.SPEAKING:
            self.window.set_state(AssistantState.IDLE)

    def _toggle_wake_word(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle hands-free wake-word listening on or off."""
        self._set_wake_word_active(not self._wake_word_active)

    def _set_wake_word_active(self, active: bool) -> None:
        """Start or stop the background wake-word listener and persist the choice."""
        if active:
            if not self.wakeword.is_available():
                logger.warning(f"Cannot enable the wake phrase: {self.wakeword.unavailable_reason()}")
                if self.window:
                    self.window.set_status("Wake word unavailable")
                return
            started = self.wakeword.start(self._on_wake_word_detected)
            self._wake_word_active = started
            if started and self.window:
                self.window.set_status("Wake word: ON")
        else:
            self.wakeword.stop()
            self._wake_word_active = False
            if self.window:
                self.window.set_status("Wake word: OFF")
        self.config.set("wake-word-enabled", "true" if self._wake_word_active else "false")

    def _download_speech_model(
        self, _action: Gio.SimpleAction = None, _param: object = None
    ) -> None:
        """Fetch and verify the speech model, on its own thread.

        A ~57 MB download cannot run on the GTK thread (the window freezes) nor
        on the AsyncBridge's loop, which also carries transcription and the LLM
        turn, so a download there would stall the assistant itself. Same shape
        as AudioRecorder's watchdog thread.
        """
        if getattr(self, "_model_download_thread", None) is not None:
            if self._model_download_thread.is_alive():
                self._set_status("A speech model is already downloading...")
                return

        parakeet = self._stt_backend() == stt.BACKEND_PARAKEET
        if parakeet:
            model = stt_provision.PARAKEET_DEFAULT_MODEL
            key = model
            available = stt_provision.PARAKEET_MODELS
        else:
            model = self.config.whisper_model or self.hardware.get_whisper_model()
            key = stt_provision.resolve_key(model)
            available = stt_provision.MODELS
        if key not in available:
            self._set_status(
                f"No downloadable build for the '{model}' model. Available: "
                f"{', '.join(sorted(available))}"
            )
            return
        if not self.config.model_download_enabled:
            self._set_status(
                "Downloading a speech model is off. Turn on "
                "'Download speech model on first use' in Settings first."
            )
            return

        self._set_status(f"Downloading the {model} speech model...")
        thread = threading.Thread(
            target=self._fetch_speech_model_worker,
            args=(key,),
            name="chronoa-model-download",
            daemon=True,
        )
        self._model_download_thread = thread
        thread.start()

    def _fetch_speech_model_worker(self, key: str) -> None:
        try:
            provision = (
                stt_provision.provision_parakeet
                if self._stt_backend() == stt.BACKEND_PARAKEET
                else stt_provision.provision
            )
            path = provision(key, config=self.config)
        except Exception as exc:  # noqa: BLE001 - the reason is user-facing
            GLib.idle_add(self._on_model_download_failed, str(exc))
            return
        GLib.idle_add(self._on_model_download_done, str(path))

    def _on_model_download_done(self, path: str) -> bool:
        self._model_download_thread = None
        # Rebuild the STT handle so the next utterance uses the new model rather
        # than keeping the one that failed is_available() at construction.
        try:
            self.stt = self._build_stt()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Downloaded, but the model could not be loaded: {exc}")
            return False
        if self.stt.is_available():
            self._set_status("Speech model ready - voice input is now available.")
        else:
            self._set_status(
                f"Model installed to {path}, but whisper-cpp itself is not "
                "installed. Run: sudo pacman -S whisper-cpp"
            )
        return False

    def _on_model_download_failed(self, reason: str) -> bool:
        self._model_download_thread = None
        self._set_status(f"Could not get the speech model: {reason}")
        return False

    def _toggle_model_download(
        self, _action: Gio.SimpleAction, _param: object
    ) -> None:
        """Toggle fetching a speech model over the network.

        This is the only setting that makes Chronoa download anything, so the
        refusal is stated where the choice is made rather than at the point of
        failure: the user should not discover it by talking and being ignored.
        """
        new_value = not self.config.model_download_enabled
        self.config.set("model-download-enabled", "true" if new_value else "false")
        if self.window:
            self.window.set_status(
                "Model download: ON - the next voice input fetches a ~57 MB "
                "speech model and checks it against a pinned SHA-256"
                if new_value else "Model download: OFF"
            )

    def _toggle_barge_in_vad(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle continuous-VAD barge-in. `_speak()` reads this fresh each call, no extra sync needed."""
        new_value = not self.config.barge_in_vad_enabled
        self.config.set("barge-in-vad-enabled", "true" if new_value else "false")
        if not new_value:
            if self.window:
                self.window.set_status("Barge-in (VAD): OFF")
            return
        # Say why this is risky *here*, where the choice is made. PipeWire ships
        # an echo canceller, but it is a SPA hook attached by a
        # filter-chain.conf.d fragment, so it cannot be switched on from here -
        # only detected. Pretending otherwise would leave the user with a
        # setting that mysteriously interrupts itself on speakers.
        from shani_chronoa import pipewire

        if pipewire.echo_cancel_active():
            if self.window:
                self.window.set_status("Barge-in (VAD): ON, echo cancel active")
        else:
            logger.warning(
                "Continuous-VAD barge-in enabled with no echo cancellation in the "
                "PipeWire graph; on speakers Chronoa may interrupt itself"
            )
            if self.window:
                self.window.set_status("Barge-in (VAD): ON - no echo cancel, use headphones")

    def _on_wake_word_detected(self, remainder: str = "") -> None:
        """Wake-word callback - runs on the listener's own background thread."""
        GLib.idle_add(self._on_wake_word_detected_main, remainder)

    def _on_wake_word_detected_main(self, remainder: str = "") -> bool:
        """Handle a wake-word detection back on the GTK main thread.

        "Hey Chronoa, what time is it" said in one breath carries its question:
        it becomes the turn straight away, instead of being thrown away and the
        microphone opened for the person to say it again (sayri's core.py does
        the same). Just the phrase opens the microphone, as before.
        """
        if self._listening:
            return GLib.SOURCE_REMOVE
        command = stt.clean_transcription(remainder or "")
        if len(command.split()) >= 2:
            logger.info("Wake phrase came with a request; taking it as the turn")
            self._on_transcribed(command)
            return GLib.SOURCE_REMOVE
        self._begin_listening()
        return GLib.SOURCE_REMOVE

    def _start_listening(self) -> None:
        """Start recording speech input, auto-stopping once the user stops talking.

        Borrowed from `aside`/`loquivox`'s silence-based auto-stop: wake-word
        activation is meant to be hands-free, so requiring a second explicit
        "stop listening" press defeated the point. `cancel_auto_stop()` (see
        `_toggle_listening`) still lets the user end a turn early.
        """
        if not self.stt or not self.stt.is_available():
            logger.error("Whisper not available for listening")
            self._listening = False
            if self.window:
                self.window.set_orb_state("error")
            return

        started = self.recorder.start_auto_stop(
            self._on_recording_done,
            max_seconds=getattr(self, "_listen_max_seconds", TURN_SECONDS),
            silence_seconds=getattr(
                self, "_listen_silence_seconds", None)
            or self.config.get_double("end-of-speech-pause", 1.2) or 1.2,
            on_level=self._on_input_level,
        )
        if not started:
            logger.error("Failed to start audio recording")
            self._listening = False
            if self.window:
                self.window.set_orb_state("error")
            return

        logger.info("Recording speech input (auto-stops on silence)...")

    def _on_input_level(self, level: float) -> None:
        """Input level from the recorder, for the orb's halo.

        Called from the recorder's own background thread, so it hops to the
        GTK thread before touching the window.
        """
        GLib.idle_add(self._on_input_level_main, level)

    def _on_playback_level(self, level: float) -> None:
        """Playback level from the player's level stream, for the orb's halo.

        Same thread rule as the recorder's level callback: hop to GTK first.
        """
        GLib.idle_add(self._on_playback_level_main, level)

    def _on_playback_level_main(self, level: float) -> bool:
        if self.window:
            self.window.set_playback_level(level)
        return GLib.SOURCE_REMOVE

    def _on_input_level_main(self, level: float) -> bool:
        if self.window:
            self.window.set_input_level(level)
        return GLib.SOURCE_REMOVE

    def _on_recording_done(self, audio_path: Optional[str]) -> None:
        """Recorder callback - runs on the recorder's own background thread."""
        GLib.idle_add(self._on_recording_done_main, audio_path)

    def _dictation_note(self) -> str:
        """What to say when a dictation ended, including when it was cut.

        A dictation stopped by its five-minute cap arrives as a transcript, and a
        transcript that silently stops mid-sentence reads as "that is everything
        I said" - which is the one thing a transcript must never imply.
        """
        if not getattr(self, "_dictating", False):
            return ""
        self._dictating = False
        if getattr(self, "_record_truncated", False):
            self._record_truncated = False
            return " (stopped at the dictation limit)"
        return " (dictation ended)"

    def _on_recording_done_main(self, audio_path: Optional[str]) -> bool:
        """Handle a finished recording (auto-stopped or cancelled early) on the GTK main thread."""
        self._listening = False
        # Why it ended, read now: the recorder's reason describes *this*
        # recording, and a later turn would overwrite it.
        self._record_truncated = (getattr(self.recorder, "auto_stop_reason", "") == "limit")
        if not audio_path:
            logger.info("No audio captured")
            if self.window:
                self.window.set_orb_state("idle")
                self.window.set_status("Didn't catch that")
            return GLib.SOURCE_REMOVE

        if self.window:
            self.window.set_orb_state("processing")
            self.window.set_status("Transcribing...")

        self._async.run(self._transcribe(audio_path), self._on_transcribed)
        return GLib.SOURCE_REMOVE

    def _model_turn_in_flight(self) -> bool:
        """Whether a model turn is running now - the contention the gate exists for."""
        future = getattr(self, "_turn_future", None)
        try:
            return future is not None and not future.done()
        except Exception:  # noqa: BLE001 - "could not tell" must not cost the transcript
            return False

    async def _transcribe(self, audio_path: str) -> str:
        """Run (blocking) whisper.cpp transcription off the GTK thread.

        **Waits for the model first, when there is one.** whisper.cpp and
        llama.cpp share this machine, so a transcript that starts while a reply is
        still being generated competes with it for the same accelerator - and the
        transcript is the half whose lateness the person notices, having just
        said the words. `assistd`'s `QueuedTranscriber` asks the same question
        before every transcription and logs which way it went; this does the
        bounded-wait half of it, with the reason on both the log and the status
        line so "your speech was slow" has an explanation attached.

        The wait happens on this coroutine's executor, not the GTK thread, so the
        window stays responsive while it happens - which is the whole reason the
        transcription is off-thread in the first place.
        """
        loop = asyncio.get_event_loop()
        gate = getattr(self, "_speech_gate", None)
        if gate is None:
            # Busy means a model turn is actually in flight. It read the window's
            # state, and the voice path itself sets THINKING ("processing") just
            # before transcribing - so the gate waited its full 4 s behind its own
            # orb on every spoken request, with no model running (measured
            # 2026-10-08: "still generating after 4s" on the first turn of a run).
            gate = self._speech_gate = speech_gate.Gate(self._model_turn_in_flight)
        try:
            def _work() -> str:
                waited = gate.run()
                if waited and self.window:
                    # A *state*, not a status line. `assistd` carries this as
                    # `VoiceCaptureState::Queued`, and the difference is visible:
                    # on the detail line it is a sentence that scrolls away, while
                    # as a state it moves the orb and holds there for as long as
                    # the wait lasts - which is the difference between "I heard
                    # you and am holding on" and "the microphone has died".
                    GLib.idle_add(self.window.set_state, AssistantState.QUEUED)
                    GLib.idle_add(self.window.set_status, gate.last_reason)
                try:
                    return self.stt.transcribe(audio_path)
                finally:
                    if waited and self.window:
                        GLib.idle_add(self.window.set_state, AssistantState.LISTENING)

            return await loop.run_in_executor(None, _work)
        finally:
            os.remove(audio_path)

    def _on_transcribed(self, result: object) -> bool:
        """Feed the transcribed text into the assistant, back on the GTK thread."""
        if isinstance(result, Exception) or not str(result).strip():
            logger.error(f"Transcription failed or empty: {result}")
            if self.window:
                self.window.set_orb_state("idle")
                self.window.set_status("Didn't catch that")
            return GLib.SOURCE_REMOVE

        text = stt.clean_transcription(str(result))
        follow_up, self._follow_up_of = getattr(self, "_follow_up_of", ""), ""
        if text and follow_up and _is_echo(text, follow_up):
            logger.info("Follow-up heard only the reply itself; not a turn")
            text = ""
        if not text:
            logger.info("Transcript was only noise or a whisper silence phrase; not a turn")
            if not follow_up:
                cues.play("not-understood", self.config)
            if self.window:
                self.window.set_orb_state("idle")
                self.window.set_status("Didn't catch that")
            return GLib.SOURCE_REMOVE
        # A question is waiting (ask_user, a permission prompt): what was said
        # answers it. Starting a new turn instead left the tool blocked for
        # its full timeout while the answer went nowhere.
        if self.window and self.window.answer_pending_question(text):
            self.window.set_state(AssistantState.THINKING)
            return GLib.SOURCE_REMOVE
        self._voice_turn = True
        files_block = ""
        if self.window:
            # a spoken message takes the attached files too, as a typed one does
            names, files_block = self.window.take_attachments()
            self.window.set_state(AssistantState.THINKING)
            self.window.add_user_turn(text + (("\n📎 " + ", ".join(names)) if names else ""))
        self._submit(text + files_block)
        return GLib.SOURCE_REMOVE

    def _spoken_presenter(self, present):
        """Wrap the window's question presenter so a question is also SAID.

        Someone talking to Chronoa hands-free cannot see a question in the
        window: it is read aloud with its options, and when the turn was
        spoken, Chronoa listens for the answer afterwards. The tool loop that
        asks is blocked waiting for the answer, so the speaking happens on a
        thread of its own, not on that loop.
        """
        def speak_then_listen(question: str, options: list) -> None:
            first = question.strip().split("\n\n", 1)[0]
            said = first + (" You can say: " + ", ".join(options[:-1]) + ", or " + options[-1] + "."
                            if len(options) > 1 else "")
            try:
                audio = self.tts.synthesize_to_bytes(markdown_lite.to_speech(said))
                if audio and self.player.is_available():
                    self.player.play_bytes(audio, on_level=self._on_playback_level)
            except Exception as e:  # a question that cannot be said is still on screen
                logger.error(f"Could not speak the question: {e}")
            if self._voice_turn:
                logger.info("Question said aloud; listening for the spoken answer")
                GLib.idle_add(self._listen_for_answer)
            else:
                logger.info("Question said aloud; the turn was typed, so not opening the microphone")

        def wrapped(question: str, options: list):
            done = present(question, options)
            if self.tts and self.tts.is_available() and self.config.notification_enabled:
                threading.Thread(target=speak_then_listen, args=(question, list(options)), daemon=True).start()
            return done
        return wrapped

    def _listen_for_answer(self) -> bool:
        pending = bool(self.window and self.window.has_pending_question())
        if pending and not self._listening:
            self._begin_listening(keep_reply=True)
        else:
            logger.info(f"Not listening for an answer (question open: {pending}, already listening: {self._listening})")
        return GLib.SOURCE_REMOVE

    def _new_speech_queue(self):
        """A sentence-by-sentence speaker for this turn, or None when replies are not spoken."""
        if not (self.tts and self.tts.is_available() and self.config.notification_enabled
                and self.player.is_available()):
            return None
        use_vad = self.config.barge_in_vad_enabled and self.barge_in_monitor.is_available()

        def started() -> None:
            self._mark("first_audio")
            if use_vad:
                self.barge_in_monitor.start(self._on_barge_in_detected)
            self.barge_in_monitor.begin_playback()
            GLib.idle_add(lambda: (self.window.set_state(AssistantState.SPEAKING) if self.window else None, False)[1])

        holder = {}

        def done() -> None:
            if use_vad:
                self.barge_in_monitor.stop()
            self._mark("spoken")
            q0 = holder.get("q")
            if q0 is not None and q0.spoken and getattr(self, "_voice_turn", False) \
                    and self.config.get_bool("follow-up-enabled", False):
                GLib.idle_add(self._listen_for_follow_up)
            self._log_latency()
            self._on_speech_finished()
            reason = getattr(self.tts, "degraded_reason", "")
            q = holder.get("q")
            if reason and q is not None and not q.spoken and self.window:
                GLib.idle_add(lambda: (self.window.set_status(f"Speech output unavailable: {reason}"), False)[1])
        holder["q"] = speech.SpeechQueue(self.tts.synthesize_to_bytes,
                                         lambda wav: self.player.play_bytes(wav, on_level=self._on_playback_level),
                                         self.player.stop,
                                         on_start=started, on_done=done)
        return holder["q"]

    def _mark(self, stage: str) -> None:
        marks = getattr(self, "_turn_marks", None)
        if marks is not None and stage not in marks:
            marks[stage] = time.monotonic()

    def _log_latency(self) -> None:
        """One line per turn: where the time went (assistd's voice::latency stages)."""
        marks = getattr(self, "_turn_marks", None)
        if not marks or "logged" in marks:
            return
        marks["logged"] = True
        t0 = marks["start"]
        parts = [f"{k} {marks[k] - t0:.2f}s" for k in ("first_word", "reply", "first_audio", "spoken") if k in marks]
        self.last_latency = {k: round(marks[k] - t0, 2) for k in marks if k not in ("start", "logged")}
        logger.info("Turn latency: %s", ", ".join(parts) or "no stages reached")

    FOLLOW_UP_SECONDS = 6.0

    def _listen_for_follow_up(self) -> bool:
        """After a spoken reply, keep the microphone open briefly for "and make it ten minutes" (sayri's always mode).

        Short (FOLLOW_UP_SECONDS), and a transcript that is just the reply
        heard back from the speakers is dropped (`_is_echo`), as sayri does.
        """
        if self._listening:
            return GLib.SOURCE_REMOVE
        self._follow_up_of = self._last_reply_text()
        self._listen_max_seconds = self.FOLLOW_UP_SECONDS
        try:
            self._begin_listening()
        finally:
            self._listen_max_seconds = TURN_SECONDS
        return GLib.SOURCE_REMOVE

    def _last_reply_text(self) -> str:
        if not self.assistant:
            return ""
        turns = self.assistant.visible_turns()
        return next((t for r, t in reversed(turns) if r == "assistant"), "")

    def _on_reply_text(self, delta: str) -> None:
        """Words of the reply as the local model streams them (AsyncBridge thread): show and speak them."""
        self._mark("first_word")
        self._turn_streamed.append(delta)
        queue = getattr(self, "_turn_speech", None)
        if queue is not None:
            queue.speak(self._turn_buffer.push(delta))
        so_far = "".join(self._turn_streamed)
        GLib.idle_add(lambda: (self.window.set_response(so_far) if self.window else None, False)[1])

    def _silence_reply(self) -> None:
        queue = getattr(self, "_turn_speech", None)
        if queue is not None:
            queue.stop()

    async def _speak(self, text: str) -> None:
        """Synthesize and play back a spoken reply, off the GTK thread.

        Optionally (see `barge-in-vad-enabled`) monitors the mic for real
        speech continuously during playback for barge-in that doesn't need
        an explicit button/wake-word re-trigger first - off by default, see
        `BargeInMonitor`'s docstring for why.
        """
        loop = asyncio.get_event_loop()
        tts_bytes = await loop.run_in_executor(None, self.tts.synthesize_to_bytes, text)
        if not tts_bytes or not self.player.is_available():
            return

        use_vad = self.config.barge_in_vad_enabled and self.barge_in_monitor.is_available()
        if use_vad:
            self.barge_in_monitor.start(self._on_barge_in_detected)
        # Opened just before the audio starts, so the monitor's noise floor is
        # sampled from a room that is not currently talking.
        self.barge_in_monitor.begin_playback()
        try:
            await loop.run_in_executor(None, lambda: self.player.play_bytes(tts_bytes, on_level=self._on_playback_level))
        finally:
            if use_vad:
                self.barge_in_monitor.stop()
            self._on_speech_finished()

    def _on_speech_finished(self) -> None:
        """Clear the speaking state once playback ends.

        Reached from `_speak`, which the AsyncBridge runs on its own thread,
        so the state change hops to the GTK thread first - GTK4 is not
        thread-safe and every other background callback in this file does the
        same.

        Guarded on the state still being SPEAKING: an interrupt moves the
        window to LISTENING, and without the guard playback ending would
        yank the UI back to IDLE mid-turn, so the mic looked closed while it
        was still open.
        """
        GLib.idle_add(self._on_speech_finished_main)

    def _on_speech_finished_main(self) -> bool:
        if self.window and self.window.get_state() is AssistantState.SPEAKING:
            self.window.set_state(AssistantState.IDLE)
        return GLib.SOURCE_REMOVE

    def _on_barge_in_detected(self) -> None:
        """Barge-in monitor callback - runs on the monitor's own background thread."""
        GLib.idle_add(self._on_barge_in_detected_main)

    def _on_barge_in_detected_main(self) -> bool:
        """Handle a mid-playback interruption on the GTK main thread.

        `_begin_listening()` already calls `player.stop()` unconditionally,
        so no separate stop call is needed here.
        """
        if not self._listening:
            self._begin_listening()
        return GLib.SOURCE_REMOVE
