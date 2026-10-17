"""The application object: startup, the window, actions, command-line arguments; the rest of its behaviour is mixed in from voice, brain, conversation and desktop_integration."""

import logging
from pathlib import Path
import sys
import os
import threading
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
from shani_chronoa import pipewire, conversation_store, ask_bridge, planmode
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
        #: Set by `do_shutdown`, read by anything that builds a window. Declared
        #: here so it exists on a normal run, and read through `getattr` where a
        #: test's `__new__`-built application may not have it.
        self._shutting_down = False
        self._pending_idles: "set[int]" = set()
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
        ask_bridge.set_presenter(self._notifying_presenter(
            self._spoken_presenter(make_question_presenter(lambda: self.window))))

        # Same hook, same getter, different presenter: the question one answers
        # "pick an option", this one answers "type a sentence" - the free-form
        # path a permission question's "Provide feedback" offer leads to.
        # `set_text_presenter` had no caller anywhere, so that path always
        # resolved to an empty answer: a dead option. Installed deliberately
        # *next to* its question sibling, not in a settings window that is
        # built lazily, or feedback could not arrive on a normal run.
        from shani_chronoa.gui.questions import make_text_presenter
        ask_bridge.set_text_presenter(make_text_presenter(lambda: self.window))

        # The `browse` skill drives the in-app browser while this window runs,
        # so the person watches it; with no provider (MCP, a headless run) it
        # keeps its headless Chromium. `is_available` is asked per call, so a
        # machine without WebKitGTK simply has no provider.
        from shani_chronoa import browser_bridge
        from shani_chronoa.gui.browser import is_available as _browser_available
        browser_bridge.set_provider(self.browser_for_model, available=_browser_available)

        # Initialize components based on hardware and config
        self._init_components()

        # Create actions
        self._create_actions()

        # A call ringing on the paired phone, shown with Answer / Decline.
        from shani_chronoa.incoming_call import IncomingCallWatcher
        self.incoming_calls = IncomingCallWatcher(self.config)
        self.incoming_calls.start()
        # Files a phone shares to this computer over Bluetooth, asked about one by one.
        from shani_chronoa.obex_receive import ObexReceiver
        self.obex_receiver = ObexReceiver(self.config)
        self.obex_receiver.start()
        # A MoYoung watch kept connected, when its own switch is on.
        from shani_chronoa.watch_companion import WatchCompanion
        self.watch_companion = WatchCompanion(self.config, on_voice=self._watch_voice)
        self.watch_companion.start()
        # This computer as "Chronoa Remote", a Bluetooth keyboard for the phone, when its switch is on.
        from shani_chronoa.ble_peripheral import RemoteService
        self.phone_remote = RemoteService(self.config)
        self.phone_remote.start()
        # New texts on the paired phone, announced as they arrive (MAP notifications).
        from shani_chronoa.map_notify import MessageWatcher
        self.message_watcher = MessageWatcher(self.config)
        self.message_watcher.start()
        # This computer's music, for Bluetooth remotes (AVRCP target), when its switch is on.
        self.avrcp_target = None
        if self.config.get_bool("bluetooth-media-remote-enabled", False):
            from shani_chronoa.avrcp_target import AvrcpTarget
            self.avrcp_target = AvrcpTarget()
            self.avrcp_target.start()

        logger.info("Shani Chronoa startup complete")

    def _watch_voice(self, start: bool) -> None:
        """The watch's AI-voice button: start Chronoa listening, or end the turn.

        Called from the companion's thread, so handed to the GTK loop.

        **This listens on this computer's microphone, not the watch's.** The
        watch cannot be used as a microphone: it advertises Handsfree/Audio
        Sink/A2DP in SDP but bluez exposes only `MediaControl1` (AVRCP) for it
        and never creates an audio card, so no watch audio reaches this machine
        - measured 2026-10-08, three connect attempts and three `ConnectProfile`
        attempts, all with `/proc/asound/cards` unchanged. Da Fit's own manual
        describes the button as waking the AI voice *on the phone*, so this
        matches its behaviour rather than substituting for a missing feature.
        `watch_companion.py` carries the same finding and its evidence.

        The window is told, because a button on the wrist starting a microphone
        on the desk should not look like the watch is listening.
        """
        def act():
            if start and not self._listening:
                self._begin_listening()
                if self.window:
                    self.window.set_status("Listening on this computer's microphone "
                                            "(the watch cannot be one)")
            elif not start and self._listening:
                self.recorder.cancel_auto_stop()
            return False
        GLib.idle_add(act)

    def _queue_idle(self, work):
        """Queue `work` on the next idle turn, and remember the source id.

        **Remembering is the point.** An idle queued at *activation* is still
        pending if the loop ends before it runs, and the loop is drained inside
        teardown - so the work fires while the application is being taken apart.
        `_open_setup` refuses during shutdown and that catches the case this was
        written for, but a guard at the callee is one check on one path; a
        *cancelled* source is cancelled on every path, including the ones nobody
        has thought of yet.

        Returns the id, so a caller can cancel one thing early.
        """
        source = GLib.idle_add(work)
        self._pending_idles.add(source)
        return source

    def do_shutdown(self) -> None:
        """Release background mic/playback resources before the app exits."""
        # **Alive is a state with a flag, and building a window requires it.**
        # A `GLib.idle_add` queued at activation - "offer setup once" for a fresh
        # install - is still pending when the loop ends before it runs, and the
        # loop is drained *inside* teardown. So `_open_setup` ran against an
        # application that had already begun shutting down, and
        # `Gtk.Window(application=self)` took the interpreter with it: measured
        # as a segfault in `Gtk.py:575 __init__`, reached from `do_shutdown`, in
        # a chunked suite run - costing every test file after the one that hit.
        self._shutting_down = True
        # **Cancel what activation queued.** Every `_queue_idle` source is
        # removed before any component is released, so none of them runs against
        # an application that is being taken apart.
        #
        # `getattr`, not an attribute access, for the same reason the services
        # below use it: `_pending_idles` is created in `do_startup`, and *this*
        # is a different lifecycle method. A stub that shuts down without
        # starting - `tests/conftest.py`'s `stubbed_app` builds the application
        # with `__new__` and hand-assigns only what a test needs - reaches here
        # with the attribute absent, and the `AttributeError` costs the real
        # teardown that follows. Measured: this exact line broke
        # `test_asyncbridge.py::TestShutdownCleanup::
        # test_do_shutdown_stops_async_bridge_thread` the moment `_queue_idle`
        # began tracking source ids, and it is the same defect class as
        # `MathBlock` reading an attribute it never set.
        pending = getattr(self, "_pending_idles", None) or ()
        for source in list(pending):
            try:
                GLib.source_remove(source)
            except Exception:            # already fired, or a stale id
                pass
        if getattr(self, "_pending_idles", None) is not None:
            self._pending_idles.clear()
        logger.info("Shani Chronoa shutting down...")
        self._stop_global_shortcut()
        for name in ("incoming_calls", "obex_receiver", "watch_companion", "phone_remote",
                     "message_watcher", "avrcp_target"):
            service = getattr(self, name, None)
            if service is not None:
                service.stop()
        try:
            from shani_chronoa import landline
            landline.stop()
        except Exception:  # noqa: BLE001
            pass
        if getattr(self, "mascot", None) is not None:
            try:
                self.mascot.stop()
            except Exception:  # noqa: BLE001
                pass
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

    #: The reply styles the schema allows, in the order a menu shows them.
    REPLY_STYLES = ("ordinary", "brief", "explanatory")

    def _on_thinking(self, action, value) -> None:
        """Write the chosen thinking level, then set the state from config.

        Read back, as `_on_reply_style` does, so a refused value shows the
        level that is actually in force rather than the click.
        """
        from shani_chronoa.cloud_llm import THINKING_LEVELS
        level = value.get_string() if value is not None else ""
        if level in THINKING_LEVELS:
            self.config.set("thinking", level)
        action.set_state(GLib.Variant("s", self.config.thinking))
        self._refresh_strip()

    def _on_choose_project(self, _action, _param) -> None:
        """Pick the project folder, and turn Build mode on with it.

        Choosing a project is saying "work on this code", so Build mode comes on
        too - a project that only applied after a second switch would be a
        setting that silently did nothing.
        """
        dialog = Gtk.FileDialog(title="Choose the project folder", modal=True)

        def done(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return  # cancelled, or no portal: nothing chosen
            path = folder.get_path() if folder is not None else ""
            if path:
                self._set_project(path)
                rules = self.lookup_action("answer-rules")
                if rules is not None:
                    rules.change_state(GLib.Variant("s", "build"))

        dialog.select_folder(self.window, None, done)

    def _discover_mcp(self) -> None:
        import threading
        from shani_chronoa import mcp_client, tools

        def work():
            found = mcp_client.discover(tools.TOOLS, tools._HANDLER_FNS)
            if found:
                logger.info("MCP servers: %s", found)
            GLib.idle_add(self._mcp_discovered, found)

        threading.Thread(target=work, name="mcp-discovery", daemon=True).start()

    def _mcp_discovered(self, found) -> bool:
        window = getattr(self, "window", None)
        if window is not None and found:
            ready = sum(1 for v in found.values() if "tool" in v)
            window.set_status(f"MCP: {ready} of {len(found)} server(s) ready")
        return False

    def _set_project(self, path: str) -> None:
        from shani_chronoa import buildmode
        self.config.set("project-dir", path or "")
        # Read back, so a refused write leaves the old project in force.
        buildmode.set_project(self.config.project_dir or path or "")
        if self.window is not None:
            self.window.set_status(
                f"Project: {path}" if path else "No project folder")
        self._refresh_strip()

    def _on_answer_plan(self, action, value) -> None:
        """Set plan mode to the chosen row, through the toggle that says so."""
        want = (value.get_string() if value is not None else "") == "plan"
        if want != planmode.is_enabled():
            self._toggle_plan_mode(None, None)
        action.set_state(GLib.Variant("s", "plan" if planmode.is_enabled() else "working"))
        self._refresh_strip()

    def _on_reply_style(self, action, value) -> None:
        """Write the chosen style, then set the action's state from config.

        Read back rather than echoed: a value the schema refuses leaves the
        setting where it was, and the menu must then show that, not the click.
        """
        style = value.get_string() if value is not None else ""
        if style in self.REPLY_STYLES:
            self.config.set("reply-style", style)
        action.set_state(GLib.Variant("s", self.config.reply_style))
        self._refresh_strip()

    #: The words each menu offers, read from the strip so the app validates
    #: against the menu the person is actually looking at. **A second copy here
    #: would be a second thing to get wrong**: the first version of the two
    #: handlers referenced `_RULES` and `_HANDS` as though they were local names
    #: and raised `NameError` on every click.
    @property
    def _RULES_KEYS(self):
        from shani_chronoa.gui.window import _ModeStrip
        return {row[0] for row in _ModeStrip._CAPABILITY}

    @property
    def _EGRESS_KEYS(self):
        from shani_chronoa.gui.window import _ModeStrip
        return {row[0] for row in _ModeStrip._EGRESS}

    @property
    def _PRESENCE_KEYS(self):
        from shani_chronoa.gui.window import _ModeStrip
        return {row[0] for row in _ModeStrip._PRESENCE}

    @property
    def _AMBIENT_KEYS(self):
        from shani_chronoa.gui.window import _ModeStrip
        return {row[0] for row in _ModeStrip._AMBIENT}

    @property
    def _HANDS_KEYS(self):
        from shani_chronoa.gui.window import _ModeStrip
        return {row[0] for row in _ModeStrip._HANDS}

    def _on_answer_rules(self, action, value) -> None:
        """Write the "what it may change" choice, then read back.

        **This used to be two questions in one menu.** It crossed *may it change
        things* with *may anything leave this machine*, which are unrelated - so
        its four options could not be ordered against each other, and "Local only"
        decided where answers came from, which the chip beside it also decided.
        The two could contradict each other and did: with privacy on and
        `answer-mode=cloud`, this chip's neighbour read "Cloud" while the app
        answered locally. The privacy half now belongs to `answer-egress`.
        """
        key = value.get_string() if value is not None else ""
        if key not in self._RULES_KEYS:
            logger.warning("unknown answer-rules choice %r", key)
            return
        # **"Read only" is `explore`, not plan mode.** It was plan mode, and
        # that conflated two different ideas under one word: a *permission*
        # (may it change anything) and a *workflow* (work out what it would
        # do). The permission is what this chip asks, and `explore` is the
        # permission layer's own name for it - `permissions.explore_refuses`
        # is the check that answers it.
        #
        # Plan mode is a workflow and now has its own control, so the two can
        # be combined deliberately rather than by accident: read-only *and*
        # planning, or read-only and acting.
        from shani_chronoa import buildmode

        buildmode.set_enabled(key == "build")
        action.set_state(GLib.Variant("s", key))
        self._refresh_strip()

    @property
    def _PERMISSION_KEYS(self):
        """The keys the Auto chip and this action accept.

        **The chip's own keys, checked against the module's modes.** The chip
        says "ask" and "auto" because those are the two things a person decides;
        the module calls them `default` and `dont_ask`, which are two things an
        implementer has to look up. Keeping both lists is the drift this mapping
        exists to prevent - and the first version checked the action's key
        against the *module's* names, so "ask" and "auto" were refused and the
        chip did nothing.
        """
        # **`explore` is not one of them.** It used to be accepted here so that
        # Settings' combo could reach it through this action, but that combo
        # writes `permissions.set_mode` directly and never came here - so the
        # third key was a mode with no control. Read-only is reached in
        # Settings, or by asking in chat while plan mode is on.
        return ("ask", "auto")

    def _on_answer_permission(self, action, value) -> None:
        """Set how Chronoa treats a risky call: ask, refuse, or look only.

        **It was in Settings and nowhere else, which made it invisible from the
        window that does the asking.** Three postures existed - `default`,
        `dont_ask`, `explore` - and the one you could not see from the
        conversation was `dont_ask`, the only one that answers "it will not ask
        me before doing this" with *no*.

        **It is not persisted, and this must not change that.** `permissions`
        deliberately keeps the posture in memory only, so a mode that suppresses
        prompts cannot become the silent default at the next unattended login.
        Putting it on the strip does not make it survive a restart, and a chip
        that looked sticky would be the worst outcome - so the tooltip says so.

        **An unknown key is refused, never treated as the permissive one.** A
        typo in a safety mode must not mean "ask nothing".
        """
        from shani_chronoa import permissions

        key = value.get_string() if value is not None else ""
        if key not in self._PERMISSION_KEYS:
            logger.warning("unknown answer-permission choice %r", key)
            action.set_state(GLib.Variant("s", permissions.get_mode()))
            return
        # **Two keys, two modes, and nothing else in the table.**
        #
        # This carried an `"explore"` entry for a while. It was **unreachable** -
        # the key is rejected above - and it read as a working route to a mode
        # the strip does not offer, which is the one thing a lookup table must
        # not do. Settings' combo writes `permissions.set_mode` directly and
        # never came through here, which is why `_PERMISSION_KEYS` excludes
        # `explore` and why the mapping excludes it too. Read-only left the
        # strip deliberately; see `buildmode.read_only_note`.
        wanted = {"ask": permissions.Mode.DEFAULT,
                  "auto": permissions.Mode.DONT_ASK}.get(key)
        if wanted is None:
            logger.warning("unknown answer-permission choice %r", key)
            action.set_state(GLib.Variant("s", permissions.get_mode()))
            return
        permissions.set_mode(wanted)
        action.set_state(GLib.Variant("s", permissions.get_mode()))
        self._refresh_strip()

    def _on_answer_egress(self, action, value) -> None:
        """Write "where it may go": the egress lock and the engine together.

        **One action because one decision.** "Keep it local" means both that
        nothing leaves the machine and that questions are answered here, and
        offering those separately is what let the strip show a cloud answer it was
        not giving. So the three reachable states are chosen as a pair and the
        contradictory one - local-only *and* cloud - is not offered rather than
        offered and ignored.

        `answer-mode` keeps its three stored values underneath; this writes it
        rather than replacing it, because it is read by the engine and by the
        wizard.
        """
        key = value.get_string() if value is not None else ""
        if key not in self._EGRESS_KEYS:
            logger.warning("unknown answer-egress choice %r", key)
            return
        if key == "this-machine":
            mode = "local"
            self.config.set("privacy-mode", True)
            self.config.set("cloud-fallback-enabled", False)
        elif key == "local-answers":
            mode = "local"
            self.config.set("privacy-mode", False)
            self.config.set("cloud-fallback-enabled", False)
        elif key == "cloud":
            mode = "cloud"
            self.config.set("privacy-mode", False)
            self.config.set("cloud-fallback-enabled", True)
        else:
            mode = "local-cloud"
            self.config.set("privacy-mode", False)
            self.config.set("cloud-fallback-enabled", True)
        self.config.set("answer-mode", mode)
        action.set_state(GLib.Variant("s", key))
        self._apply_answer_mode_if_present()
        self._refresh_strip()

    def _on_answer_presence(self, action, value) -> None:
        """Move to the chosen presence state, and say what happened.

        **Applying a state is not writing a setting.** ACTIVE and DROWSY swap
        which weights the local server holds; SLEEPING stops it. That is a
        `systemctl` call and a cold start, so **the work runs off the main loop**
        - the same reason `answer-model` restarts there, and for the same reason:
        done on the loop it freezes the window for the duration.

        **The choice is recorded only on success.** `presence.apply` reports
        `(reached, reason)` rather than raising, so a stop that fails leaves the
        model loaded while the chip would read "Asleep" - the chip reporting a
        state the machine is not in, which is the failure this strip has now hit
        three times. So on failure the chip redraws from what is actually true.
        """
        key = value.get_string() if value is not None else ""
        if key not in self._PRESENCE_KEYS:
            logger.warning("unknown answer-presence choice %r", key)
            return
        from shani_chronoa import local_llm, presence
        from shani_chronoa.gateway import off_the_main_loop

        target = presence.Presence(key)
        self._presence_pending = target

        def work() -> None:
            try:
                reached, reason = presence.apply(
                    target, is_up=local_llm.is_up,
                    wake=local_llm.start_service, sleep=local_llm.stop_service)
            except Exception:                           # noqa: BLE001
                logger.warning("presence change failed", exc_info=True)
                GLib.idle_add(self._presence_finished, False, "it could not be changed")
                return
            if reached:
                self._presence = target
            GLib.idle_add(self._presence_finished, reached, reason)

        off_the_main_loop(work, "answer-presence")

    def _presence_finished(self, reached: bool, reason: str) -> bool:
        """Back on the loop: show the outcome, and the truth either way."""
        action = self.lookup_action("answer-presence")
        if action is not None:
            action.set_state(GLib.Variant(
                "s", self._presence.value if reached else ""))
        if not reached:
            self.window.set_status(f"Still here: {reason}") \
                if self.window is not None else None
        self._presence_pending = None
        self._refresh_strip()
        return False

    def _on_answer_ambient(self, action, value) -> None:
        """Write "when it may act": only on request, or in the background.

        The setting is `background-mode-enabled`, which gates the daemon surface.
        It is on the strip because it decides whether Chronoa acts at all while
        you are away, which is a larger trust question than either other chip and
        was reachable only from Settings until now.
        """
        key = value.get_string() if value is not None else ""
        if key not in self._AMBIENT_KEYS:
            logger.warning("unknown answer-ambient choice %r", key)
            return
        self.config.set("background-mode-enabled", key == "background")
        action.set_state(GLib.Variant("s", key))
        self._sync_background_mode()
        self._refresh_strip()

    def _on_hands_free(self, action, value) -> None:
        """Write the "how it hears you" choice, then read back.

        Wake word and talk over are both about the microphone, and they interact -
        talking over a reply only matters when the mic is already open - so the
        pair is one menu with four words rather than two booleans a person has to
        multiply. Each switch keeps its own action and its own consent, so this
        only decides which of the four combinations is being asked for.
        """
        key = value.get_string() if value is not None else ""
        if key not in self._HANDS_KEYS:
            logger.warning("unknown hands-free choice %r", key)
            return
        wake = key in ("wake", "wake-over")
        over = key in ("over", "wake-over")
        if bool(self.config.wake_word_enabled) != wake:
            self._toggle_wake_word(None, None)
        if bool(self.config.barge_in_vad_enabled) != over:
            self._toggle_barge_in_vad(None, None)
        action.set_state(GLib.Variant("s", key))
        self._refresh_strip()

    def _on_answer_model(self, action, value) -> None:
        """Switch which model answers, then restart the local server.

        **The only control on the mode strip with a real effect.** Every other
        chip writes a setting the next turn reads. This one points `current.gguf`
        at another tier, and the running `llama-server` keeps the weights it
        already loaded - so a handler that only renamed the symlink would leave
        the chip reading "8B" while the 1.7B was still answering. That is the
        two-widgets-that-disagree failure this repo records repeatedly, and it is
        why the restart is part of the action rather than a follow-up somebody
        might forget.

        **The restart runs off the main loop.** A cold start takes seconds and the
        quality gate before it runs `llama-perplexity`, so doing either here would
        freeze the window - the same reason the Models panel and the rebuild
        button work on a worker. `gateway.off_the_main_loop` is the named helper,
        so a test can assert the dispatch rather than only its outcome.

        **Refusals are named, never swallowed.** An uninstalled tier is a download
        and not a mode, so it is refused by name; and `use()`'s quality gate can
        refuse a switch that would make replies measurable worse, which is the
        gate that made "installed" and "usable" different claims.

        The outcome is **logged, not surfaced**. A person watching the window sees
        the strip redraw and nothing else until the next reply comes back from
        the new model, and announcing it wants a surface that can say "loading"
        for as long as a cold start takes. Recorded as the next step rather than
        faked with a toast that claims a switch happened before it did.
        """
        from shani_chronoa import local_llm
        from shani_chronoa.gateway import off_the_main_loop

        key = value.get_string() if value is not None else ""
        installed = local_llm.installed()
        if key not in installed:
            logger.warning(
                "answer-model: %r is not installed on this machine (installed: "
                "%s), so it is a download and not a mode", key, installed)
            return

        def swap() -> None:
            try:
                verdict = local_llm.use(key)
                if not verdict.get("promote"):
                    logger.warning(
                        "answer-model: the quality gate refused %s - %s",
                        key, verdict.get("why"))
                    return
                # Order matters: the old server is stopped before the new one
                # starts, because two servers on one port is a silent failure
                # where the first keeps answering.
                local_llm.stop_service()
                local_llm.start_service()
                logger.info("answer-model: now answering with %s", key)
            except Exception:                           # noqa: BLE001
                # A failed switch leaves the previous model in place, which is
                # the safe direction - but it must say so rather than log a
                # success the machine never reached.
                logger.error("answer-model: switching to %s failed", key,
                             exc_info=True)

        off_the_main_loop(swap, "answer-model")
        action.set_state(GLib.Variant("s", key))
        self._refresh_strip()

    def _on_answer_cloud_model(self, action, value) -> None:
        """Choose which model a **cloud** provider answers with.

        **The twin of `answer-model`, and it is a separate action because the
        two switches do different things.** `answer-model` points `current.gguf`
        at another tier on this machine and restarts llama-server. A cloud choice
        has no symlink and no server: it is a setting the chain reads when it is
        built. One action covering both would have to know which kind of thing
        it was handed, and every caller would have to be right about it.

        **Writing the setting is not enough, and that is the reason this
        exists.** `CloudLLMChain.__init__` reads the keys and the chosen models
        **once**, so a model picked while the app is running waits for the chain
        to be rebuilt - and the only rebuilds are startup, the privacy toggle,
        the fallback toggle and `select_llm("cloud")`. So this calls the last of
        those, on purpose.

        **Only when a provider is already answering.** `select_llm("cloud")` on a
        machine answering locally would move a person off this machine without
        being asked, which is the opposite of what picking a model asks for. The
        choice is still saved - so it is what answers next time - and that is
        said in the log rather than left to be discovered.

        **Two refusals, both by name**: a provider nobody has registered, and a
        registered provider with no model setting, where `set_cloud_model`
        returns early and the choice would be discarded without a word.
        """
        from shani_chronoa import cloud_llm
        from shani_chronoa.config import MODEL_SETTINGS

        try:
            provider_id = value.get_child_value(0).get_string()
            model = value.get_child_value(1).get_string()
        except Exception:                              # noqa: BLE001
            logger.error("answer-cloud-model: expected (provider, model)")
            return

        provider = cloud_llm.PROVIDERS.get(provider_id)
        if provider is None:
            logger.warning("answer-cloud-model: no provider is registered as "
                           "%r, so nothing was changed", provider_id)
            return
        model = model.strip()
        if not model:
            logger.warning("answer-cloud-model: %s was given no model",
                           provider_id)
            return
        if MODEL_SETTINGS.get(provider_id) is None:
            # **`set_cloud_model` returns early on an id it does not name**, so
            # this would look like it worked and be discarded. The picker offers
            # exactly the providers in `PROVIDERS`, so a gap here is a registry
            # mistake rather than a person's mistake - and it has to be visible
            # when it happens rather than reported as a successful choice.
            logger.warning("answer-cloud-model: %s has no entry in "
                           "MODEL_SETTINGS, so a choice could not be saved",
                           provider_id)
            return

        self.config.set_cloud_model(provider_id, model)

        if self.current_llm_kind() != "cloud":
            # Saved for next time, and said rather than assumed.
            logger.info("answer-cloud-model: this machine is answering locally, "
                        "so %s is saved as %s's choice for when the cloud "
                        "answers", model, provider_id)
            self._refresh_strip()
            return

        problem = self.select_llm("cloud")
        if problem:
            logger.warning("answer-cloud-model: %s is saved for %s but the "
                           "chain did not rebuild - %s", model, provider_id,
                           problem)
        else:
            logger.info("answer-cloud-model: now answering with %s via %s",
                        model, provider_id)
        self._refresh_strip()

    def _refresh_strip(self) -> None:
        """Redraw the mode strip after an action wrote its setting.

        One line in three handlers was three chances to forget, and the reply
        style already had this inline - so it is named here and used everywhere.
        """
        window = getattr(self, "window", None)
        strip = getattr(window, "_mode_strip", None)
        if strip is not None:
            strip.refresh()
        # **And the model picker under the composer.** Same question as the
        # chip, asked at the moment of typing, so it has to redraw from the same
        # refresh or the row and the strip name different models.
        #
        # The two are separate lookups on purpose: `refresh()` is the strip's,
        # and the picker row is a child of the *window*, not of the strip. An
        # earlier version put `_refresh_model_word` on `_ModeStrip` - where
        # nothing called it - and this line looked for it here, so `getattr`
        # returned None and the redraw silently never happened. The `callable()`
        # guard is what made that invisible: a refresh that does nothing looks
        # exactly like a refresh with nothing to do.
        # The state line words the voice setting ("Ready - say ..."), so a
        # change to it is a change to that line too.
        sync = getattr(window, "_sync_from_state", None)
        if callable(sync):
            try:
                sync()
            except Exception:  # noqa: BLE001 - a label must not fail a mode
                logger.debug("state line not redrawn", exc_info=True)
        refresh = getattr(window, "_refresh_model_word", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001 - a label must not fail a mode
                logger.debug("model word would not redraw", exc_info=True)

    def _apply_answer_mode_if_present(self) -> None:
        """Apply `answer-mode`, if the code that does it exists.

        **Chronoa did not start at all without this.** `_init_components` called
        `self._apply_answer_mode()` directly, and that method is **defined
        nowhere in `app/`** - it lived only in an uncommitted `brain.py`, while
        the caller in this file was committed. `do_startup` raised
        `AttributeError`, `do_activate` never ran, `sense_scheduler` was None,
        and the window never appeared.

        A `hasattr` guard rather than deleting the two call sites, because:

        - **deleting them would be a silent lie** - `answer-mode` would be
          written by `_on_answer_egress` and never honoured, which is exactly
          the "a setting that does nothing" failure this file's own tests are
          written against.
        - **this lights up by itself.** If the implementation lands later, the
          mode starts applying with no edit here.

        **It lit up.** `_apply_answer_mode` now exists, in this file, calling
        `select_llm` - so the branch below is unreachable in this build and the
        guard is now only insurance against the method being removed again.
        The guard is kept rather than simplified away: it is the thing that
        stopped the application from starting at all, and a `hasattr` check
        costs nothing next to that. `tests/test_answer_mode.py` asserts the
        warning does **not** fire while the method exists, so "the guard is
        vestigial" is checked rather than assumed.

        **It warns, once, and says which half is missing** - because a mode strip
        showing "In the cloud" on a machine that answered locally is the
        confident-wrong-answer shape, and the person watching it cannot see a
        missing method. Written as a warning rather than an exception because the
        alternative is an application that will not open, which is worse than
        one that opens and says it is running the old behaviour.
        """
        applier = getattr(self, "_apply_answer_mode", None)
        if applier is None:
            if not getattr(self, "_answer_mode_warned", False):
                self._answer_mode_warned = True
                # **`config.get`, not `config.answer_mode`.** The property does
                # not exist either - `answer-mode` is in neither the gschema nor
                # `config.py` - so the first version of this warning printed
                # "answer-mode is set to '?'", which tells the person reading the
                # log nothing about the setting it is complaining about.
                logger.warning(
                    "answer-mode is %r but _apply_answer_mode is not "
                    "implemented in this build, so the choice is not being "
                    "applied - the model will answer as it did before that "
                    "setting existed",
                    self.config.get("answer-mode", "(unset)"),
                )
            return
        applier()

    def _apply_answer_mode(self) -> None:
        """Point the answering engine at what `answer-mode` says, right now.

        **This was missing while two callers and a warning existed for it**, and
        the warning was *false*: it told the reader "the choice is not being
        applied - the model will answer as it did before that setting existed"
        while `brain.py` was reading `privacy-mode` and `cloud-fallback-enabled`
        on every turn. The setting did govern what left the machine; what it
        did not do is move the engine off the one already loaded.

        That is `BrainMixin.select_llm`, which was written for the Models panel,
        documented as "switch the answering engine **now**", and had **no product
        caller at all** - one more of this repository's built-but-unwired
        methods. So this is wiring, not a decision: the semantics were already
        written down and tested, and this is the line that finally calls them.

        **The mapping is the strip's own labels, not a new vocabulary.**
        `_ModeStrip._EGRESS` offers four rows that reduce to three stored modes:
        "Nothing leaves" and "This machine answers" both mean the engine is
        local, and "This machine, then cloud" means *start* local - falling back
        is `_maybe_enable_cloud_fallback`'s job, which is why it maps to local
        rather than to cloud.

        **Switching only when it would change something.** Rebuilding a cloud
        chain on every startup to arrive at the engine already running would
        spend a provider's rate limit to learn nothing.

        **A refusal is logged and left in place.** `select_llm` returns a
        sentence naming the gate that shut; the previous engine keeps answering,
        which is the safe direction, but it must not be reported as a success.
        """
        mode = (self.config.get("answer-mode", "") or "").strip().lower()
        if not mode:
            # **Only reachable when the key has no value at all** - and since
            # the gschema was given `answer-mode` a default of 'local', that is
            # a config without the schema (tests, a build whose compiled
            # schemas predate it), not the ordinary startup. Not deleting it:
            # with a real schema the empty case never arrives, and the first
            # draft of this comment claimed it was the everyday path and that
            # guessing 'local' would override a legitimate cloud chain. It
            # would not - 'local' is the schema's own default - but a comment
            # that describes a path the code cannot take is a comment that
            # misleads whoever reads it next.
            return
        want = "cloud" if mode == "cloud" else "local"
        try:
            if self.current_llm_kind() == want:
                self._answer_mode_problem = ""
                return
        except Exception:                           # noqa: BLE001
            logger.debug("answer-mode: the current engine is unreadable",
                         exc_info=True)
        problem = self.select_llm(want)
        # **Kept on the application, not only logged.** The mode strip reads it
        # to say *why* the engine did not move, rather than guessing "no
        # provider was reachable" - the reason it gives is one sentence the
        # engine already produced, and a second guess beside it is a second
        # answer to "what is wrong".
        self._answer_mode_problem = problem
        if problem:
            logger.warning(
                "answer-mode is %r but the engine is still %r: %s", mode,
                self.current_llm_kind(), problem)

    def _answer_mode(self) -> str:
        """The stored answer mode, for the action's initial state.

        **This method was missing while `_create_actions` called it**, so every
        startup raised `AttributeError: 'ChronoaApplication' object has no
        attribute '_answer_mode'` at the point the `answer-mode` action is
        registered - and GApplication swallows an exception from `do_startup`,
        so nothing said so. What it cost, measured by driving `_create_actions()`
        on a real application object and then asking which actions exist:

            quit                     OK          answer-mode             MISSING
            toggle-listening         OK          toggle-barge-in-vad     MISSING
            reply-style              OK          toggle-model-download   MISSING
                                              download-speech-model   MISSING
                                              toggle-debug            MISSING
                                              stop-speaking           MISSING
                                              open-settings           MISSING
                                              setup                   MISSING

        Eight actions, registered after the failure point, never registered at
        all - so the gear button and `Ctrl+,` opened nothing, the setup wizard
        action was dead, and stop-speaking did nothing. The suite stayed green
        because `test_the_application_starts_and_builds_its_window` asserts the
        *window*, which `do_activate` builds after the swallowed exception, and
        the two tests that call `_create_actions()` directly were failing in a
        file nobody had run.

        Read from config rather than defaulted here, so a value the schema
        refuses is corrected by `ChronoaConfig.answer_mode`'s own validation -
        one place decides what a legal mode is.
        """
        return self.config.answer_mode

    def _on_answer_mode(self, action, value) -> None:
        """Write the chosen mode, then set the action's state from config.

        Read back rather than echoed, for the same reason as the reply style: a
        value the schema refuses leaves the setting where it was and the menu
        must then show that, not the click.

        **The mode takes effect now, not at the next restart.** Privacy mode is
        the lever that already exists for "nothing leaves this machine", so
        `local` and `cloud` are written through it, and the cloud chain is built
        or dropped immediately rather than the next time the app starts. Before
        this, the wizard's "In the cloud" was a promise about the next launch -
        while the machine in front of you kept answering locally.
        """
        # **One mapping, not two.** This handler and `_on_answer_egress` used to
        # each write the same three settings (`answer-mode`, `privacy-mode`,
        # `cloud-fallback-enabled`) from their own key vocabulary - and could
        # therefore disagree. Measured, both were live: this is still a registered
        # action reachable over D-Bus, so `app.answer-mode = "local"` would put
        # the egress lock on behind the strip's back. A dependency that exists in
        # two copies is a dependency that will diverge, so this now translates its
        # key and defers to the one that owns the writing.
        #
        # The translation is the old behaviour exactly: `local` meant "nothing
        # leaves" here, which is the egress lock, and `local-answers` - a state the
        # old vocabulary could not express - did not exist.
        egress = {"local": "this-machine",
                  "local-cloud": "local-then-cloud",
                  "cloud": "cloud"}.get(
                      value.get_string() if value is not None else "")
        if egress is None:
            return
        self._on_answer_egress(action, GLib.Variant("s", egress))
        action.set_state(GLib.Variant("s", self.config.answer_mode))

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
            self.window.connect("rewind-history", self._rewind_history)
            # Start the desktop companion now that a display exists. Off by
            # default; `running` is the gate and each surface answers it from
            # its own state - the window from `_win`, the notch from its bus
            # proxy - so this line no longer names a private attribute of one
            # of them.
            if not getattr(self, "mascot", None) is None and not self.mascot.running:
                try:
                    if self.mascot.start():
                        def _landline_to_mascot(kind, **info):
                            if kind == "incoming":
                                who = info.get("name") or info.get("number") or "someone"
                                GLib.idle_add(lambda: (self.mascot.notice("call", f"Call from {who}"), False)[1])
                        try:
                            landline.listen(_landline_to_mascot)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    logger.exception("mascot: could not start the companion")
            self.window.set_browser_available(browser_available())
            # A window raised by a global shortcut arrives with the caret
            # somewhere else, so the first thing a person types is lost.
            self.window.focus_input()
            if self.assistant:  # reopening the app shows the conversation it was in
                self.window.show_conversation(self.assistant.visible_turns())
                # **Before the first keystroke, and before `restore_draft`**
                # below reads it, or every draft would be restored as the
                # global one and the newest conversation's would be missed.
                if hasattr(self.window, "set_conversation"):
                    self.window.set_conversation(
                        Path(self.assistant.session_path).stem
                        if self.assistant.session_path else "")
            self.attach_regenerate()
            # **The rail now reads fired rules.** `event_results()` had no
            # caller anywhere in `usr/`, so an event rule could run a skill or
            # change a setting with nothing on screen to say so.
            self.window.set_event_source(self.sense_scheduler)
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
                self._queue_idle(lambda: (self._open_setup() if self._setup_needed() else None, False)[1])
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

    #: The one notification id a question uses, so a newer one replaces it.
    QUESTION_NOTIFICATION = "chronoa-question"

    def _notifying_presenter(self, present):
        """`present`, plus a notification with the answers when nobody is looking.

        **An approval waited where nobody could see it.** "May I run pytest in
        ~/shop?" appeared only in the window, so with the window behind another
        one the turn sat blocked until someone happened to look - up to the
        decision timeout. Now, when the window is not the active one, the same
        question is a desktop notification whose buttons answer it (through
        `app.answer-question`, the same path a spoken answer takes), and it is
        withdrawn once the question is answered from either place.
        """
        def wrapped(question: str, options: list):
            done = present(question, options)
            window = self.window
            try:
                looking = bool(window is not None and window.is_active())
            except Exception:  # noqa: BLE001
                looking = False
            if not looking and options:
                GLib.idle_add(self._notify_question, question, list(options), done)
            return done
        return wrapped

    def _notify_question(self, question: str, options: list, done) -> bool:
        """Post the question through org.freedesktop.Notifications, with its answers.

        **Not `Gio.Notification`.** GNOME shows a GApplication's notifications
        only when a desktop file is named after the app id, and Chronoa's is
        `shani-chronoa.desktop` - measured 2026-10-15 in the demo recorder: the
        question was posted and nothing appeared on screen. The freedesktop API
        is the one `incoming_call.py` already uses, and both GNOME and Plasma
        show it. A button press arrives as `ActionInvoked` and is answered
        through `app.answer-question`, the same path as a spoken answer.
        """
        bus = self.get_dbus_connection() or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        first = (question or "").strip().split("\n\n", 1)[0]
        actions = []
        for i, option in enumerate(options[:3]):
            actions += [str(i), str(option)]
        hints = {"urgency": GLib.Variant("y", 2), "resident": GLib.Variant("b", True),
                 "desktop-entry": GLib.Variant("s", "shani-chronoa")}
        try:
            reply = bus.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                GLib.Variant("(susssasa{sv}i)", ("Shani Chronoa", 0, "shani-chronoa",
                                                  "Chronoa is asking", first[:300],
                                                  actions, hints, 0)),
                GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
            nid = int(reply.unpack()[0])
        except Exception:  # noqa: BLE001 - no notification server: the window still asks
            logger.warning("could not post the question as a notification", exc_info=True)
            return GLib.SOURCE_REMOVE
        self._question_note = nid

        def on_action(_conn, _sender, _path, _iface, _signal, params):
            got, key = params.unpack()
            if got == nid and key.isdigit() and int(key) < len(options):
                self.activate_action("answer-question", GLib.Variant("s", str(options[int(key)])))
        watch = bus.signal_subscribe("org.freedesktop.Notifications",
                                     "org.freedesktop.Notifications", "ActionInvoked",
                                     "/org/freedesktop/Notifications", None,
                                     Gio.DBusSignalFlags.NONE, on_action)

        def close():
            bus.signal_unsubscribe(watch)
            self._close_question_note()
            return False

        def withdraw_when_answered():
            if hasattr(done, "wait"):
                done.wait()
            GLib.idle_add(close)
        threading.Thread(target=withdraw_when_answered, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _close_question_note(self) -> None:
        nid = getattr(self, "_question_note", 0)
        if not nid:
            return
        self._question_note = 0
        bus = self.get_dbus_connection() or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        try:
            bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                          "org.freedesktop.Notifications", "CloseNotification",
                          GLib.Variant("(u)", (nid,)), None, Gio.DBusCallFlags.NONE, 2000, None)
        except Exception:  # noqa: BLE001 - already gone
            pass

    def _on_answer_question(self, _action, value) -> None:
        """An answer from the notification: the same as one given in the window."""
        answer = value.get_string() if value is not None else ""
        window = self.window
        if window is None or not window.answer_pending_question(answer):
            logger.info("answer-question %r: no question is open any more", answer)
        self._close_question_note()

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
        # `--rules=build`: the answer rules to ask under (Circle to Search's
        # "Fix this error" asks in Build mode). Set before the question goes.
        rules = next((a.split("=", 1)[1] for a in args[1:] if a.startswith("--rules=")), "")
        if rules and self.lookup_action("answer-rules") is not None:
            self.lookup_action("answer-rules").change_state(GLib.Variant("s", rules))
        if self.window is not None and ask.strip():
            # Through the window's own send path, so the question shows in the
            # transcript with its attachments, as if it had been typed.
            GLib.idle_add(lambda: (self.window.submit_text(ask.strip()), False)[1])
        # Verbs for a desktop's own custom-shortcut settings and the dock menu
        # (sayri's `sayri toggle`): work on any session, portal or not.
        if "--listen" in args[1:] or "--toggle-listening" in args[1:]:
            GLib.idle_add(lambda: (self.activate_action("toggle-listening", None), False)[1])
        # Dictate into the focused app, and stop whatever Chronoa is doing -
        # the two verbs a dictation shortcut needs (Handy's --toggle-
        # transcription and --cancel, for the same reason).
        if "--dictate" in args[1:]:
            GLib.idle_add(lambda: (self.activate_action("dictate", None), False)[1])
        if "--stop" in args[1:]:
            GLib.idle_add(lambda: (self.activate_action("stop-speaking", None), False)[1])
        if "--new-conversation" in args[1:]:
            GLib.idle_add(lambda: (self.activate_action("reset-conversation", None), False)[1])
        if "--setup" in args[1:]:
            # `shani-chronoa --setup`: open first-run setup now, done or not
            self._queue_idle(lambda: (self._open_setup(), False)[1])
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
        # **And the stored mode, which until 2026-10-12 was asked once by the
        # wizard and then ignored.** `_init_components` built local first
        # unconditionally - "local-first by design" - so a person who chose
        # "In the cloud" at setup was answered locally whenever a local model
        # happened to be installed. The mode is a setting now and this is where
        # it is honoured; privacy mode still wins over it above.
        self._apply_answer_mode_if_present()
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
        # A server started at login from an llm.env an older build wrote keeps
        # that build's choices (its context size, above all) until something
        # rewrites the file. Checked once, off the main loop: a restart reloads
        # the model, which is seconds, and is done before the first question.
        from shani_chronoa.gateway import off_the_main_loop

        def refresh() -> None:
            try:
                if local_llm.refresh_env() and local_llm.is_up():
                    logger.info("restarting the local model with its new settings")
                    local_llm.restart_service()
            except Exception:  # noqa: BLE001 - a stale setting is not a broken start
                logger.debug("could not refresh llm.env", exc_info=True)
        off_the_main_loop(refresh, "refresh-llm-env")
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
        # The same switch as a choice of two, for menus: a radio pair needs a
        # stateful action, and two rows that both fire a toggle would turn plan
        # mode *off* when "Plan first" was pressed while it was already on.
        plan_choice = Gio.SimpleAction.new_stateful(
            "answer-plan", GLib.VariantType.new("s"),
            GLib.Variant("s", "plan" if planmode.is_enabled() else "working"))
        plan_choice.connect("change-state", self._on_answer_plan)
        self.add_action(plan_choice)

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

        # How replies are written: ordinary, brief or explanatory. Stateful, so
        # a menu bound to it draws radio items and reads the current style
        # back from the action rather than from a copy of its own. The value is
        # read from config on every turn (`assistant.reply_style_clause`), so a
        # change applies to the next reply - which is what makes it a mode for
        # the window and not only a setting.
        reply_style_action = Gio.SimpleAction.new_stateful(
            "reply-style", GLib.VariantType.new("s"),
            GLib.Variant("s", self.config.reply_style))
        reply_style_action.connect("change-state", self._on_reply_style)
        self.add_action(reply_style_action)

        # How hard the model thinks: the same stateful shape, read on every
        # turn by `Assistant._ask`, so a change applies to the next reply.
        thinking_action = Gio.SimpleAction.new_stateful(
            "thinking", GLib.VariantType.new("s"),
            GLib.Variant("s", self.config.thinking))
        thinking_action.connect("change-state", self._on_thinking)
        self.add_action(thinking_action)

        # The project Build mode works in: chosen with a folder picker, kept in
        # `project-dir`, handed to `buildmode` so skills and the prompt see it.
        from shani_chronoa import buildmode
        buildmode.set_project(self.config.project_dir)
        choose_project = Gio.SimpleAction.new("choose-project", None)
        choose_project.connect("activate", self._on_choose_project)
        self.add_action(choose_project)
        clear_project = Gio.SimpleAction.new("clear-project", None)
        clear_project.connect("activate", lambda *_a: self._set_project(""))
        self.add_action(clear_project)

        # Tools from the MCP servers in `mcp-servers`: listed off the main
        # loop (each server is a process to start), now and on demand.
        reload_mcp = Gio.SimpleAction.new("reload-mcp", None)
        reload_mcp.connect("activate", lambda *_a: self._discover_mcp())
        self.add_action(reload_mcp)
        self._discover_mcp()

        # Where a reply comes from. Three values, so it is the same stateful
        # action shape as the reply style, and it is in the mode strip for the
        # same reason a person flips it mid-conversation: "answer here" and
        # "answer from anywhere" are different questions and the Setup wizard
        # asked the second once and then forgot the answer.
        answer_mode_action = Gio.SimpleAction.new_stateful(
            "answer-mode", GLib.VariantType.new("s"),
            GLib.Variant("s", self._answer_mode()))
        answer_mode_action.connect("change-state", self._on_answer_mode)
        self.add_action(answer_mode_action)

        # What it may do, and how it listens: both were four chips' worth of
        # toggles and are now one choice each, carried as stateful string
        # actions so the menu and the chip cannot disagree.
        for name, handler, initial in (
                ("answer-rules", self._on_answer_rules, "anything"),
                ("answer-egress", self._on_answer_egress,
                 "local-then-cloud"),
                ("answer-ambient", self._on_answer_ambient, "on-ask"),
                ("answer-presence", self._on_answer_presence, ""),
                ("hands-free", self._on_hands_free, "off"),
                ("answer-model", self._on_answer_model, ""),
                # Settings' "How Chronoa treats a risky call" combo writes
                # `permissions` directly, so this action is for anywhere else
                # that wants the posture as a first-class, stateful control.
                ("answer-permission", self._on_answer_permission, "default")):
            act = Gio.SimpleAction.new_stateful(
                name, GLib.VariantType.new("s"),
                GLib.Variant("s", initial))
            act.connect("change-state", handler)
            self.add_action(act)

        barge_in_vad_action = Gio.SimpleAction.new("toggle-barge-in-vad", None)
        barge_in_vad_action.connect("activate", self._toggle_barge_in_vad)
        self.add_action(barge_in_vad_action)

        # **Which cloud model answers**, as `(provider, model)` - two values, so
        # this is a plain parameterised action rather than one of the stateful
        # `s` chips above. A stateful action would have to carry the pair in a
        # single string, and every reader would have to split it correctly to
        # learn which provider a model belongs to.
        cloud_model_action = Gio.SimpleAction.new(
            "answer-cloud-model", GLib.VariantType.new("(ss)"))
        cloud_model_action.connect("activate", self._on_answer_cloud_model)
        self.add_action(cloud_model_action)

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
        # The speaker toggle beside the microphone. Stateful, so the button
        # draws the setting rather than its own idea of it; turning it off
        # also stops a reply that is already being said.
        # A question answered from its notification (`_notifying_presenter`).
        answer_action = Gio.SimpleAction.new("answer-question", GLib.VariantType.new("s"))
        answer_action.connect("activate", self._on_answer_question)
        self.add_action(answer_action)

        # Text in any script, committed through IBus (ibus_input.py); a no-op
        # where IBus is not running.
        from shani_chronoa import ibus_input
        ibus_input.start()
        # One summary on unlock of what Chronoa did while you were away (away.py).
        from shani_chronoa import away
        away.start()
        # A meeting app opening the microphone: offer notes (mic_watch.py).
        from shani_chronoa import mic_watch
        mic_watch.start(self)
        # The JioFiber landline, when set up and allowed: register with the
        # router so the line can ring in and be dialled (landline/__init__.py).
        from shani_chronoa import landline
        landline.start(self)
        # The desktop companion (mascot.py): an always-on overlay, off by
        # default, that mirrors the assistant state and reacts to events.
        # `mascot.create()` picks the surface this machine can show - the
        # draggable window where a layer shell exists, the GNOME Shell notch
        # where it does not - and both answer the same surface, so everything
        # below holds one object and never asks which it got.
        #
        # **Constructed here, started in do_activate().** Both gates are honest
        # and neither can be answered yet: `mascot.available()` asks the
        # compositor and `mascot_notch.available()` asks the session bus, and
        # both depend on a main loop that does not exist at `do_startup`. So the
        # *choice* of surface can be made now (it is a property of the machine)
        # and the *running* of it cannot.
        #
        # **There is deliberately no second build site in `do_activate`**, which
        # looked like the obvious safety net for a partial startup. Measured
        # rather than assumed, because the assumption was wrong in an
        # interesting way: when `do_startup` raises, GLib logs
        # `failed to chain up on ::startup` and **carries on into
        # `do_activate` anyway** - so a fallback there *is* reachable. It would
        # still change nothing for the better, and the reason is the one that
        # matters: every reader of `self.mascot` (`do_shutdown`,
        # `do_activate`, `voice._on_level`) already treats absence as "no
        # companion" rather than as a fault, so the fallback's only observable
        # effect would be to **show a mascot on an application that failed to
        # start**.
        from shani_chronoa import mascot
        self.mascot = mascot.create(self)

        # What the assistant is doing, for the top-bar indicator: idle,
        # listening, thinking, speaking... Read-only - disabled, so a D-Bus
        # caller can watch it but not set it; `ChronoaWindow.set_state` is the
        # one writer.
        status_action = Gio.SimpleAction.new_stateful(
            "status", None, GLib.Variant("s", "idle"))
        status_action.set_enabled(False)
        self.add_action(status_action)

        speak_action = Gio.SimpleAction.new_stateful(
            "speak-replies", None, GLib.Variant("b", self.config.speak_replies))
        speak_action.connect("change-state", self._on_speak_replies)
        self.add_action(speak_action)

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
        # The same wizard on one page ("ears", "eyes", "memory", ...): every
        # panel's "Download" for a missing model lands on that model's page.
        setup_page_action = Gio.SimpleAction.new("setup-page", GLib.VariantType.new("s"))
        setup_page_action.connect("activate", lambda _a, p: self._open_setup(
            p.get_string() if p is not None else ""))
        self.add_action(setup_page_action)

        # "Connect a device" (gui/connect_device.py): Bluetooth, an Android
        # phone over adb, a KDE Connect phone. The parameter opens one route
        # directly ("bluetooth", "android", "kdeconnect"); "" is the chooser.
        transcribe_action = Gio.SimpleAction.new("transcribe-file", None)
        transcribe_action.connect("activate", lambda *_: self.window is not None
                                  and self.window.pick_recording_to_transcribe())
        self.add_action(transcribe_action)

        connect_action = Gio.SimpleAction.new("connect-device", GLib.VariantType.new("s"))
        connect_action.connect("activate", lambda _a, p: self._open_connect_device(
            p.get_string() if p is not None else ""))
        self.add_action(connect_action)

    def _open_connect_device(self, route: str = "") -> None:
        from shani_chronoa.gui.connect_device import ConnectDeviceWindow
        window = ConnectDeviceWindow(self, route, transient_for=self.window)
        window.present()

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
