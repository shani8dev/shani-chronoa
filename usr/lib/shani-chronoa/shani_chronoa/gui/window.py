"""The main window: layout, state, the transcript area and text input; styling, questions, attachments and the conversations menu are mixed in."""

import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Gdk, GObject, Adw

# libadwaita has to be initialised before a single Adw widget exists, and the
# failure mode is silent: the widgets render nothing, no exception, no warning.
# That is exactly how this repo's own settings window once fell back to unstyled
# GTK in a way nobody could see. It is idempotent, and `do_startup` calls it
# again, so doing it here covers a surface or window built before the
# application is started - which is what a test does.
Adw.init()

from shani_chronoa import capabilities

from .widgets import (  # noqa: F401
    AssistantState,
    ChronoaOrbWidget,
    HelpWindow,
    TranscriptView,
    _LEGACY_STATE_ALIASES,
)
from .style import StyleMixin
from .asking import AskingMixin
from .attaching import AttachingMixin
from .conversations_menu import ConversationsMenuMixin
from .sidebar import SidebarBreakpoint, SidebarPage
from .organs import OrganPanel, OrganStrip, ORGAN_IDLE_CSS

logger = logging.getLogger(__name__)


def _broken_page(name: str, exc: BaseException) -> "Adw.NavigationPage":
    """The page a panel shows when it could not be built.

    A panel that raises on construction must not take the window with it: the
    conversation is the product, and the panel is one of ten ways to look at it.
    So the failure is a page that says what failed, with the exception in the
    detail, and every other panel still works.
    """
    from shani_chronoa.gui.surfaces import common

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    body.set_margin_top(18)
    body.set_margin_start(18)
    body.set_margin_end(18)
    heading = Gtk.Label(label="This panel could not be opened")
    heading.add_css_class("title-4")
    body.append(heading)
    detail = Gtk.Label(label=f"{type(exc).__name__}: {exc}")
    detail.set_wrap(True)
    detail.set_selectable(True)
    detail.add_css_class("dim-label")
    body.append(detail)
    if common.adw_ready():
        return Adw.NavigationPage(child=body, title=name)
    return body




class ChronoaWindow(StyleMixin, AskingMixin, AttachingMixin, ConversationsMenuMixin,
                    Adw.ApplicationWindow):
    """The main window: the conversation, and every panel beside it.

    A `Adw.ApplicationWindow` inside an `Adw.NavigationSplitView`: the sidebar
    lists the panels (`gui/sidebar.py`, built from the same registry the panels
    register in) and the content is a navigation view the conversation lives on,
    with each panel pushed onto it.

    The chat's own widgets are unchanged by any of that - the orb, the transcript,
    the composer, the question row all keep their names and their place - because
    the split view wraps a column that was already there rather than rebuilding
    it. That is deliberate: this window has tests that walk its tree, and a
    redesign that also moved everything would make them all fail at once, which
    is how a real regression hides in a large diff.
    """

    def __init__(self, application: Gtk.Application, config=None) -> None:
        super().__init__(application=application)
        # Panels are handed the application, not the window: they read config and
        # the assistant from it, and a panel that only knew the window would have
        # to guess where the model is.
        self._app = application
        self._state = AssistantState.IDLE
        self._config = config if config is not None else self._default_config()
        self._caps = self._load_capabilities()
        self._help_window: HelpWindow | None = None
        #: Set by the application through `set_regenerate_handler`; None means
        #: the transcript shows no "Ask that again" control at all.
        self._regenerate = None
        #: Built panels, kept so opening one twice does not re-run everything it
        #: measures. Empty until a panel is opened.
        self._surface_pages: dict = {}
        self._toasts = None
        self._setup_window()
        self._setup_ui()
        try:
            self._apply_organ_css()
        except Exception:                               # noqa: BLE001
            # No display is the only way this fails, and a window with no strip
            # colour is a window that still shows what Chronoa is doing.
            logger.debug("the organ strip stylesheet could not be applied",
                         exc_info=True)
        self._sync_from_state()

    @staticmethod
    def _default_config():
        """Config for the window, degrading rather than refusing.

        The window has to be constructible without one - a test, or a caller
        that only wants the visual surface - so a missing config is replaced by
        one that reports every gate as closed. Closed is the honest answer when
        consent cannot be read, and it means the help window explains the
        switches instead of quietly claiming everything is available.
        """
        try:
            from shani_chronoa.config import ChronoaConfig
            return ChronoaConfig()
        except Exception:
            logger.debug("no ChronoaConfig available; gates read as closed",
                         exc_info=True)

            class _Closed:
                def sense_allowed(self, key):
                    return False
            return _Closed()

    @staticmethod
    def _load_capabilities() -> list:
        """Read the live skill registry, tolerating its absence entirely.

        A broken or missing registry must cost the suggestions and the help
        list, not the window. Both surfaces are additive, so the failure mode
        is a plainer window rather than a window that will not open.
        """
        try:
            from shani_chronoa.skills import discover_skills
            tools, _handlers = discover_skills()
        except Exception:
            logger.warning("skill registry unavailable; no suggestions or help",
                           exc_info=True)
            return []
        return capabilities.find_capabilities(tools)

    def _apply_organ_css(self) -> None:
        """The strip's colours, which are the one thing on it that must not
        follow the theme.

        An indicator whose colour changes with the desktop theme is an indicator
        whose *meaning* changes with the desktop theme: on a light theme a
        "listening" light that has gone pale is not a smaller claim, it is a
        different one. The idle/active rules are fixed for that reason and live
        in `organs.ORGAN_IDLE_CSS` rather than in this window's stylesheet.
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(ORGAN_IDLE_CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _setup_window(self) -> None:
        self.set_title("Shani Chronoa")
        self.set_default_size(460, 640)
        self.set_size_request(380, 480)
        self.set_decorated(True)
        self._apply_css()
        self._apply_motion_preference()

    def _on_suggestion_clicked(self, _button: Gtk.Button, suggestion: str) -> None:
        """Put a suggestion in the input, ready to send or edit."""
        self._input_entry.set_text(suggestion)
        self._input_entry.grab_focus()
        self._input_entry.set_position(-1)

    def open_help(self) -> HelpWindow | None:
        """Open the capability list, or raise the one already open.

        Returns the window so a caller can wait on it; `None` when there is
        nothing to show, which is a real state - a build whose skill registry
        failed to load has no capabilities, and an empty help window would be
        worse than no button.
        """
        if not self._caps:
            logger.info("no capabilities to show; skill registry was empty")
            return None
        if self._help_window is not None:
            self._help_window.present()
            return self._help_window
        self._help_window = HelpWindow(
            self._caps, self._config, self._on_help_try, parent=self
        )
        self._help_window.connect("close-request", self._on_help_close)
        self._help_window.present()
        return self._help_window

    def _on_help_try(self, _button: Gtk.Button, prompt: str) -> None:
        self._on_suggestion_clicked(_button, prompt)
        if self._help_window is not None:
            self._help_window.close()

    def _on_help_close(self, _window) -> bool:
        self._help_window = None
        return False

    def _setup_ui(self) -> None:
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=14,
            margin_bottom=14,
            margin_start=14,
            margin_end=14,
        )
        self._chat_column = main_box
        # Forward declaration: the header bar is built further down in the same
        # method, and the page that holds it has to be assembled before either.
        header_bar_holder = Adw.Bin()
        self._split = Adw.NavigationSplitView()
        self._sidebar_page = SidebarPage(
            self._app, self._show_chat, self._show_surface)
        self._split.set_sidebar(self._sidebar_page)
        self._content_view = Adw.NavigationView()
        self._chat_page = Adw.NavigationPage(
            child=self._content_view, title="Conversation")
        self._split.set_content(self._chat_page)
        SidebarBreakpoint.apply(self._split)
        # A toolbar view with the header bar on top of the column, all of it
        # under a toast overlay - so a notice is a `Adw.Toast` that floats and
        # disappears rather than a line of text that permanently steals the room
        # where the answer is being written.
        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header_bar_holder)
        toolbar.set_content(self._toasts)
        self._content_view.add(Adw.NavigationPage(child=toolbar, title="Conversation"))
        self.set_content(self._split)

        # Header: an Adw.HeaderBar, not a row of widgets in a box.
        #
        # The buttons themselves are unchanged - same names, same actions, same
        # tooltips - but an `Adw.HeaderBar` is what gives them the title bar's
        # own background, the correct button sizing for the title's height, and a
        # working window-controls area, none of which a `Gtk.Box` inside a plain
        # window had. It also means the sidebar toggle below has somewhere real
        # to live.
        header_bar = Adw.HeaderBar()
        self._sidebar_toggle = Gtk.ToggleButton()
        self._sidebar_toggle.set_icon_name("open-menu-symbolic")
        self._sidebar_toggle.add_css_class("flat")
        self._sidebar_toggle.set_tooltip_text("Show or hide the panels (F9)")
        self._sidebar_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Show or hide the panels"])
        self._sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        header_bar.pack_start(self._sidebar_toggle)
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header = Gtk.Label(label="Shani Chronoa")
        header.add_css_class("cajita-header")
        header.set_hexpand(True)
        header.set_halign(Gtk.Align.START)

        self._mic_icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        self._mic_icon.set_pixel_size(16)
        self._mic_icon.set_tooltip_text("Microphone is in use")
        self._mic_icon.add_css_class("flat")
        #: Hidden until the microphone is actually in use; the tooltip says why.
        self._mic_icon.set_visible(False)

        # Quick Ask and the browser are actions with keyboard shortcuts, which is
        # the same as being invisible: a shortcut nobody knows about is not a
        # feature. Both are buttons for the same reason they are actions - the
        # actions are what a global shortcut and a second window reach.
        quick_button = Gtk.Button()
        quick_button.set_icon_name("dialog-question-symbolic")
        quick_button.add_css_class("flat")
        quick_button.set_valign(Gtk.Align.CENTER)
        quick_button.set_tooltip_text("Ask one quick question (Ctrl+Shift+A)")
        quick_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Ask one quick question"]
        )
        quick_button.set_action_name("app.quick-ask")

        self._browser_button = Gtk.Button()
        self._browser_button.set_icon_name("emblem-web-symbolic")
        self._browser_button.add_css_class("flat")
        self._browser_button.set_valign(Gtk.Align.CENTER)
        self._browser_button.set_tooltip_text("Browse the web (Ctrl+Shift+U)")
        self._browser_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Browse the web"]
        )
        self._browser_button.set_action_name("app.open-browser")

        help_button = Gtk.Button()
        help_button.set_icon_name("help-about-symbolic")
        help_button.add_css_class("flat")
        help_button.set_valign(Gtk.Align.CENTER)
        help_button.set_tooltip_text("What can Chronoa do?")
        help_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["What can Chronoa do?"]
        )
        help_button.connect("clicked", lambda _b: self.open_help())

        settings_button = Gtk.Button()
        settings_button.set_icon_name("emblem-system-symbolic")
        settings_button.add_css_class("flat")
        settings_button.set_valign(Gtk.Align.CENTER)
        settings_button.set_tooltip_text("Settings")
        settings_button.set_action_name("app.open-settings")

        # Conversations: every saved conversation, newest first, with New.
        self._conversations_button = Gtk.MenuButton()
        self._conversations_button.set_icon_name("view-list-symbolic")
        self._conversations_button.add_css_class("flat")
        self._conversations_button.set_valign(Gtk.Align.CENTER)
        self._conversations_button.set_tooltip_text("Conversations")
        self._conversations_button.update_property([Gtk.AccessibleProperty.LABEL], ["Conversations"])
        self._conversations_popover = Gtk.Popover()
        self._conversations_popover.connect("show", lambda _p: self._fill_conversations())
        self._conversations_button.set_popover(self._conversations_popover)

        # A widget goes into the header bar OR the row, never both: appending to
        # `header_row` first and then packing the same button tripped
        # `adw_header_bar_pack_end`'s "child already has a parent" assertion four
        # times over, and every one of those buttons silently ended up nowhere.
        header_bar.set_title_widget(header)
        header_bar.pack_start(self._conversations_button)
        header_bar.pack_start(self._mic_icon)
        for widget in (quick_button, self._browser_button, help_button, settings_button):
            header_bar.pack_end(widget)
        self._header_row = header_row
        self._header_bar = header_bar
        # The page was assembled before the bar existed (the column is built
        # top-down), so the bar goes into the holder now. An `Adw.Bin` exists
        # exactly for this: one slot, fillable later, no re-parenting.
        header_bar_holder.set_child(header_bar)
        self._toasts.set_child(main_box)

        self._orb = ChronoaOrbWidget()
        self._orb.set_halign(Gtk.Align.CENTER)
        self._orb.set_action_name("app.toggle-listening")
        self._orb.set_tooltip_text("Start listening (Enter)")
        main_box.append(self._orb)

        # Two separate lines: the state (what the assistant is doing) and a
        # transient detail (the last thing that changed). They used to share
        # one label, so any toggle notice overwrote the state.
        self._state_label = Gtk.Label(label="Ready")
        self._state_label.add_css_class("cajita-state")
        self._state_label.set_halign(Gtk.Align.CENTER)
        main_box.append(self._state_label)

        self._detail_label = Gtk.Label(label="")
        self._detail_label.add_css_class("cajita-detail")
        self._detail_label.set_halign(Gtk.Align.CENTER)
        self._detail_label.set_visible(False)
        main_box.append(self._detail_label)

        self._transcript = TranscriptView(
            config=self._config,
            caps=self._caps,
            on_suggestion=self._on_suggestion_clicked,
            reduce_motion=self._motion_is_reduced(),
            # No regenerate control until the application attaches one: a button
            # that cannot re-ask anything is a dead control in every transcript.
            on_regenerate=None,
        )
        # Search this conversation (Ctrl+F). A search *field* in the window, not
        # a skill and not the desktop search provider: the two things a person
        # searches for are "what did I ask before" (a conversation) and "what is
        # on my screen" (the search provider), and neither is the other.
        self._search_bar = Gtk.SearchBar()
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_hexpand(True)
        self._search_entry.set_placeholder_text("Find in this conversation")
        self._search_entry.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Find in this conversation"])
        self._search_entry.connect("search-changed", lambda e: self._run_search(e.get_text()))
        self._search_entry.connect("activate", lambda _e: self._search_next())
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        main_box.append(self._search_bar)

        main_box.append(self._transcript)

        # Where an `ask_user` question appears. Hidden until a question is
        # actually pending, so it costs nothing the rest of the time.
        self._question_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._question_row.add_css_class("cajita-question")
        self._question_row.set_visible(False)
        main_box.append(self._question_row)
        self._pending_question = None
        self._question_widgets: list = []

        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        # Still a `Gtk.Entry`, not `Adw.Entry`: measured, `Adw.Entry` does not
        # exist on libadwaita 1.5 (it arrived in 1.6), so the multi-line composer
        # Alpaca has is a `Gtk.TextView` and is a separate piece of work. Kept as
        # an entry so nothing that drives it - Enter, the Send button, `--ask=`,
        # a suggestion chip - changes behaviour while the panels are landing.
        self._input_entry = Gtk.Entry()
        self._input_entry.set_placeholder_text("Type a message…")
        # A placeholder is not an accessible name: a screen reader announced
        # this field as just "text" (shani-testbed a11y-lint, chronoa-voice)
        self._input_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Message to Chronoa"])
        self._input_entry.add_css_class("cajita-input")
        self._input_entry.set_hexpand(True)
        self._input_entry.connect("activate", self._on_input_activate)
        # Typing must not imply "Enter submits" to everyone: it rules out
        # on-screen keyboards whose return key inserts a newline, and for a
        # motor-impaired user it is a gesture to get wrong repeatedly. The
        # button is the primary action, so it is a real suggested-action and
        # stays present; Stop still appears only while something is speaking.
        self._send_button = Gtk.Button()
        self._send_button.set_icon_name("go-next-symbolic")
        self._send_button.add_css_class("suggested-action")
        self._send_button.set_valign(Gtk.Align.CENTER)
        self._send_button.set_tooltip_text("Send (Enter)")
        self._send_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Send message"]
        )
        self._send_button.connect("clicked", self._on_send_clicked)

        # Stop is only meaningful while something is being said, so it appears
        # then and disappears otherwise rather than sitting there disabled.
        self._stop_button = Gtk.Button()
        self._stop_button.set_icon_name("media-playback-stop-symbolic")
        self._stop_button.add_css_class("flat")
        self._stop_button.set_valign(Gtk.Align.CENTER)
        self._stop_button.set_tooltip_text("Stop speaking (Esc)")
        self._stop_button.set_action_name("app.stop-speaking")
        self._stop_button.set_visible(False)

        # Attachments: files dropped on the window or added with the paperclip
        # go with the next message as paths (see attachments.py), shown as
        # chips until it is sent; a chip's click removes it.
        self._attachments: list = []
        self._attach_bar = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                       max_children_per_line=4, column_spacing=6, row_spacing=6)
        self._attach_bar.set_visible(False)
        self._attach_button = Gtk.Button()
        self._attach_button.set_icon_name("mail-attachment-symbolic")
        self._attach_button.add_css_class("flat")
        self._attach_button.set_valign(Gtk.Align.CENTER)
        self._attach_button.set_tooltip_text("Attach files (or drop them on this window)")
        self._attach_button.update_property([Gtk.AccessibleProperty.LABEL], ["Attach files"])
        self._attach_button.connect("clicked", lambda _b: self._pick_files())
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_files_dropped)
        self.add_controller(drop)

        input_row.append(self._attach_button)
        input_row.append(self._input_entry)
        input_row.append(self._stop_button)
        input_row.append(self._send_button)
        main_box.append(self._attach_bar)
        # The organ strip sits directly above the composer, always present, so
        # "is it listening right now" is answerable without a menu. It is not
        # hidden when nothing is happening: an indicator that only exists while
        # the camera is on cannot answer "is it on now", and an empty strip looks
        # exactly like a broken one.
        self._organ_strip = OrganStrip(self._config)
        main_box.append(self._organ_strip)
        main_box.append(input_row)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def set_state(self, state: AssistantState) -> None:
        """Set the assistant state. The single entry point for it."""
        self._state = state
        self._sync_from_state()
        for observer in getattr(self, "state_observers", ()):
            try:
                observer(state)
            except Exception as e:  # an observer must never break the UI
                logger.error(f"State observer failed: {e}")

    def get_state(self) -> AssistantState:
        return self._state

    def _sync_from_state(self) -> None:
        """Derive every state-dependent widget from `self._state`.

        Called from exactly one place, which is what makes it impossible for
        the orb and the status line to show different things.
        """
        self._orb.set_state(self._state)
        self._state_label.set_label(self._state.label)
        # The mic is genuinely hot in exactly two states.
        hot = self._state in (AssistantState.LISTENING, AssistantState.INTERRUPTING)
        self._mic_icon.set_visible(True)
        self._mic_icon.set_from_icon_name(
            "audio-input-microphone-symbolic" if hot else "audio-input-microphone-muted-symbolic"
        )
        self._mic_icon.set_tooltip_text(
            "Microphone is in use" if hot else "Microphone idle"
        )
        self._mic_icon.remove_css_class("mic-off")
        if hot:
            self._mic_icon.add_css_class("mic-off")
        # Stop works while thinking too: it cancels the turn (app._stop_speaking)
        self._stop_button.set_visible(self._state in (AssistantState.SPEAKING, AssistantState.THINKING))
        self._stop_button.set_tooltip_text("Stop speaking (Esc)" if self._state is AssistantState.SPEAKING
                                           else "Stop thinking about this (Esc)")

    def set_input_level(self, level: float) -> None:
        """Feed the recorder's input level to the orb.

        Ignored unless listening, so a level that arrives after the turn ended
        cannot make the halo pulse at nothing.
        """
        if self._state is AssistantState.LISTENING:
            self._orb.set_level(level)

    def set_orb_state(self, state: str) -> None:
        """Compatibility shim for the existing `app.py` call sites.

        Kept because the string form is what `app.py` already speaks, but it
        now goes through the same single state rather than poking the orb and
        the label separately.
        """
        resolved = _LEGACY_STATE_ALIASES.get(state)
        if resolved is None:
            try:
                resolved = AssistantState(state)
            except ValueError:
                resolved = AssistantState.IDLE
        self.set_state(resolved)

    # ------------------------------------------------------------------
    # Detail line (never touches state)
    # ------------------------------------------------------------------

    def set_status(self, status: str) -> None:
        """Show a transient notice on the detail line.

        Intentionally cannot change the state line. A notice is an aside; the
        state is the answer to "what is Chronoa doing", and one must not
        overwrite the other.
        """
        text = (status or "").strip()
        self._detail_label.set_label(text)
        self._detail_label.set_visible(bool(text))
        # A notice that matters more than a line of dim text gets a toast as
        # well, because the detail line is easy to miss on a window that is
        # busy answering a question. The line stays: a toast disappears, and some
        # of these are things a person needs to still be able to read.
        if text and self._should_toast(text):
            self.toast(text)
        self._sync_sidebar_toggle()

    #: Notices worth interrupting for. A toast is for something a person would
    #: want to know about *now*; the rest are conversation-adjacent and stay in
    #: the detail line.
    _TOAST_PREFIXES = ("Asking again", "Attached ", "Kept in", "Saved to", "Copied")

    @staticmethod
    def _should_toast(text: str) -> bool:
        return text.startswith(ChronoaWindow._TOAST_PREFIXES)

    def toast(self, message: str, seconds: float = 2.5) -> None:
        """A real `Adw.Toast`, floating over the column and then leaving.

        `set_timeout` is a method, not a constructor argument - `Adw.Toast.new()`
        takes the message only, and passing `timeout=` raises `TypeError`. Two
        seconds is libadwaita's own default; a longer notice is set afterwards,
        because the constructor cannot be told.
        """
        if self._toasts is None:
            return
        notice = Adw.Toast.new(message)
        if seconds:
            notice.set_timeout(seconds)
        self._toasts.add_toast(notice)

    def clear_status(self) -> None:
        self.set_status("")

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------

    def set_response(self, text: str) -> None:
        """Record the assistant's reply.

        Empty text clears the transcript, which is what resetting the
        conversation needs; otherwise the current assistant turn is added or
        updated rather than the whole view being replaced.
        """
        if not (text or "").strip():
            self._transcript.show_placeholder()
            return
        self._transcript.add_assistant_turn(text)

    # -- the panels -------------------------------------------------------

    def toggle_sidebar(self) -> None:
        """F9, and anything else that wants to flip the panels."""
        if self._sidebar_toggle is not None:
            self._sidebar_toggle.set_active(not self._sidebar_toggle.get_active())

    def _on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        """Show or hide the panels.

        `set_collapsed` is what the split view actually understands; the toggle
        only mirrors it, and mirroring is done in one direction here - the
        breakpoint also collapses the sidebar on a narrow window, and that must
        not leave the button showing the wrong state.
        """
        self._split.set_collapsed(button.get_active())

    def _sync_sidebar_toggle(self) -> None:
        """Follow the split view, not the other way round.

        Without this, collapsing the sidebar by dragging its edge, or by the
        breakpoint on a narrow window, leaves the button pressed while the panels
        are off screen - a control whose state is a lie is worse than no control.
        """
        collapsed = self._split.get_collapsed()
        if self._sidebar_toggle.get_active() != collapsed:
            self._sidebar_toggle.set_active(collapsed)

    def open_body_page(self) -> None:
        """The full body register, for when the strip is not enough."""
        if "body" not in self._surface_pages:
            self._surface_pages["body"] = OrganPanel()
        page = self._surface_pages["body"]
        try:
            page._back = self._pop_panel
        except AttributeError:
            pass
        self._content_view.push(page)
        self._sidebar_page.select(None)

    def _show_chat(self) -> None:
        """Back to the conversation, from any panel."""
        self._content_view.set_visible_page(self._content_view.get_first_child())
        self._sidebar_page.select(None)

    def _pop_panel(self) -> None:
        """The back arrow inside a panel's own header bar.

        `pop_to_tag` needs a tag and these pages are built by ten different
        modules that none of them set; popping to the first page is the same
        thing here, because the first page *is* the conversation and the only
        other pages are the panels.
        """
        view = self._content_view
        if view.get_visible_page() is not view.get_first_child():
            view.pop_to_page(view.get_first_child())
        self._sidebar_page.select(None)

    def _show_surface(self, name: str) -> None:
        """Push a panel onto the content view.

        Built on first use and kept afterwards, so returning to a panel shows the
        readings it had rather than re-running every sense - a panel that
        re-measured the machine each time it was opened would be a panel nobody
        opened twice. One build failure becomes the page's own error state, not a
        window that will not open.
        """
        from shani_chronoa.gui import surfaces
        available = surfaces.available_surfaces()
        if name not in available:
            self.set_status(f"That panel is not available: {name}")
            return
        page = self._surface_pages.get(name)
        if page is None:
            try:
                page = available[name][2](getattr(self, "_app", None))
            except Exception as exc:
                logger.error("Panel %r could not be built", name, exc_info=True)
                page = _broken_page(name, exc)
            self._surface_pages[name] = page
        # Panels are pushed, so they need a way back that does not depend on the
        # sidebar being open - on a narrow window it is a collapsed drawer.
        try:
            page._back = self._pop_panel
        except AttributeError:
            pass
        self._content_view.push(page)
        self._sidebar_page.select(name)
        # With panels on the content stack, a narrow window has them covering the
        # conversation rather than sitting beside it; closing the sidebar is what
        # gets the chat back on a small screen.
        if self._split.get_collapsed():
            self._split.set_collapsed(False)
        self._sidebar_toggle.set_active(False)

    def set_browser_available(self, available: bool) -> None:
        """Hide the browser button when WebKitGTK is not installed.

        A control that opens nothing and explains why is worse than no control
        for someone who has not read the packaging notes.
        """
        self._browser_button.set_visible(available)

    def focus_input(self) -> None:
        """Put the caret in the message field.

        The window can be raised by a global shortcut while something else has
        focus, and a window you have to click before you can type is one extra
        gesture on every voice-adjacent turn.
        """
        self._input_entry.grab_focus()

    # -- finding something in this conversation --------------------------

    def toggle_search(self) -> None:
        """Ctrl+F, and the search entry's own escape."""
        self._search_bar.set_search_mode_enabled(not self._search_bar.get_search_mode())
        if not self._search_bar.get_search_mode():
            self._transcript.highlight("")
            self._search_entry.set_text("")

    def _run_search(self, text: str) -> None:
        found = self._transcript.highlight(text)
        self._search_entry.set_text(text)
        self._search_entry.set_placeholder_text(
            f"Find in this conversation - {found} match" + ("es" if found != 1 else "")
            if text else "Find in this conversation")

    def _search_next(self) -> None:
        """Walk the hits: the first, then the next one."""
        hits = self._transcript.find(self._search_entry.get_text())
        if not hits:
            return
        self._search_cursor = getattr(self, "_search_cursor", -1) + 1
        if self._search_cursor >= len(hits):
            self._search_cursor = 0
        self._transcript.scroll_to_turn(hits[self._search_cursor])

    def add_tool_call(self, name: str, arguments: dict, result: str = "",
                      ok: bool = True) -> None:
        """Show a skill's call, its arguments and what it returned."""
        self._transcript.add_tool_call(name, arguments, result, ok)

    def add_user_turn(self, text: str) -> None:
        """Show what the user said, so a spoken turn is visible too."""
        self._transcript.add_user_turn(text)

    def clear_transcript(self) -> None:
        self._transcript.show_placeholder()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def set_regenerate_handler(self, handler) -> None:
        """Attach (or detach) what the transcript's "Ask that again" does.

        The window is constructed before the application knows whether there is
        an assistant to re-ask, so the handler arrives after construction - the
        same pattern `set_suggestion_handler` uses. Passing None removes the
        control, which is what happens when there is no model to answer with;
        a button that cannot work is worse than no button.
        """
        self._regenerate = handler
        self._transcript.set_regenerate_handler(handler)

    def _on_regenerate_clicked(self) -> None:
        if self._regenerate is not None:
            self._regenerate()

    def _on_input_activate(self, entry: Gtk.Entry) -> None:
        self._submit_input()

    def _on_send_clicked(self, _button: Gtk.Button) -> None:
        self._submit_input()

    def _submit_input(self) -> None:
        """Send whatever is typed, from Enter or the button.

        Both call this rather than duplicating it, so the send button cannot
        drift from the key binding - the bug class where a control is wired to
        a slightly different action than the shortcut beside it.
        """
        raw = self._input_entry.get_text()
        text = raw.strip()
        if not text:
            return
        self._input_entry.set_text("")
        if self._pending_question is not None:
            # Answering out loud or typing both land here, and both resolve the
            # question rather than opening a new turn - the turn is still blocked
            # inside the tool waiting for exactly this.
            self._resolve_question(text)
            return
        names, files_block = self.take_attachments()
        self.add_user_turn(text + (("\n📎 " + ", ".join(names)) if names else ""))
        # The stripped text, not the raw entry contents: the turn the user can
        # see is the stripped one, and emitting the raw string meant the
        # assistant received something the transcript never showed. The one
        # addition is the attachment block, shown above as the files' names.
        self.emit("user-input", text + files_block)

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def submit_text(self, text: str) -> None:
        """Send `text` as if typed - for `--ask=`, through the same path as Enter."""
        self._input_entry.set_text(text)
        self._submit_input()

    def get_input_text(self) -> str:
        return self._input_entry.get_text()

    def clear_input(self) -> None:
        self._input_entry.set_text("")

    __gsignals__ = {
        "user-input": (GObject.SignalFlags.RUN_FIRST, str, (str,)),
    }
