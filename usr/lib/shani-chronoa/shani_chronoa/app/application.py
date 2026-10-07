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
        # The inbound channel. `_gateway_owner` and the two lists live here rather
        # than only in `_create_actions`, because `_export_gateways()` reads and
        # clears `_gateway_owner` and a reload can reach it before any action was
        # ever created.
        self._gateways = None
        self._gateway_owner = None
        self._gateway_entries: list = []
        self._gateway_errors: list = []
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
        self._start_hidden = False
        self._hidden_hold = False

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

        # Same hook, same getter, different presenter: the question one answers
        # "pick an option", this one answers "type a sentence" - the free-form
        # path a permission question's "Provide feedback" offer leads to.
        # `set_text_presenter` had no caller anywhere, so that path always
        # resolved to an empty answer: a dead option. Installed deliberately
        # *next to* its question sibling, not in a settings window that is
        # built lazily, or feedback could not arrive on a normal run.
        from shani_chronoa.gui.questions import make_text_presenter
        ask_bridge.set_text_presenter(make_text_presenter(lambda: self.window))

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

    def _release_hidden_hold(self) -> None:
        """Drop the explicit hold a start-hidden activation took."""
        if self._hidden_hold:
            self.release()
            self._hidden_hold = False

    def _export_gateways(self) -> None:
        """Register the configured channels and put them on the bus.

        Kept out of the constructor so a test can register a gateway and export
        it deliberately, and so "nothing is listening" is the state of a machine
        that has not asked for it.

        **This used to be unreachable on every install.** It built the registry,
        found `names()` empty - because *nothing in the tree ever called
        `Registry.register()`*, measured with an AST search rather than a grep -
        and returned. A complete, tested inbound channel with no switch to turn
        it on. `gateways` in the schema is that switch, and `_reload_gateways()`
        re-reads it so a channel added in Settings works without a restart.
        """
        from shani_chronoa import gateway as gateway_module
        if self._gateways is None:
            self._gateways = gateway_module.Registry(self._submit_gateway_text)
        entries, errors = gateway_module.parse_config(
            self.config.get("gateways", "") or "")
        for message in errors:
            # **Loud, and per entry.** A channel name that could not be parsed is
            # a switch that looks set and does nothing, which is the defect this
            # whole area keeps producing.
            logger.warning("gateway setting ignored: %s", message)
        for name in list(self._gateways.names()):
            self._gateways.unregister(name)
        for name, grant in entries:
            try:
                self._gateways.register(name, grant)
            except ValueError as exc:            # parse_config should have caught it
                logger.warning("gateway %r refused: %s", name, exc)
        self._gateway_entries = entries
        self._gateway_errors = errors
        # Release the previous owner **and unregister the object**. `bus_unown_name`
        # frees the name but leaves the object path taken, and `register_object`
        # then refuses - so unowning alone made every reload after the first a
        # silent no-op. Measured before the fix: reload 1 owned the name, reloads
        # 2-4 reported `owner=False, on_bus=False` while the registry still listed
        # the channel, and the Settings row said it was configured and nothing was
        # listening.
        if self._gateway_owner is not None or self._gateways.names() is not None:
            try:
                gateway_module.unexport(self._gateway_owner)
            except Exception:                   # noqa: BLE001 - a channel is optional
                logger.exception("could not release the previous gateway export")
        self._gateway_owner = None
        if not self._gateways.names():
            logger.info("gateways: none configured, so nothing is on the bus")
            return
        try:
            self._gateway_owner = gateway_module.export(self._gateways)
        except Exception as exc:  # noqa: BLE001 - a channel is optional
            logger.warning("could not export the gateway interface: %s", exc)

    def _reload_gateways(self) -> None:
        """Re-read `gateways` and re-export. Called when the setting changes."""
        try:
            self._export_gateways()
        except Exception:                       # noqa: BLE001
            logger.exception("could not reload the gateway configuration")

    def _submit_gateway_text(self, text: str, channel: str = "") -> str:
        """A channel's words, turned into a turn exactly as a typed one is.

        This is the whole security argument in one method: the text goes through
        `submit()` - the same entry the window's input uses - so it meets the same
        whitelist, the same consent keys and the same post-conditions, and lands
        in the same log. A gateway has no way to reach any of them directly.

        `channel` carries which surface said it ("telegram", "whatsapp",
        "phone"). It is needed because nothing downstream wants the name except
        one thing: remembering which surface a remembered fact came from, so
        a private fact captured on a channel never appears in another
        channel's recall. Default "" for the unspecified surfaces.
        """
        # `_submit` is the window's own entry and returns nothing - the reply
        # arrives asynchronously and lands in the window like any other turn.
        # So a channel gets its answer the same way a person does: in the
        # window. Returning a string here would mean running the turn twice.
        self._submit(text, channel=channel)
        return ""

    def _on_show_page(self, _action, parameter) -> None:
        """`app.show-page('settings:privacy')`, over D-Bus as well as in-process.

        Says so when the target does not exist rather than opening something
        nearby: a notification with a typo must not become a silently wrong page.
        """
        from shani_chronoa import pages
        target = parameter.get_string() if parameter is not None else ""
        if not self.show_page(target):
            logger.warning("no page %r. Pages are:\n%s", target, pages.describe())
            if self.window is not None:
                self.window.set_status(f"No page called {target!r}")

    def show_page(self, target: str) -> bool:
        """Show "<window>:<id>" from Python, for a caller holding the application."""
        from shani_chronoa import pages
        return pages.show(target, application=self, config=self.config)

    def do_activate(self) -> None:
        """Handle application activation."""
        logger.info("Activating Shani Chronoa")
        if self.window is not None and self._start_hidden:
            # A later launch forwards to this running instance: the user is
            # summoning the window. The startup-only flag must be cleared or
            # every later activation would keep it hidden.
            self._start_hidden = False
        self._start_global_shortcut()
        # Every consent-gated sense in Settings was inert until this line: the
        # scheduler was constructed but never started, so a user could enable all
        # 44 switches and the assistant would perceive nothing at all. Start() is
        # idempotent, so raising the window again does not spawn a second thread.
        self.sense_scheduler.start()
        if getattr(self, "_show_page", None):
            target = self._show_page
            self._show_page = ""
            from shani_chronoa import pages
            if not pages.show(target, application=self, config=self.config):
                logger.warning("--show-page=%r named no page. Pages are:\n%s",
                               target, pages.describe())
        if not self.window:
            self.window = ChronoaWindow(self)
            # A tray icon if the desktop has one. `build()` returns None where it
            # does not - GTK4 removed `Gtk.StatusIcon`, so this needs
            # libappindicator, which is not a dependency - and None is a normal
            # outcome, not an error. The window is the whole application either
            # way; the icon is one way of reaching it.
            from shani_chronoa.app import tray as _tray
            self._tray = _tray.build(self)
            self._export_gateways()
            # Carry the startup verdict onto the window, so a raise on an empty
            # machine says "No model yet" rather than "Ready".
            if getattr(self, "_can_answer", None) is not None:
                self.window.set_can_answer(self._can_answer,
                                           getattr(self, "_no_model_reason", ""))
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
            if not self._start_hidden:
                # `present()`, not `show()`: `show` only maps the window,
                # `present` maps it *and* asks the desktop to raise and focus
                # it. The start-hidden work swapped one for the other and the
                # assistant then opened behind whatever was already in front,
                # with nothing wrong-looking to explain it - and
                # `tests/test_sense_scheduler_is_live.py` failed on a stub
                # window that only ever implemented the older call.
                self.window.present()
                self._release_hidden_hold()
            # A fresh install cannot think, hear or speak naturally yet: offer setup once.
            if not self._start_hidden:
                GLib.idle_add(lambda: (self._open_setup() if self._setup_needed() else None, False)[1])
        elif not self._start_hidden:
            self.window.present()
            self._release_hidden_hold()
        elif not self._hidden_hold:
            # No window will ever be presented on this activation: hold the
            # application explicitly, because a GtkApplication with no window
            # of its own has no reason to stay alive. Released when the window
            # is first shown.
            self.hold()
            self._hidden_hold = True

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
            # **Name what is missing.** "Whisper.cpp not available" was logged on
            # a machine that had just had whisper-cpp installed from the
            # repositories, because `is_available()` requires the binary *and*
            # the model and the model is a download. So the one thing a person
            # reading the log could act on - "install whisper-cpp" - was already
            # done, and the message pointed somewhere else entirely.
            missing = []
            if not getattr(self.stt, "whisper_path", "") or not os.path.exists(
                    getattr(self.stt, "whisper_path", "")):
                missing.append("the whisper.cpp program (install whisper-cpp)")
            if not os.path.exists(getattr(self.stt, "model_path", "")):
                missing.append(
                    "a speech model (Settings -> Voice, or the setup wizard's "
                    "Ears page)")
            logger.warning(
                "%s cannot listen yet: %s. Speech input stays off until both "
                "are there", self._stt_backend_label(),
                " and ".join(missing) or "something unknown")

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
        # Say so on the window, not only in the log. With nothing installed the
        # state line read "Ready" while the line under it read "LLM unavailable":
        # two states, one screen, one of them false - and "Ready" is the label the
        # idle state always has, so it was true by construction on every machine.
        self._can_answer = bool(self.llm)
        # Which presence we are in, read from the machine rather than assumed:
        # a remembered ACTIVE after the model was released is exactly the claim
        # presence.py exists not to make.
        from shani_chronoa.presence import detect
        from shani_chronoa import local_llm
        self._presence = detect(local_llm.is_up)
        if not self._can_answer:
            self._no_model_reason = (
                "no language model is set up, so nothing can answer yet")

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
            config=self.config,
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
        # The same line used to claim `stt=Whisper.cpp (medium)` on the very run
        # that had just warned "STT disabled", because it interpolated the
        # backend's *label* rather than whether it can listen. Two log lines a
        # millisecond apart, disagreeing, is the kind of thing that costs an
        # afternoon; so the state is read from the object.
        stt_ready = bool(self.stt and self.stt.is_available())
        logger.info(f"Initialized: model={model}, "
                    f"stt={self._stt_backend_label() if stt_ready else 'none'}, "
                    f"model={getattr(self.stt, 'model', '?') if stt_ready else '-'}, "
                    f"listening={stt_ready}, "
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

        # Dictation: one long turn with no wake word and nothing held down.
        # <Ctrl><Shift>d, because <Ctrl><Shift>a is quick-ask and this is the
        # same kind of thing - say something, get a turn - only without the
        # twenty-second ceiling.
        dictate_action = Gio.SimpleAction.new("dictate", None)
        dictate_action.connect("activate", self.dictate)
        self.add_action(dictate_action)
        self.set_accels_for_action("app.dictate", ["<Ctrl><Shift>d"])

        # An inbound channel on the session bus, for a meeting transcript or a
        # message bridge to type into. It exposes exactly one method and cannot
        # reach a tool - see `gateway.py` for why the interface is one method
        # and lives on the bus rather than a socket. Exported only when a
        # gateway has been registered, so a machine with none has nothing
        # listening at all.
        self._gateways = None
        self._gateway_owner = None

        # One action reaching any page of any window: `show-page` with
        # "<window>:<id>" - "settings:privacy", "setup:review", "main:quick-ask".
        #
        # **Every page used to be reachable only by holding the mouse.** The
        # settings sections by typing in a search box, the wizard steps by
        # pressing Next, the main window by whatever was already open. So a
        # notification could not say "open Settings on Privacy", a keybinding
        # could not either, and a test could not - it had to guess at pixel
        # selectors, which is how one run produced four screenshots of four
        # "different" sections that were byte-identical while reporting every
        # step green. `pages.py` holds the ids and their aliases; this is the
        # doorway, and it answers over D-Bus as well as in-process.
        show_page_action = Gio.SimpleAction.new("show-page", GLib.VariantType.new("s"))
        show_page_action.connect("activate", self._on_show_page)
        self.add_action(show_page_action)

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

        # The right rail, on F10 - adjacent to F9 for the left one, because they
        # are the same question ("show me the state of this machine") about two
        # different halves of the window.
        rail_action = Gio.SimpleAction.new("toggle-rail", None)
        rail_action.connect("activate", lambda *a: self.window.toggle_rail())
        self.add_action(rail_action)
        self.set_accels_for_action("app.toggle-rail", ["F10"])

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

        start_hidden_action = Gio.SimpleAction.new("toggle-start-hidden", None)
        start_hidden_action.connect("activate", self._toggle_start_hidden)
        self.add_action(start_hidden_action)

        show_window_action = Gio.SimpleAction.new("show-window", None)
        # `present()` again: "show the window" from a tray icon or a
        # notification means raise it and focus it, not merely map it.
        show_window_action.connect(
            "activate", lambda _a, _p: self.window.present() if self.window else None)
        self.add_action(show_window_action)

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
        # A keybinding as well as the header button, because "set up" is the
        # answer to five panels' "not installed - the setup wizard's page installs
        # it" and a panel that names the wizard should not make you hunt for it.
        # `Ctrl+Shift+U` is the browser's, and an accelerator cannot be spelled
        # two ways for one chord - Shift+u and Shift+U are the same keystroke,
        # so taking it would have shadowed `app.open-browser`.
        self.set_accels_for_action("app.setup", ["<Ctrl><Shift>s"])

    def _set_status(self, message: str) -> None:
        if self.window is not None:
            self.window.set_status(message)

    def _parse_args(self, args: list[str]) -> None:
        """Parse command line arguments."""
        for arg in args:
            if arg == "--debug":
                self.config.set("debug-mode", "true")
            elif arg.startswith("--show-page="):
                # `--show-page=settings:privacy` opens Settings on Privacy,
                # `--show-page=setup:review` opens the wizard at the review step.
                # Applied after activation, because both windows have to exist
                # before a page can be shown in one of them.
                self._show_page = arg.split("=", 1)[1]
            elif arg == "--privacy":
                self.privacy.enable_local_mode()
            elif arg == "--local-only":
                self.privacy.enable_local_mode()
            elif arg.startswith("--model="):
                model = arg.split("=", 1)[1]
                self.config.set("model", model)
                self._model_override = model
            elif arg == "--hidden":
                # Start without a window: the autostart entry for wake-word use.
                self._start_hidden = True
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
