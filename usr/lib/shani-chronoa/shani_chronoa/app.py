"""Main application module for Shani Chronoa.

Orchestrates the STT, LLM, TTS, GUI, and MCP components.
"""

import asyncio
import logging
import sys
import os
import signal
from typing import Optional, Union

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio, GLib  # type: ignore

# Add the package to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shani_chronoa.assistant import Assistant
from shani_chronoa.asyncbridge import AsyncBridge
from shani_chronoa.audio import AudioPlayer, AudioRecorder, BargeInMonitor
from shani_chronoa.config import ChronoaConfig, HardwareProfile, PrivacyManager
from shani_chronoa.stt import WhisperSTT
from shani_chronoa.llm import OllamaLLM
from shani_chronoa.cloud_llm import CloudLLMChain, DEFAULT_PROVIDER_ORDER, BYOK_PROVIDER_ORDER
from shani_chronoa.secrets_manager import secrets_manager
from shani_chronoa.tts import PiperTTS
from shani_chronoa.wakeword import WakeWordListener
from shani_chronoa.gui import CajitaWindow, ChronoaOrbWidget

logger = logging.getLogger(__name__)


class ChronoaApplication(Gtk.Application):
    """Main Shani Chronoa GTK Application."""

    def __init__(self) -> None:
        super().__init__(
            application_id="dev.shani.chronoa",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.config = ChronoaConfig()
        self.hardware = HardwareProfile()
        self.privacy = PrivacyManager(self.config)
        self.stt: Optional[WhisperSTT] = None
        self.llm: Optional[Union[OllamaLLM, CloudLLMChain]] = None
        self.tts: Optional[PiperTTS] = None
        self.assistant: Optional[Assistant] = None
        self.window: Optional[CajitaWindow] = None
        self._settings_window: Optional[Gtk.Window] = None
        self.recorder = AudioRecorder()
        self.player = AudioPlayer()
        self.barge_in_monitor = BargeInMonitor()
        self.wakeword = WakeWordListener(wakeword_model=self.config.wake_word_model)
        self._listening = False
        self._wake_word_active = False
        self._ollama_available = False
        self._async = AsyncBridge()
        self._model_override: Optional[str] = None

    def do_startup(self) -> None:
        """Handle application startup."""
        logger.info("Shani Chronoa starting up...")
        # super().do_startup() mis-marshals the zero-arg vfunc chain-up on
        # this PyGObject build (TypeError + GLib-GIO-CRITICAL, then a
        # segfault) - the explicit class-qualified call works correctly.
        Gtk.Application.do_startup(self)

        # Initialize components based on hardware and config
        self._init_components()

        # Create actions
        self._create_actions()

        logger.info("Shani Chronoa startup complete")

    def do_shutdown(self) -> None:
        """Release background mic/playback resources before the app exits."""
        logger.info("Shani Chronoa shutting down...")
        self.wakeword.stop()
        self.player.stop()
        self.barge_in_monitor.stop()
        self.recorder.cancel_auto_stop()
        # Stop the AsyncBridge last: it may still be running an in-flight
        # transcription/TTS/assistant coroutine scheduled by the components
        # above. shutdown() cancels pending coroutines and joins the thread
        # (bounded), so no bridge work is abandoned and the GTK app does not
        # hang on exit.
        self._async.shutdown()
        # Explicit class-qualified chain-up, matching do_startup() above -
        # this build's super().do_shutdown() has the same zero-arg vfunc
        # mismatch that crashed do_startup() before it was fixed the same way.
        Gtk.Application.do_shutdown(self)

    def do_activate(self) -> None:
        """Handle application activation."""
        logger.info("Activating Shani Chronoa")
        if not self.window:
            self.window = CajitaWindow(self)
            self.window.connect("user-input", self._on_user_input)
            self.window.set_status(self._llm_status_text())
            self.window.present()
        else:
            self.window.present()

    def _llm_status_text(self) -> str:
        """Describe which LLM backend is active - nothing else surfaced this before.

        `self.llm` is never actually None when Ollama is unavailable - it's
        still a live (just non-functional) OllamaLLM instance, same as the
        `not self.llm.is_available()` check `_submit()` already uses - so
        this must check availability explicitly, not just "is it set".
        """
        if self.llm is None or not self.llm.is_available():
            return "LLM unavailable"
        if isinstance(self.llm, CloudLLMChain):
            return f"Ready (cloud: {self.llm.model})"
        return f"Ready (Ollama: {self.llm.model})"

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """Handle command line arguments."""
        args = command_line.get_arguments()
        self._parse_args(args)
        # --debug (and a persisted debug-mode setting) take effect on the
        # running process, not just the next launch.
        _set_log_level(self.config.debug_mode)
        # do_startup (and _init_components) already ran with the default
        # model before argv was parsed, so apply a --model= override here.
        if self._model_override and self.llm:
            self.llm.model = self._model_override
            logger.info(f"Model overridden via --model=: {self._model_override}")
        self.activate()
        return 0

    def _init_components(self) -> None:
        """Initialize all components based on hardware profile."""
        # Precedence: --model= CLI flag (session-only) > persisted gsetting
        # override > hardware auto-selection. The persisted override was
        # never actually consulted before this - see config.py's `model`/
        # `whisper_model` docstrings.
        #
        # The persisted hardware-profile override (auto/low/medium/high/gpu)
        # is applied to the detected profile before any model/whisper/context
        # value is selected. "auto" (or an unknown/blank value) keeps the
        # detected profile - see config.py's `hardware_profile` property.
        persisted_profile = self.config.hardware_profile
        if persisted_profile in ("low", "medium", "high", "gpu"):
            self.hardware.profile = persisted_profile

        model = self._model_override or self.config.model or self.hardware.get_model()
        whisper_model = self.config.whisper_model or self.hardware.get_whisper_model()

        # Initialize STT
        self.stt = WhisperSTT(model=whisper_model, language=self.config.language)
        if not self.stt.is_available():
            logger.warning("Whisper.cpp not available - STT disabled")

        # Initialize LLM - Ollama first, always (local-first by design).
        self.llm = OllamaLLM(
            host=self.config.ollama_host,
            model=model,
            context_window=self.hardware.get_context_window(),
        )
        self._ollama_available = self.llm.is_available()
        if not self._ollama_available:
            logger.warning("Ollama not available")
            self._maybe_enable_cloud_fallback()
        self.assistant = Assistant(self.llm)

        # Initialize TTS
        self.tts = PiperTTS(voice=self.config.piper_voice)
        if not self.tts.is_available():
            logger.warning("Piper TTS not available - TTS disabled")

        if not self.recorder.is_available():
            logger.warning("No audio recording backend (pw-record/arecord) found - voice input disabled")
        if not self.player.is_available():
            logger.warning("No audio playback backend (pw-play/aplay) found - spoken replies disabled")

        if not self.wakeword.is_available():
            logger.info("Wake-word engine unavailable (openWakeWord/numpy not installed) - push-to-talk only")
        elif self.config.wake_word_enabled:
            self._set_wake_word_active(True)

        self._sync_autostart()

        logger.info(f"Initialized: model={model}, whisper={whisper_model}, profile={self.hardware.profile}")

    def _create_actions(self) -> None:
        """Create application-wide actions."""
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Ctrl>Q"])

        # Toggle listening action
        listen_action = Gio.SimpleAction.new("toggle-listening", None)
        listen_action.connect("activate", self._toggle_listening)
        self.add_action(listen_action)

        # Privacy toggle action
        privacy_action = Gio.SimpleAction.new("toggle-privacy", None)
        privacy_action.connect("activate", self._toggle_privacy)
        self.add_action(privacy_action)

        # Wake-word (hands-free) toggle action - no settings UI exists yet
        # to flip this, so it's reachable via a keyboard accelerator too,
        # same gap as toggle-privacy already had.
        wake_word_action = Gio.SimpleAction.new("toggle-wake-word", None)
        wake_word_action.connect("activate", self._toggle_wake_word)
        self.add_action(wake_word_action)
        self.set_accels_for_action("app.toggle-wake-word", ["<Ctrl><Shift>W"])

        # Reset conversation - Assistant.reset() existed but nothing ever
        # called it; the only way to clear history was restarting the app.
        reset_action = Gio.SimpleAction.new("reset-conversation", None)
        reset_action.connect("activate", self._reset_conversation)
        self.add_action(reset_action)
        self.set_accels_for_action("app.reset-conversation", ["<Ctrl>N"])

        # Auto-start-on-login toggle - config.py's "auto-start" gsetting
        # existed but was never wired to anything that actually created or
        # removed an autostart entry.
        auto_start_action = Gio.SimpleAction.new("toggle-auto-start", None)
        auto_start_action.connect("activate", self._toggle_auto_start)
        self.add_action(auto_start_action)

        # Cloud-fallback and barge-in-VAD toggles - added alongside the
        # settings window so every toggle in it goes through one real
        # GAction, not a second copy of the enable/disable logic.
        cloud_fallback_action = Gio.SimpleAction.new("toggle-cloud-fallback", None)
        cloud_fallback_action.connect("activate", self._toggle_cloud_fallback)
        self.add_action(cloud_fallback_action)

        barge_in_vad_action = Gio.SimpleAction.new("toggle-barge-in-vad", None)
        barge_in_vad_action.connect("activate", self._toggle_barge_in_vad)
        self.add_action(barge_in_vad_action)

        # Debug-logging toggle - the settings row goes through this action so
        # the live log-level change and the persisted setting stay in one path.
        debug_action = Gio.SimpleAction.new("toggle-debug", None)
        debug_action.connect("activate", self._toggle_debug)
        self.add_action(debug_action)

        # Settings window
        settings_action = Gio.SimpleAction.new("open-settings", None)
        settings_action.connect("activate", self._open_settings)
        self.add_action(settings_action)
        self.set_accels_for_action("app.open-settings", ["<Ctrl>comma"])

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

    def _begin_listening(self) -> None:
        """Start a listening turn, however it was triggered (button or wake word).

        Interrupts any in-progress TTS playback first - this is Chronoa's
        barge-in: starting to talk again while the assistant is still
        speaking cuts it off immediately instead of waiting it out.
        """
        self.player.stop()
        self._listening = True
        if self.window:
            self.window.set_orb_state("listening")
            self.window.set_status("Listening...")
        self._start_listening()

    def _toggle_privacy(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle privacy mode."""
        if self.privacy.is_local_only:
            self.privacy.disable_local_mode()
            if self.window:
                self.window.set_status("Privacy: OFF")
            # Only relevant now that privacy is off: pick up the cloud
            # fallback immediately if Ollama was unavailable at startup,
            # rather than waiting for a restart.
            if not self._ollama_available:
                self._maybe_enable_cloud_fallback()
        else:
            self.privacy.enable_local_mode()
            if self.window:
                self.window.set_status("Privacy: ON (Local Only)")
            # Privacy just turned back on - drop the cloud LLM immediately
            # if that's what's active. Re-enabling privacy mode should stop
            # sending prompts off the machine right away, not just at the
            # next restart.
            if isinstance(self.llm, CloudLLMChain):
                logger.info("Privacy mode re-enabled - dropping cloud LLM fallback")
                self.llm = None
                if self.assistant:
                    self.assistant.llm = None

    def _maybe_enable_cloud_fallback(self) -> None:
        """Switch self.llm to the free cloud LLM chain if both opt-in gates are open.

        Both privacy mode being off AND cloud-fallback-enabled must hold -
        this is the one path in Chronoa that sends prompts and tool-call
        arguments off the machine, so it never triggers from a single
        setting alone.
        """
        if self._ollama_available or isinstance(self.llm, CloudLLMChain):
            return
        if self.privacy.is_local_only or not self.config.cloud_fallback_enabled:
            logger.info(
                "LLM unavailable (no Ollama, and cloud fallback needs privacy mode off "
                "AND cloud-fallback-enabled)"
            )
            return
        # BYOK providers (Anthropic/OpenAI/Google/Groq) go first when a key
        # is configured for them - presumably far more capable/reliable
        # than the anonymous free chain, which stays as the fallback of
        # last resort. CloudLLMChain itself skips any BYOK provider left
        # without a key, so listing all of them here is harmless.
        api_keys = self.config.cloud_llm_api_keys()
        # Make the sanitizer/sandbox-env-injection aware of the actual live
        # keys (stored in GSettings by design, not the vault - see
        # ChronoaConfig.cloud_llm_api_keys()'s threat-model note) so
        # secrets_manager.sanitize_text_for_llm() can actually redact them.
        # Without this the vault's cache stays empty and sanitization is a
        # silent no-op against every real key in use.
        for provider_id, key_value in api_keys.items():
            secrets_manager.register_runtime_secret(f"cloud_llm_{provider_id}", key_value)
        provider_order = BYOK_PROVIDER_ORDER + DEFAULT_PROVIDER_ORDER
        cloud_llm = CloudLLMChain(provider_ids=provider_order, api_keys=api_keys)
        if not cloud_llm.is_available():
            return
        active = [p for p in provider_order if api_keys.get(p) or p in DEFAULT_PROVIDER_ORDER]
        logger.warning(
            f"Falling back to cloud LLM providers ({', '.join(active)}) - privacy mode is "
            "off and cloud-fallback-enabled is set. Prompts will leave this machine."
        )
        self.llm = cloud_llm
        if self.assistant:
            self.assistant.llm = cloud_llm

    def _toggle_wake_word(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle hands-free wake-word listening on or off."""
        self._set_wake_word_active(not self._wake_word_active)

    def _set_wake_word_active(self, active: bool) -> None:
        """Start or stop the background wake-word listener and persist the choice."""
        if active:
            if not self.wakeword.is_available():
                logger.warning("Cannot enable wake word - openWakeWord not installed")
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

    def _reset_conversation(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Clear conversation history and the response area."""
        if self.assistant:
            self.assistant.reset()
        if self.window:
            self.window.set_response("")
            self.window.set_status("New conversation")

    _AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
    _AUTOSTART_DESKTOP_FILE = os.path.join(_AUTOSTART_DIR, "shani-chronoa.desktop")
    _INSTALLED_DESKTOP_FILE = "/usr/share/applications/shani-chronoa.desktop"

    def _sync_autostart(self) -> None:
        """Create or remove the XDG autostart entry to match the auto-start setting.

        Symlinks to the installed launcher .desktop file rather than
        copying it, so it stays in sync if that file's Exec/Icon ever
        changes.
        """
        want = self.config.auto_start
        exists = os.path.lexists(self._AUTOSTART_DESKTOP_FILE)
        if want:
            if exists:
                return
            if not os.path.exists(self._INSTALLED_DESKTOP_FILE):
                logger.warning(
                    f"auto-start enabled but {self._INSTALLED_DESKTOP_FILE} doesn't exist "
                    "(not installed via the package?) - skipping"
                )
                return
            try:
                os.makedirs(self._AUTOSTART_DIR, exist_ok=True)
                os.symlink(self._INSTALLED_DESKTOP_FILE, self._AUTOSTART_DESKTOP_FILE)
                logger.info("Enabled autostart on login")
            except OSError as e:
                logger.error(f"Failed to enable autostart: {e}")
        elif exists:
            try:
                os.remove(self._AUTOSTART_DESKTOP_FILE)
                logger.info("Disabled autostart on login")
            except OSError as e:
                logger.error(f"Failed to disable autostart: {e}")

    def _toggle_auto_start(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle autostart-on-login and sync the XDG autostart entry immediately."""
        new_value = not self.config.auto_start
        self.config.set("auto-start", "true" if new_value else "false")
        self._sync_autostart()
        if self.window:
            self.window.set_status(f"Autostart: {'ON' if new_value else 'OFF'}")

    def _toggle_cloud_fallback(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle the free-cloud-LLM opt-in and react immediately, same as toggle-privacy."""
        new_value = not self.config.cloud_fallback_enabled
        self.config.set("cloud-fallback-enabled", "true" if new_value else "false")
        if new_value:
            self._maybe_enable_cloud_fallback()
        elif isinstance(self.llm, CloudLLMChain):
            logger.info("Cloud fallback disabled - dropping cloud LLM")
            self.llm = None
            if self.assistant:
                self.assistant.llm = None
        if self.window:
            self.window.set_status(f"Cloud fallback: {'ON' if new_value else 'OFF'}")

    def _toggle_barge_in_vad(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle continuous-VAD barge-in. `_speak()` reads this fresh each call, no extra sync needed."""
        new_value = not self.config.barge_in_vad_enabled
        self.config.set("barge-in-vad-enabled", "true" if new_value else "false")
        if self.window:
            self.window.set_status(f"Barge-in (VAD): {'ON' if new_value else 'OFF'}")

    def _toggle_debug(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle debug logging live and persist the choice."""
        new_value = not self.config.debug_mode
        self.config.set("debug-mode", "true" if new_value else "false")
        _set_log_level(new_value)
        if self.window:
            self.window.set_status(f"Debug logging: {'ON' if new_value else 'OFF'}")

    def _open_settings(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Open (or focus) the settings window."""
        from shani_chronoa.settings_window import SettingsWindow

        if self._settings_window is None:
            self._settings_window = SettingsWindow(self)
            self._settings_window.connect("close-request", self._on_settings_closed)
        self._settings_window.present()

    def _on_settings_closed(self, _window: Gtk.Window) -> bool:
        self._settings_window = None
        return False

    def _on_wake_word_detected(self) -> None:
        """Wake-word callback - runs on the listener's own background thread."""
        GLib.idle_add(self._on_wake_word_detected_main)

    def _on_wake_word_detected_main(self) -> bool:
        """Handle a wake-word detection back on the GTK main thread."""
        if not self._listening:
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

        started = self.recorder.start_auto_stop(self._on_recording_done, max_seconds=20.0, silence_seconds=1.2)
        if not started:
            logger.error("Failed to start audio recording")
            self._listening = False
            if self.window:
                self.window.set_orb_state("error")
            return

        logger.info("Recording speech input (auto-stops on silence)...")

    def _on_recording_done(self, audio_path: Optional[str]) -> None:
        """Recorder callback - runs on the recorder's own background thread."""
        GLib.idle_add(self._on_recording_done_main, audio_path)

    def _on_recording_done_main(self, audio_path: Optional[str]) -> bool:
        """Handle a finished recording (auto-stopped or cancelled early) on the GTK main thread."""
        self._listening = False
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

    async def _transcribe(self, audio_path: str) -> str:
        """Run (blocking) whisper.cpp transcription off the GTK thread."""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self.stt.transcribe, audio_path)
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

        text = str(result).strip()
        if self.window:
            self.window.set_status(f'Heard: "{text}"')
        self._submit(text)
        return GLib.SOURCE_REMOVE

    def _on_user_input(self, window: CajitaWindow, text: str) -> None:
        """Handle typed user input."""
        self._submit(text)

    def _submit(self, text: str) -> None:
        """Send text through the assistant's tool-calling pipeline."""
        logger.info(f"User input: {text}")

        if not self.assistant or not self.llm or not self.llm.is_available():
            if self.window:
                self.window.set_response("LLM not available")
                self.window.set_orb_state("error")
            return

        if self.window:
            self.window.set_orb_state("processing")
            self.window.set_status("Processing...")

        self._async.run(self.assistant.handle(text, on_tool_call=self._on_tool_call), self._on_response_ready)

    def _on_tool_call(self, name: str, _arguments: dict) -> None:
        """Assistant callback - runs on AsyncBridge's background thread."""
        GLib.idle_add(self._on_tool_call_main, name)

    def _on_tool_call_main(self, name: str) -> bool:
        """Show which skill is currently running instead of a generic status."""
        if self.window:
            self.window.set_status(f"{name.replace('_', ' ').capitalize()}...")
        return GLib.SOURCE_REMOVE

    def _on_response_ready(self, result: object) -> bool:
        """Deliver an assistant response (or error) back on the GTK main thread."""
        if isinstance(result, Exception):
            logger.error(f"Processing error: {result}")
            if self.window:
                self.window.set_response(f"Error: {result}")
                self.window.set_orb_state("error")
                self.window.set_status("Ready")
            return GLib.SOURCE_REMOVE

        response = str(result)
        if self.window:
            self.window.set_response(response)
            self.window.set_orb_state("idle")
            self.window.set_status("Ready")

        if self.tts and self.tts.is_available() and self.config.notification_enabled:
            self._async.run(self._speak(response))

        return GLib.SOURCE_REMOVE

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
        try:
            await loop.run_in_executor(None, self.player.play_bytes, tts_bytes)
        finally:
            if use_vad:
                self.barge_in_monitor.stop()

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

    def _parse_args(self, args: list[str]) -> None:
        """Parse command line arguments."""
        for arg in args:
            if arg == "--debug":
                self.config.set("debug-mode", "true")
            elif arg == "--privacy":
                self.privacy.enable_local_mode()
            elif arg == "--local-only":
                self.privacy.enable_local_mode()
            elif arg.startswith("--model="):
                model = arg.split("=", 1)[1]
                self.config.set("model", model)
                self._model_override = model
            elif arg.startswith("--voice="):
                voice = arg.split("=", 1)[1]
                self.config.set("piper-voice", voice)
                if self.tts:
                    self.tts.voice = voice
                    self.tts.voice_path = self.tts._get_voice_path(voice)


def _set_log_level(debug: bool) -> None:
    """Apply the debug-mode setting to the root logger and its handlers.

    `logging.basicConfig` pins both the root logger and its handler to the
    same level, so flipping only the logger level would still be filtered by
    the handler - set both. `SHANI_DEBUG` stays an always-on override.
    """
    level = logging.DEBUG if (debug or os.environ.get("SHANI_DEBUG")) else logging.INFO
    logging.getLogger().setLevel(level)
    for handler in logging.getLogger().handlers:
        handler.setLevel(level)


def main() -> None:
    """Main entry point for shani-chronoa."""
    # Set up logging. The persisted debug-mode gsetting is read after the
    # app (and its config) exist so a debug toggle takes effect at startup;
    # --debug is applied again in do_command_line once argv is parsed.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app = ChronoaApplication()
    _set_log_level(app.config.debug_mode)

    logger = logging.getLogger(__name__)
    logger.info("Starting Shani Chronoa v0.1.0")

    exit_status = app.run(sys.argv)
    logger.info(f"Shani Chronoa exited with status {exit_status}")
    sys.exit(exit_status)
