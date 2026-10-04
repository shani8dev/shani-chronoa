"""The application object: startup, the window, actions, command-line arguments; the rest of its behaviour is mixed in from voice, brain, conversation and desktop_integration."""

import logging
import sys
import os
from typing import Optional, Union

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gio', '2.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw  # type: ignore


from shani_chronoa.assistant import Assistant

from shani_chronoa.asyncbridge import AsyncBridge
from shani_chronoa.audio import AudioPlayer, AudioRecorder, BargeInMonitor
from shani_chronoa import pipewire, conversation_store, ask_bridge
from shani_chronoa.config import ChronoaConfig, PrivacyManager
from shani_chronoa.hardware_profile import HardwareProfile
from shani_chronoa.stt import STT
from shani_chronoa.ollama_llm import OllamaLLM
from shani_chronoa.cloud_llm import CloudLLMChain
from shani_chronoa.senses.context import ContextBuilder
from shani_chronoa.senses.store import PerceptStore
from shani_chronoa.senses.scheduler import AmbientScheduler
from shani_chronoa.triggers import EventEngine
from shani_chronoa.tts import PiperTTS
from shani_chronoa.wakeword import WakeWordListener
from shani_chronoa.gui import (
    AssistantState,
    ChronoaWindow,
    QuickAskWindow,
    browser_available,
    make_question_presenter,
)

from .common import (  # noqa: F401
    _set_log_level,
)
from .voice import VoiceMixin
from .brain import BrainMixin
from .conversation import ConversationMixin
from .desktop_integration import DesktopIntegrationMixin

logger = logging.getLogger(__name__)




class ChronoaApplication(VoiceMixin, BrainMixin, ConversationMixin, DesktopIntegrationMixin, Gtk.Application):
    """Main Shani Chronoa GTK Application."""

    def __init__(self) -> None:
        super().__init__(
            application_id="dev.shani.chronoa",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.config = ChronoaConfig()
        self.hardware = HardwareProfile()
        self.privacy = PrivacyManager(self.config)
        self.stt: Optional[STT] = None
        self.llm: Optional[Union[OllamaLLM, CloudLLMChain]] = None
        self.tts: Optional[PiperTTS] = None
        self.assistant: Optional[Assistant] = None
        # The senses layer's read side. Until these existed the whole layer
        # was reachable only from the headless CLI, so a percept it produced
        # never reached an LLM turn; the Assistant rebuilds the percept block
        # from `percept_store` on every request (see assistant.build_messages).
        self.percept_store: Optional[PerceptStore] = None
        self.percept_context: Optional[ContextBuilder] = None
        self.window: Optional[ChronoaWindow] = None
        #: The Quick Ask popup, created on first use and reused after. Declared
        #: here rather than in do_startup so any caller can ask whether it is
        #: open without the application having been started.
        self._quick_ask_window: Optional[QuickAskWindow] = None
        #: The in-app browser, created on first use. Optional: WebKitGTK may not
        #: be installed, and `open_browser` says so rather than failing.
        self._browser_window = None
        self._settings_window: Optional[Gtk.Window] = None
        # A chosen device is threaded into every capture path at once -
        # recorder, barge-in monitor and wake word each run their own
        # `pw-record`, so setting it on only one of them would have the
        # assistant listening and waking on different microphones.
        #
        # Each is resolved against the live graph first, because `pw-record`
        # silently ignores an unknown --target and records from the default
        # device instead - so an unplugged headset would look honoured while
        # quietly using the laptop mic. The problem is kept for `do_activate`,
        # where a window exists to say it in.
        self._device_warnings: "list[str]" = []
        in_target, in_problem = pipewire.resolve_target(
            self.config.audio_input_device, "input"
        )
        out_target, out_problem = pipewire.resolve_target(
            self.config.audio_output_device, "output"
        )
        for problem in (in_problem, out_problem):
            if problem:
                logger.warning("Audio device: %s", problem)
                self._device_warnings.append(problem)
        # Replaced by the real thing in _init_components. Declared here so a
        # partially-constructed app still has the attribute: do_shutdown runs
        # on paths that never reached _init_components, and a shutdown path
        # that raises is worse than one that skips a step.
        self.sense_scheduler = None
        self._model_download_thread = None
        self.recorder = AudioRecorder(target=in_target or None)
        self.player = AudioPlayer(target=out_target or None)
        self.barge_in_monitor = BargeInMonitor(target=in_target or None)
        self.wakeword = WakeWordListener(
            phrase=self.config.wake_phrase, target=in_target or None
        )
        self._listening = False
        self._voice_turn = False
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

        # libadwaita requires adw_init() before any Adw widget exists, and the
        # settings window is entirely Adw.PreferencesPage/SwitchRow/EntryRow.
        # The failure mode is silent - the rows fall back to unstyled GTK, so
        # the dialog looks *less* designed than the plain GTK4 one it replaced
        # - so this line looks redundant and is not. It is idempotent.
        Adw.init()

        # `do_startup`, not `_open_settings`: the settings window is built lazily
        # on first Ctrl+, so a presenter installed there would not exist for a
        # normal run - dead code behind an even rarer one. This hook runs on every
        # launch, before any tool can be dispatched. The window itself does not
        # exist yet (`do_activate` builds it), which is why the presenter takes a
        # getter rather than a window.
        ask_bridge.set_presenter(self._spoken_presenter(make_question_presenter(lambda: self.window)))

        # Initialize components based on hardware and config
        self._init_components()

        # Create actions
        self._create_actions()

        logger.info("Shani Chronoa startup complete")

    def do_shutdown(self) -> None:
        """Release background mic/playback resources before the app exits."""
        logger.info("Shani Chronoa shutting down...")
        self._stop_global_shortcut()
        # Answer a prompt that is still on screen. The window is about to be
        # destroyed, so nobody is ever going to click it, and the tool loop is
        # blocked on that answer - `AsyncBridge.shutdown()` below joins its
        # thread, so an unanswered event is minutes of a quit that looks hung.
        if self.window is not None:
            self.window.abandon_pending_question()
        self.wakeword.stop()
        self.player.stop()
        self.barge_in_monitor.stop()
        self.recorder.cancel_auto_stop()
        # Bounded join, so a sense that is mid-poll when the user quits cannot
        # hold the shutdown open.
        # getattr, not an attribute access: conftest's `stubbed_app` builds the
        # app with __new__ and hand-assigns only what a test needs, so the
        # attribute genuinely may not exist. A shutdown that raises is worse
        # than one that skips a step, and every other release below this line
        # has the same exposure.
        scheduler = getattr(self, "sense_scheduler", None)
        if scheduler is not None:
            scheduler.stop()
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
        self._start_global_shortcut()
        # Every consent-gated sense in Settings was inert until this line: the
        # scheduler was constructed but never started, so a user could enable all
        # 44 switches and the assistant would perceive nothing at all. Start() is
        # idempotent, so raising the window again does not spawn a second thread.
        self.sense_scheduler.start()
        if not self.window:
            self.window = ChronoaWindow(self)
            # the wake phrase only listens between turns
            self.window.state_observers = [
                lambda s: self.wakeword.resume() if s is AssistantState.IDLE else self.wakeword.pause()
            ]
            self.window.connect("user-input", self._on_user_input)
            self.window.set_browser_available(browser_available())
            # A window raised by a global shortcut arrives with the caret
            # somewhere else, so the first thing a person types is lost.
            self.window.focus_input()
            if self.assistant:  # reopening the app shows the conversation it was in
                self.window.show_conversation(self.assistant.visible_turns())
            self.attach_regenerate()
            # A rejected device outranks the ordinary status: it explains why
            # the microphone the user picked is not the one in use.
            self.window.set_status(
                self._device_warnings[0] if self._device_warnings else self._llm_status_text()
            )
            self.window.present()
            # A fresh install cannot think, hear or speak naturally yet: offer setup once.
            GLib.idle_add(lambda: (self._open_setup() if self._setup_needed() else None, False)[1])
        else:
            self.window.present()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """Handle command line arguments."""
        args = command_line.get_arguments()
        self._parse_args(args)
        # Files handed in from outside - "Open with Chronoa" in a file manager
        # (the desktop entry's %U), or `shani-chronoa photo.png` - land in the
        # attachment bar exactly as a drop would. Resolved against the cwd of the
        # process that invoked us, not ours: a second launch is forwarded to this
        # running instance, and its relative paths mean its own directory.
        handed = launch_paths(args[1:], command_line.get_cwd() or os.getcwd())
        ask = next((a.split("=", 1)[1] for a in args[1:] if a.startswith("--ask=")), "")
        # --debug (and a persisted debug-mode setting) take effect on the
        # running process, not just the next launch.
        _set_log_level(self.config.debug_mode)
        # do_startup (and _init_components) already ran with the default
        # model before argv was parsed, so apply a --model= override here.
        if self._model_override and self.llm:
            self.llm.model = self._model_override
            logger.info(f"Model overridden via --model=: {self._model_override}")
        self.activate()
        if self.window is not None and handed:
            self.window.add_attachments(handed)
        if self.window is not None and ask.strip():
            # Through the window's own send path, so the question shows in the
            # transcript with its attachments, as if it had been typed.
            GLib.idle_add(lambda: (self.window.submit_text(ask.strip()), False)[1])
        # Verbs for a desktop's own custom-shortcut settings and the dock menu
        # (sayri's `sayri toggle`): work on any session, portal or not.
        if "--listen" in args[1:] or "--toggle-listening" in args[1:]:
            GLib.idle_add(lambda: (self.activate_action("toggle-listening", None), False)[1])
        if "--new-conversation" in args[1:]:
            GLib.idle_add(lambda: (self.activate_action("reset-conversation", None), False)[1])
        if "--setup" in args[1:]:
            # `shani-chronoa --setup`: open first-run setup now, done or not
            GLib.idle_add(lambda: (self._open_setup(), False)[1])
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

        # Initialize STT
        self.stt = self._build_stt()
        if not self.stt.is_available():
            logger.warning(
                "%s not available - STT disabled", self._stt_backend_label()
            )

        # Initialize LLM - Ollama first, always (local-first by design).
        self.llm = OllamaLLM(
            host=self.config.ollama_host,
            model=model,
            context_window=self.hardware.get_context_window(),
        )
        self._ollama_available = self.llm.is_available()
        if not self._ollama_available:
            logger.warning("Ollama not available")
            self._use_local_server()
        if not self._ollama_available:
            self._maybe_enable_cloud_fallback()

        # Perception is optional context, so this is wired unconditionally and
        # degrades to nothing: with no percepts stored, the assistant's prompts
        # are byte-for-byte what they were before the senses layer existed.
        # `PerceptStore()`'s default durable path is the same file
        # `shani-chronoa-sense remember` and the memory sense write to, so
        # anything persisted by the CLI is already live on the next turn here.
        self.percept_store = PerceptStore()
        # The `consent=` argument is load-bearing: omitted, it silently reverts
        # to injecting a percept after its sense is withdrawn. Do not "simplify"
        # this back to a bare ContextBuilder() - every test constructs one, and
        # that is not a licence to do so here.
        self.percept_context = ContextBuilder(
            consent=lambda sense: self.config.sense_allowed(sense))
        # Built here, started in do_activate. Constructing the scheduler is
        # inert; start() spawns the polling thread, and several tests call
        # _init_components directly, so starting it in this method would put a
        # live poller in the middle of the suite. It shares the app's own
        # PerceptStore, so what a sense sees reaches the conversation through the
        # same context builder the assistant already reads - a separate store
        # would be a second, invisible one.
        self.event_engine = EventEngine()
        self.sense_scheduler = AmbientScheduler(
            store=self.percept_store,
            event_engine=self.event_engine,
        )
        self.assistant = Assistant(
            self.llm,
            percept_store=self.percept_store,
            context_builder=self.percept_context,
            session_path=conversation_store.active_path(conversation_store.session_dir()),
        )

        # Initialize TTS
        self.tts = PiperTTS(voice=self.config.piper_voice)
        self.tts.rate = self.config.get_double("speech-rate", 1.0) or 1.0
        if not self.tts.is_available():
            logger.warning("Piper TTS not available - TTS disabled")

        if not self.recorder.is_available():
            logger.warning("No audio recording backend (pw-record/arecord) found - voice input disabled")
        if not self.player.is_available():
            logger.warning("No audio playback backend (pw-play/aplay) found - spoken replies disabled")

        if not self.wakeword.is_available():
            logger.info(f"Wake phrase unavailable ({self.wakeword.unavailable_reason()}) - push-to-talk only")
        elif self.config.wake_word_enabled:
            self._set_wake_word_active(True)

        self._sync_autostart()
        self._sync_background_mode()
        try:
            from shani_chronoa import secret_store
            from shani_chronoa.config import API_KEY_SETTINGS
            secret_store.migrate(self.config, API_KEY_SETTINGS)
        except Exception as exc:  # noqa: BLE001 - a keyring problem must not stop startup
            logger.debug("API key migration skipped: %s", exc)

        # Read the STT off the live object: the model is resolved inside
        # `_build_stt`, and interpolating a local here instead raised
        # UnboundLocalError, which took all of `do_startup` down with it.
        logger.info(f"Initialized: model={model}, "
                    f"stt={self._stt_backend_label()} "
                    f"({getattr(self.stt, 'model', '?')}), "
                    f"profile={self.hardware.profile}")

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

        # Plan mode: the assistant may read the machine but not change it, so
        # "what would you do about X" can be answered without granting the
        # permission to do it. Reachable as an action because a mode the user
        # cannot see is worse than not having one.
        plan_action = Gio.SimpleAction.new("toggle-plan-mode", None)
        plan_action.connect("activate", self._toggle_plan_mode)
        self.add_action(plan_action)

        # Wake-word (hands-free) toggle action - no settings UI exists yet
        # to flip this, so it's reachable via a keyboard accelerator too,
        # same gap as toggle-privacy already had.
        wake_word_action = Gio.SimpleAction.new("toggle-wake-word", None)
        wake_word_action.connect("activate", self._toggle_wake_word)
        self.add_action(wake_word_action)
        self.set_accels_for_action("app.toggle-wake-word", ["<Ctrl><Shift>W"])

        # Reset conversation - Assistant.reset() existed but nothing ever
        # called it; the only way to clear history was restarting the app.
        # Quick Ask: a one-off question that is not a conversation. Bound to
        # Ctrl+Shift+A here; the portal shortcut that should open it without
        # focusing the main window is the natural follow-up and is not done.
        quick_action = Gio.SimpleAction.new("quick-ask", None)
        quick_action.connect("activate", self.quick_ask)
        self.add_action(quick_action)
        self.set_accels_for_action("app.quick-ask", ["<Ctrl><Shift>a"])

        # The in-app browser. WebKitGTK is optional, so the action is only
        # *sensitive* when it is there rather than being absent: a shortcut that
        # does nothing is worse than one that says why. `open_browser` reports
        # the missing package in its own words when it is pressed anyway.
        # The full body register, one key from the conversation. The strip in the
        # window answers "what is it doing now"; this answers "what did it do".
        body_action = Gio.SimpleAction.new("show-body", None)
        body_action.connect("activate", lambda *a: self.window.open_body_page())
        self.add_action(body_action)
        self.set_accels_for_action("app.show-body", ["<Ctrl><Shift>B"])

        search_action = Gio.SimpleAction.new("find-in-conversation", None)
        search_action.connect("activate", lambda *a: self.window.toggle_search())
        self.add_action(search_action)
        self.set_accels_for_action("app.find-in-conversation", ["<Ctrl>f"])

        # The sidebar, on F9 - the same key every browser and editor uses for it,
        # and a panel list that can only be reached by clicking one row is a panel
        # list a keyboard user does not have.
        sidebar_action = Gio.SimpleAction.new("toggle-sidebar", None)
        sidebar_action.connect("activate", lambda *a: self.window.toggle_sidebar())
        self.add_action(sidebar_action)
        self.set_accels_for_action("app.toggle-sidebar", ["F9"])

        browser_action = Gio.SimpleAction.new("open-browser", None)
        browser_action.connect("activate", self.open_browser)
        self.add_action(browser_action)
        self.set_accels_for_action("app.open-browser", ["<Ctrl><Shift>u"])
        browser_action.set_enabled(browser_available())

        reset_action = Gio.SimpleAction.new("reset-conversation", None)
        reset_action.connect("activate", self._reset_conversation)
        self.add_action(reset_action)
        self.set_accels_for_action("app.reset-conversation", ["<Ctrl>N"])

        # Many conversations: open one by id (from the Conversations menu, or
        # after a `conversations` skill call switched the index).
        switch_action = Gio.SimpleAction.new("switch-conversation", GLib.VariantType.new("s"))
        switch_action.connect("activate", lambda _a, p: self._open_conversation(p.get_string()))
        self.add_action(switch_action)
        delete_action = Gio.SimpleAction.new("delete-conversation", GLib.VariantType.new("s"))
        delete_action.connect("activate", lambda _a, p: self._delete_conversation(p.get_string()))
        self.add_action(delete_action)

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

        model_download_action = Gio.SimpleAction.new("toggle-model-download", None)
        model_download_action.connect("activate", self._toggle_model_download)
        self.add_action(model_download_action)

        fetch_model_action = Gio.SimpleAction.new("download-speech-model", None)
        fetch_model_action.connect("activate", self._download_speech_model)
        self.add_action(fetch_model_action)

        # Debug-logging toggle - the settings row goes through this action so
        # the live log-level change and the persisted setting stay in one path.
        debug_action = Gio.SimpleAction.new("toggle-debug", None)
        debug_action.connect("activate", self._toggle_debug)
        self.add_action(debug_action)

        # `player.stop()` was previously only reachable via `_begin_listening`,
        # so stopping the assistant meant also opening the mic.
        stop_speaking_action = Gio.SimpleAction.new("stop-speaking", None)
        stop_speaking_action.connect("activate", self._stop_speaking)
        self.add_action(stop_speaking_action)
        self.set_accels_for_action("app.stop-speaking", ["Escape"])

        # Settings window
        settings_action = Gio.SimpleAction.new("open-settings", None)
        settings_action.connect("activate", self._open_settings)
        self.add_action(settings_action)
        self.set_accels_for_action("app.open-settings", ["<Ctrl>comma"])

        # First-run setup (setup_wizard.py): brain, ears, voice.
        setup_action = Gio.SimpleAction.new("setup", None)
        setup_action.connect("activate", lambda *_: self._open_setup())
        self.add_action(setup_action)

    def _set_status(self, message: str) -> None:
        if self.window is not None:
            self.window.set_status(message)

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


def launch_paths(args: "list[str]", cwd: str) -> "list[str]":
    """The file arguments of a launch: paths or file:// URIs, flags skipped.

    Only `file://` URIs are taken - a desktop entry's %U can also pass http(s)
    URLs, and fetching one would be network access nobody asked for here.
    Paths that do not exist are passed on: `attachments.accept` is the one
    place that decides what may be attached, and says why it refused.
    """
    out = []
    for arg in args:
        if not arg or arg.startswith("-"):
            continue
        if "://" in arg:
            if not arg.startswith("file://"):
                logger.info("Ignoring a non-file URI handed to Chronoa: %s", arg.split("://", 1)[0])
                continue
            path = Gio.File.new_for_uri(arg).get_path()
            if path:
                out.append(path)
            continue
        out.append(os.path.normpath(os.path.join(cwd, os.path.expanduser(arg))))
    return out


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
