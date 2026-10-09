"""The main window: layout, state, the transcript area and text input; styling, questions, attachments and the conversations menu are mixed in."""

import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Gdk, Gio, GLib, GObject, Adw, Pango

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
    reading_width,
)
from .style import StyleMixin
from .asking import AskingMixin
from .attaching import AttachingMixin
from .conversations_menu import ConversationsMenuMixin
from .sidebar import SidebarBreakpoint, SidebarPage
from . import rail as rail_module
from . import task_card as task_card
from .organs import OrganPanel, OrganStrip, ORGAN_IDLE_CSS

logger = logging.getLogger(__name__)

#: The tag on the conversation page inside the content `Adw.NavigationView`.
#:
#: It exists because the way back to the conversation is `pop_to_tag`, and
#: `pop_to_tag` needs a tag. `Adw.NavigationView` has no `set_visible_page` -
#: measured on the installed libadwaita 1.5, `hasattr` is False - and the code
#: that went back to the chat called it anyway, so every click on the
#: "Conversation" row raised `AttributeError` and did nothing at all. From the
#: outside that reads as a dead row: the sidebar's own chat entry, on the state
#: the window opens in, silently broken, and with it every panel's back arrow.
CHAT_TAG = "chronoa-chat"


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


def _panel_status(page: object) -> "str | None":
    """What a panel says about itself, or None if it has nothing to say.

    A panel may expose `status()` returning one of `common.STATUS_*`. This is
    deliberately duck-typed and deliberately optional: twenty panels build
    through several different shapes (`Adw.NavigationPage`, `Gtk.Box`, and the
    ones that forward attributes through a loop), and requiring every one of
    them to answer would be a large change for a cosmetic gain. A panel that
    answers gets a dot; a panel that does not simply keeps none.

    The one thing that *is* checked is the vocabulary. A panel returning a fourth
    word would leave the dot uncoloured - the exact failure the closed vocabulary
    exists to prevent - so it is treated as no answer at all rather than passed
    through to a grey square. A `status()` that raises is likewise no answer: a
    sidebar dot is not worth taking the window down for.
    """
    from shani_chronoa.gui.surfaces import common

    status = getattr(page, "status", None)
    if not callable(status):
        return None
    try:
        answer = status()
    except Exception:
        logger.warning("A panel's status() raised", exc_info=True)
        return None
    if answer not in common.STATUS_CLASSES:
        return None
    return answer


#: The mode strip's rules, kept out of `style.py` for the reason
#: `organs.ORGAN_IDLE_CSS` is: these chips are *indicators*, and an indicator
#: whose "on" colour follows the desktop theme changes what it means when the
#: theme changes. It inherits `currentColor` rather than naming a hue, so a chip
#: reads as "on" by weight and background rather than by a second colour
#: vocabulary nobody would remember by the time they needed it.
MODE_STRIP_CSS = """
.mode-strip { padding: 2px 4px; }
.mode-chip { padding: 1px 7px; border-radius: 9px; }
.mode-chip-label { font-size: 10px; opacity: 0.6; }
.mode-chip-on { background-color: alpha(currentColor, 0.14); }
.mode-chip-on .mode-chip-label { opacity: 1.0; font-weight: 600; }
"""


class _ModeStrip(Gtk.Box):
    """What Chronoa *is* right now: three switches and one button.

    The organ strip beside it answers "what is Chronoa doing"; this answers "what
    is Chronoa allowed to be". Those are different questions and they were both
    answered only in Settings, so a user who had turned plan mode on had no way
    to see it from the window they were talking to - and a mode that removes
    capabilities without saying so reads as a bug the first time the assistant
    declines something that was explicitly permitted.

    **Each chip is the control, not a label for one.** It is a
    `Gtk.ToggleButton` wired to the app's own action, which is the same path the
    keyboard accelerator and the Settings window's switch take. A label that
    reads "Plan mode: on" beside a switch in another window is two widgets that
    can disagree; this is one widget, and the only way for it to be wrong is for
    the action not to have run.

    **The state is read back, not tracked.** `_syncing` guards `set_active()`
    from re-entering the handler, and every read goes to the thing that owns the
    state - `planmode.is_enabled()` for plan mode, the persisted
    `wake-word-enabled` for the wake phrase, `privacy_mode` for local-only. A
    chip that remembered its own last click would be right until something else
    changed the setting, and then confidently wrong, which is the specific
    failure `set_status()` already documents for state lines.

    **Dictation is a button, not a fourth switch**, because it has no state to
    hold: it is a way of starting one long turn, and a chip that stayed pressed
    afterwards would claim a turn is still running when it ended on silence two
    hundred seconds later.
    """

    #: (attribute, label, icon, action, how to read the state). Kept as a table
    #: because the strip is rebuilt from it and a chip added by copy-paste is a
    #: chip whose tooltip and its read-back can drift apart.
    _CHIPS = (
        ("_privacy_chip", "Local only", "security-high-symbolic",
         "toggle-privacy", "_read_privacy"),
        ("_plan_chip", "Plan mode", "document-edit-symbolic",
         "toggle-plan-mode", "_read_plan_mode"),
        ("_wake_chip", "Wake word", "audio-input-microphone-symbolic",
         "toggle-wake-word", "_read_wake_word"),
        # Talking over a reply. It existed only as a switch deep in Settings,
        # yet it is the one mode a person flips mid-conversation: on with
        # headphones, off on speakers, where with no echo cancellation Chronoa
        # hears itself and stops.
        ("_barge_chip", "Talk over", "media-playback-pause-symbolic",
         "toggle-barge-in-vad", "_read_barge_in"),
    )

    def __init__(self, application, config=None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.add_css_class("mode-strip")
        self.set_halign(Gtk.Align.CENTER)
        #: The application is passed in rather than read from
        #: `Gtk.Application.get_default()`. That global is process-wide, so a
        #: strip built while *any* other application happens to be the default
        #: activates its actions on the wrong one - measured: `tests/
        #: test_mode_strip.py` passed alone and failed every activation when run
        #: beside `test_window_ux.py`, whose fixture is the default by then.
        #: `SidebarPage(self._app, ...)` a few lines up takes it the same way.
        self._app = application
        self._config = config
        #: True while `refresh()` is writing the chips. `set_active()` emits
        #: `toggled` even when nothing was clicked, so without this the strip
        #: would activate its own actions every time it redrew - the loop
        #: `set_can_answer()` and the organ strip both avoid by only ever
        #: being written from one direction.
        self._syncing = False
        for attribute, label, icon, action, reader in self._CHIPS:
            chip = self._chip(label, icon, action)
            chip.connect("toggled", self._on_toggled, action)
            setattr(self, attribute, chip)
            self._readers = getattr(self, "_readers", {})
            self._readers[attribute] = getattr(self, reader)
            self.append(chip)
        self._style_chip = self._reply_style_chip()
        self.append(self._style_chip)
        self.append(self._dictate_button())
        self.refresh()

    def _chip(self, label: str, icon: str, action: str) -> Gtk.ToggleButton:
        """One switch: an icon and its word, in a toggle that is the control.

        The word is in the chip rather than only in the tooltip because a chip
        showing only a glyph is a control whose meaning has to be remembered, and
        four of them in a row is four things to decode before reading any of
        them.
        """
        chip = Gtk.ToggleButton()
        chip.add_css_class("mode-chip")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        image = Gtk.Image.new_from_icon_name(icon)
        image.set_pixel_size(13)
        box.append(image)
        text = Gtk.Label(label=label)
        text.add_css_class("mode-chip-label")
        box.append(text)
        chip.set_child(box)
        chip.set_tooltip_text(
            f"{label}: off. Click to turn it on "
            f"(the {action} action, the same one the shortcut uses)"
        )
        chip.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{label} mode, currently off"])
        return chip

    #: style -> (chip word, menu line). Words a person picks by, not the keys.
    _STYLES = (
        # Under 30 characters each: the layout contract fails any label longer
        # than that which can neither wrap nor ellipsise, and menu items do neither.
        ("ordinary", "Ordinary", "Ordinary: the usual answer"),
        ("brief", "Brief", "Brief: just the answer"),
        ("explanatory", "Explanatory", "Explanatory: adds the why"),
    )

    def _reply_style_chip(self) -> Gtk.MenuButton:
        """How replies are written, as a chip showing the current style.

        Three values, so a menu rather than a toggle, bound to the stateful
        `app.reply-style` action - GTK draws the menu's items as radio buttons
        and marks the current one from the action's own state. The style was
        reachable only in Settings although it changes the very next reply,
        which is the definition of a mode this strip exists for.
        """
        button = Gtk.MenuButton()
        button.add_css_class("mode-chip")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        image = Gtk.Image.new_from_icon_name("format-justify-left-symbolic")
        image.set_pixel_size(13)
        box.append(image)
        self._style_label = Gtk.Label(label="Ordinary")
        self._style_label.add_css_class("mode-chip-label")
        box.append(self._style_label)
        button.set_child(box)
        menu = Gio.Menu()
        for key, _word, line in self._STYLES:
            menu.append(line, f"app.reply-style::{key}")
        button.set_menu_model(menu)
        return button

    #: Below this window width the chips show icons only. Measured: with words
    #: the strip needs 512px, the window can be 380px, and between the two the
    #: strip was clipped at the right - losing Dictate first.
    COMPACT_BELOW = 560

    def set_compact(self, compact: bool) -> None:
        """Icons only, or icons and words. Tooltips and accessible names are
        unchanged either way, so nothing a screen reader says is lost."""
        stack = [self]
        while stack:
            node = stack.pop()
            if isinstance(node, Gtk.Label) and "mode-chip-label" in node.get_css_classes():
                node.set_visible(not compact)
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()

    def _read_reply_style(self) -> str:
        try:
            style = str(self._config.reply_style or "ordinary")
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the reply style", exc_info=True)
            style = "ordinary"
        return style if style in {k for k, _w, _l in self._STYLES} else "ordinary"

    def _dictate_button(self) -> Gtk.Button:
        """The long-turn control, which has no state to hold."""
        button = Gtk.Button()
        button.add_css_class("mode-chip")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        image = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        image.set_pixel_size(13)
        box.append(image)
        text = Gtk.Label(label="Dictate")
        text.add_css_class("mode-chip-label")
        box.append(text)
        button.set_child(box)
        button.set_action_name("app.dictate")
        button.set_tooltip_text(
            "Dictate one long turn - up to five minutes, ending on a longer "
            "silence than an ordinary turn (Ctrl+Shift+D)"
        )
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Dictate: start one long listening turn"])
        return button

    # -- the state, read from whoever owns it ---------------------------

    def _read_privacy(self) -> bool:
        try:
            return bool(self._config.privacy_mode)
        except Exception:                               # noqa: BLE001
            # A config that cannot answer must not take the window down over a
            # chip. False is the safe reading: the local-only claim is the one
            # that has to be earned, so an unreadable setting is shown as off.
            logger.warning("could not read the privacy-mode setting",
                           exc_info=True)
            return False

    def _read_plan_mode(self) -> bool:
        try:
            from shani_chronoa import planmode
            return bool(planmode.is_enabled())
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the plan-mode state", exc_info=True)
            return False

    def _read_wake_word(self) -> bool:
        try:
            return bool(self._config.wake_word_enabled)
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the wake-word setting", exc_info=True)
            return False

    def _read_barge_in(self) -> bool:
        try:
            return bool(self._config.barge_in_vad_enabled)
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the barge-in setting", exc_info=True)
            return False

    # -- keeping the chips honest ---------------------------------------

    def refresh(self) -> None:
        """Redraw every chip from the state that owns it.

        Called on construction, after every toggle, and when the window regains
        focus - because the Settings window flips the same settings through the
        same actions, and a chip that only refreshed on its own click would go on
        saying "off" for a setting that is on.
        """
        self._syncing = True
        try:
            for attribute, label, _icon, _action, _reader in self._CHIPS:
                chip = getattr(self, attribute)
                on = self._readers[attribute]()
                chip.set_active(on)
                if on:
                    chip.add_css_class("mode-chip-on")
                else:
                    chip.remove_css_class("mode-chip-on")
                chip.set_tooltip_text(f"{label}: {'on' if on else 'off'}")
                chip.update_property(
                    [Gtk.AccessibleProperty.LABEL],
                    [f"{label} mode, currently {'on' if on else 'off'}"])
            style_chip = getattr(self, "_style_chip", None)
            if style_chip is not None:
                style = self._read_reply_style()
                word = next(w for k, w, _l in self._STYLES if k == style)
                self._style_label.set_text(word)
                # Highlighted like an "on" chip when it is not the default, so a
                # brief or explanatory setting is visible at a glance.
                if style == "ordinary":
                    style_chip.remove_css_class("mode-chip-on")
                else:
                    style_chip.add_css_class("mode-chip-on")
                style_chip.set_tooltip_text(
                    f"Reply style: {word}. Applies from the next reply.")
                style_chip.update_property(
                    [Gtk.AccessibleProperty.LABEL], [f"Reply style, currently {word}"])
        finally:
            self._syncing = False

    def _on_toggled(self, button: Gtk.ToggleButton, action: str) -> None:
        """A chip was clicked: run the app's own action, then read back.

        Not `set_action_name`. A `GSimpleAction` carries no boolean state, so a
        toggle button bound to one keeps its own pressed state, flips it
        locally, and diverges from the app - a chip that reads "on" for a plan
        mode that was refused because the wake word was unavailable is worse
        than no chip at all. So the action is activated explicitly and the chip
        is then written from the truth.
        """
        if self._syncing:
            return
        app = self._app
        if app is None:
            # No application (a surface or a test built the strip alone): the
            # chip goes back to what the state says rather than keeping a press
            # nothing acted on.
            self.refresh()
            return
        try:
            app.activate_action(action, None)
        finally:
            # Whatever the action decided - including "refused, here is why" -
            # the chip ends on the answer and not on the click.
            self.refresh()


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
        self._holding = False
        self._setup_ui()
        self.connect("close-request", lambda *_a: self._on_close_request())
        try:
            self._apply_organ_css()
        except Exception:                               # noqa: BLE001
            # No display is the only way this fails, and a window with no strip
            # colour is a window that still shows what Chronoa is doing.
            logger.debug("the organ strip stylesheet could not be applied",
                         exc_info=True)
        self._sync_from_state()
        # On an idle turn, not here: building twenty panels costs about two
        # seconds of subprocesses, and a window that is on screen but not
        # usable for two seconds is worse than one whose sidebar fills in.
        # `_seed_status_dots` installs its own idle source and returns `None`,
        # so it is *called* here rather than handed to `GLib.idle_add` - handing
        # it over would re-add a source that returns `None`, i.e. never
        # removing itself, and re-seed every panel forever.
        # See `_seed_status_dots` for why the dots are drawn at all.
        self.connect("map", lambda *_a: self._seed_status_dots())

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
        provider.load_from_data(MODE_STRIP_CSS.encode())
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
            self._app, self._show_chat, self._show_surface,
            on_close=self._hide_panels)
        self._split.set_sidebar(self._sidebar_page)
        self._content_view = Adw.NavigationView()
        self._chat_page = Adw.NavigationPage(
            child=self._content_view, title="Conversation")
        self._split.set_content(self._chat_page)
        # Registered with the window, not merely constructed: an `Adw.Breakpoint`
        # that no window knows about is never evaluated. Verified by rendering
        # at 600px - before this, neither the sidebar nor the rail collapsed and
        # the conversation was left in a 150px column.
        self._sidebar_breakpoint = SidebarBreakpoint.apply(self._split)
        self.add_breakpoint(self._sidebar_breakpoint)
        #: Whether `_apply_narrow_layout` last found this window narrow. `None`
        #: until it has run once, so the first evaluation is always a
        #: transition - `False == False` would otherwise skip the very
        #: correction a window opened narrow needs.
        self._narrow_applied = None
        # The width-driven half of the same behaviour; see
        # `_apply_narrow_layout` for why the breakpoint alone was not enough.
        self.connect("notify::default-width", lambda *_a: self._apply_narrow_layout())
        self.connect("notify::maximum-width", lambda *_a: self._apply_narrow_layout())
        # ...and once when the window is first shown, because a window opened
        # already narrow emits no width change - it starts at that width.
        self.connect("map", lambda *_a: GLib.idle_add(self._apply_narrow_layout))
        # A toolbar view with the header bar on top of the column, all of it
        # under a toast overlay - so a notice is a `Adw.Toast` that floats and
        # disappears rather than a line of text that permanently steals the room
        # where the answer is being written.
        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header_bar_holder)
        toolbar.set_content(self._toasts)
        self._content_view.add(Adw.NavigationPage(
            child=toolbar, title="Conversation", tag=CHAT_TAG))
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
        # No automatic back arrow. When the split view collapses it adds one that
        # goes to the panel list - the same thing the sidebar toggle beside it
        # does - so a narrow window showed two buttons for one action, and the
        # arrow on the conversation's own page read as "back to somewhere".
        header_bar.set_show_back_button(False)
        self._sidebar_toggle = Gtk.ToggleButton()
        # `sidebar-show`, not the hamburger: `open-menu` promises a menu, and
        # this opens a column. The rail toggle is its mirror image on the right.
        self._sidebar_toggle.set_icon_name("sidebar-show-symbolic")
        self._sidebar_toggle.add_css_class("flat")
        self._sidebar_toggle.set_tooltip_text("Show or hide the panels (F9)")
        self._sidebar_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Show or hide the panels"])
        self._sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        # ...and the other direction: the split view collapsing on its own has to
        # move the button, or the button lies about what is on screen.
        self._split.connect("notify::collapsed", self._on_split_collapsed)
        # `show_content` is the half that decides what a person can actually see,
        # and nothing outside this window changes it - so it needs its own
        # notification, or the button drifts the moment the panels are hidden and
        # anything else looks.
        self._split.connect("notify::show-content", self._on_split_collapsed)
        header_bar.pack_start(self._sidebar_toggle)
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header = Gtk.Label(label="Shani Chronoa")
        header.add_css_class("cajita-header")
        # May shorten. Unellipsised, the title reserved its full width and the
        # header bar as a whole needed 632px - wider than the 600px narrow
        # layout - so every narrow window clipped the right of the chat.
        header.set_ellipsize(Pango.EllipsizeMode.END)
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
        # Compose, not "?": with a question mark here and an "i" on Help, the
        # header had Help and Quick Ask the wrong way round to every reader who
        # has used a GNOME app. Every name below exists in Adwaita and Yaru.
        quick_button.set_icon_name("mail-message-new-symbolic")
        quick_button.add_css_class("flat")
        quick_button.set_valign(Gtk.Align.CENTER)
        quick_button.set_tooltip_text("Ask one quick question (Ctrl+Shift+A)")
        quick_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Ask one quick question"]
        )
        quick_button.set_action_name("app.quick-ask")

        self._browser_button = Gtk.Button()
        # `emblem-web-symbolic` is not in the installed theme - measured
        # `has_icon` False - so this button showed a missing-glyph box on a
        # machine where the feature worked. `web-browser-symbolic` exists.
        self._browser_button.set_icon_name("web-browser-symbolic")
        self._browser_button.add_css_class("flat")
        self._browser_button.set_valign(Gtk.Align.CENTER)
        self._browser_button.set_tooltip_text("Browse the web (Ctrl+Shift+U)")
        self._browser_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Browse the web"]
        )
        self._browser_button.set_action_name("app.open-browser")

        help_button = Gtk.Button()
        help_button.set_icon_name("help-browser-symbolic")
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

        # Setup: the wizard that installs the model, the ears and the voice.
        #
        # `app.setup` was registered in `application.py` and mentioned in the
        # shortcuts window, with no control anywhere in the UI to reach it - and
        # five panels dead-end into "turn it on in Settings" with no button that
        # leads there. An action with no control is the same class of thing this
        # file keeps finding: built, registered, and not reachable. The shortcut
        # exists for the same reason as the other two: a feature nobody can
        # invoke is not a feature.
        setup_button = Gtk.Button()
        # `system-run` draws a gear in Yaru - the same glyph as Settings beside
        # it, so the header showed two identical gears. Setup installs things.
        setup_button.set_icon_name("system-software-install-symbolic")
        setup_button.add_css_class("flat")
        setup_button.set_valign(Gtk.Align.CENTER)
        setup_button.set_tooltip_text("Set Chronoa up: the model, listening and the voice")
        setup_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Set Chronoa up"])
        setup_button.set_action_name("app.setup")
        self._setup_button = setup_button

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
        self._quick_button = quick_button
        self._help_button = help_button
        self._browser_available = True
        self._overflow_button = self._build_header_overflow()
        for widget in (quick_button, self._browser_button, help_button,
                       self._overflow_button, setup_button, settings_button):
            header_bar.pack_end(widget)
        self._header_row = header_row
        self._header_bar = header_bar
        # The page was assembled before the bar existed (the column is built
        # top-down), so the bar goes into the holder now. An `Adw.Bin` exists
        # exactly for this: one slot, fillable later, no re-parenting.
        header_bar_holder.set_child(header_bar)

        # The right rail: what it is doing, what it changed that can be taken
        # back, and how it is currently armed. The conversation is a
        # transcript - it grows downward and the useful part scrolls off - so
        # these three answers had nowhere to live. `main_box` keeps every
        # child it already had; the rail is a sibling, never a parent of it,
        # so nothing in the conversation's own layout moved.
        #
        # The rail is built *after* the column is complete, because it reads
        # state the column's own widgets report (the orb state, the live tool)
        # and pointing it at half-built widgets would show "Idle" forever.
        self._rail = None                      # built at the end of this method
        self._rail_row = None
        self._rail_toggle = Gtk.ToggleButton()
        self._rail_toggle.set_icon_name("sidebar-show-right-symbolic")
        self._rail_toggle.add_css_class("flat")
        self._rail_toggle.set_valign(Gtk.Align.CENTER)
        self._rail_toggle.set_tooltip_text("Show or hide the Now rail (F10)")
        self._rail_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Show or hide the Now rail"])
        self._rail_toggle.set_active(True)
        self._rail_toggle.connect("toggled", self._on_rail_toggled)
        header_bar.pack_end(self._rail_toggle)

        main_box.set_hexpand(True)
        self._chat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._chat_row.set_vexpand(True)
        self._chat_row.append(main_box)
        self._toasts.set_child(self._chat_row)

        self._orb = ChronoaOrbWidget()
        self._orb.set_halign(Gtk.Align.CENTER)
        self._orb.set_action_name("app.toggle-listening")
        self._orb.set_tooltip_text("Start listening (Enter)")
        main_box.append(self._orb)

        # Two separate lines: the state (what the assistant is doing) and a
        # transient detail (the last thing that changed). They used to share
        # one label, so any toggle notice overwrote the state.
        self._state_label = Gtk.Label(label="Ready")
        #: Set by `set_can_answer`; None means "nothing has said yet".
        self._can_answer = None
        self._state_label.add_css_class("cajita-state")
        self._state_label.set_halign(Gtk.Align.CENTER)
        main_box.append(self._state_label)

        # **The way out of "no model", on the screen that says it.**
        #
        # The state line says "No model yet" and its tooltip explains why, and
        # that was the whole of it: the person asking a question on a fresh
        # machine was told what was wrong in a tooltip they have to hover to
        # read, with nothing to click. Every route to a fix was elsewhere - the
        # header's setup button, or the Models panel - and both are one to three
        # steps away from the one screen that reports the problem.
        #
        # So the button appears exactly when `can_answer` is False and goes away
        # when it is not, driven from `set_can_answer` so it cannot disagree
        # with the label beside it. It is `app.setup`, the same action the
        # header button and `Ctrl+Shift+S` use, so all three are one path.
        self._setup_cta = Gtk.Button(
            label="Set Chronoa up", css_classes=["suggested-action", "pill"],
            halign=Gtk.Align.CENTER, visible=False,
            tooltip_text="Install a language model, and the ears and voice")
        # Deliberately not `set_action_name("app.setup")`: the header's setup
        # button already declares that action, and a tree walk for "the setup
        # button" must find the one that is always on screen rather than this
        # one, which is hidden until there is nothing to answer with. See
        # `set_can_answer`. It still activates the same `GAction`, so the
        # shortcut, the header button and this reach one implementation.
        self._setup_cta.connect(
            "clicked",
            lambda _b: self._app.activate_action("setup", None)
            if self._app is not None else None)
        self._setup_cta.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Set Chronoa up: install a language model"])
        main_box.append(self._setup_cta)

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
            # A file write's card links straight to the Diff panel, which was
            # otherwise reachable only from the rail or by typing /diff.
            on_show_diff=lambda: self._show_surface("diff"),
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
        # The command menu: cline has a `SlashCommandMenu`, kimi-cli a
        # decorator registry, and here a list of names that each run something
        # real. `_sync_command_menu` decides when it is up - only for a leading
        # slash with no space yet.
        self._input_entry.connect("changed", lambda _e: self._sync_command_menu())
        self._command_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._command_menu = Gtk.Popover()
        self._command_menu.set_child(self._command_list)
        self._command_menu.set_position(Gtk.PositionType.BOTTOM)
        GLib.idle_add(lambda: (self._parent_command_menu(), False)[1])
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
        # Centred now - `OrganStrip.do_measure` reports the width the lights
        # need rather than FlowBox's widest-child-times-count. The history of
        # why the two earlier attempts did nothing is kept below.
        #
        # It was left-aligned in the column, 86px off the centre of the mode
        # chips below it. **Two attempts to centre it, both measured to do
        # nothing:**
        # `set_halign(Gtk.Align.CENTER)` on the strip sets the property and
        # changes no pixel: a vertical `Gtk.Box` hands its child the full
        # cross-axis width whatever the child's alignment is (measured: the
        # strip was allocated 680px of a 680px box with `halign=CENTER`).
        # Wrapping it in a horizontal row with two expanding spacers is the
        # usual trick and also did nothing, because there is no slack to
        # distribute: the strip *fills* its container. The reason is a number
        # nobody had looked at - `strip.measure(HORIZONTAL, -1)` reports a
        # natural width of **834px** against a 680px chat column, so the flow
        # box is always over-full and always takes the whole width. `Gtk.Center`
        # and `Gtk.Alignment` are both gone in GTK4 and `Gtk.FlowBox` has no
        # `justify`, so the next thing to measure is where those 834px come
        # from: the `.organ-strip > flowboxchild { padding: 0 }` rule is worth
        # 350px of it and its natural width suggests it is not matching.
        main_box.append(self._organ_strip)
        # The mode strip sits between the organ strip and the composer:
        # what Chronoa *is* (its modes) rather than what it is *doing*
        # (its organs). Wake word, plan mode, privacy and dictation are
        # four switches the user can flip, and a strip that shows which
        # are on is the answer to "what is Chronoa" without opening
        # Settings. Each chip is a toggle button wired to its own action,
        # so the state shown and the control that changes it are the same
        # widget - a label that says "on" and a switch elsewhere is two
        # widgets that can disagree.
        self._mode_strip = _ModeStrip(self._app, self._config)
        main_box.append(self._mode_strip)

        # The task card, above the composer: OpenHands and cline both keep the
        # plan in the conversation rather than behind a panel, because a plan
        # you have to go and look for is not a plan you are following. Hidden
        # entirely when nothing is outstanding - an empty card is a permanent
        # tax on attention for no information.
        self._task_card = task_card.TaskCard()
        main_box.append(self._task_card)
        # Same reading width as the conversation above it, so the composer lines
        # up with the turns instead of running the full width of the window.
        main_box.append(reading_width(input_row))
        # ...and re-read whenever the window comes back, because the Settings
        # window flips these same settings through the same actions. A strip
        # that only refreshed on its own click would keep saying "off" for a
        # mode that is on, which is the one thing an indicator must not do.
        self.connect("notify::is-active", self._on_window_activated)

        # The rail, last, because it reads the column it sits beside: the state
        # word comes from `_state_label` and the tool name from
        # `_detail_label`. Built earlier it rendered "Idle" while the window
        # said "Ready" - not because either was wrong, but because the label it
        # reads did not exist yet. A rail that answers a question from
        # half-built widgets is worse than one built a moment later.
        self._rail = rail_module.NowRail(get_state=self._rail_state,
                                         get_tool=self._rail_tool,
                                         on_open=self._show_surface,
                                         get_context=self._rail_context)
        self._chat_row.append(self._rail)
        # **Hidden, not shrunk, on a narrow window.** At a fraction of its width
        # the rail wraps every label into two and three lines and reads as a
        # column of broken words; the conversation is what the window is for.
        # Same reasoning as `SidebarBreakpoint` on the left, and the toggle stays
        # available either way, so nothing becomes unreachable.
        self._rail_breakpoint = rail_module.RailBreakpoint.apply(self._rail, 1040)
        self.add_breakpoint(self._rail_breakpoint)
        # Two seconds, because that is the smallest number that makes a countdown
        # read as a countdown rather than as a stuck label.
        self._rail_ticket = GLib.timeout_add_seconds(2, self._on_rail_tick)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    # -- narrow-window behaviour ------------------------------------------

    #: Below this width the sidebar cannot stay a column, and becomes a drawer
    #: behind the ☰ button instead.
    SIDEBAR_COLLAPSE_WIDTH = 720

    def _apply_narrow_layout(self) -> None:
        """Collapse the sidebar by hand when the window is narrow.

        **The breakpoint alone does not do it, and this is the measurement.**
        With `SidebarBreakpoint` registered and firing, `split.collapsed` was
        still `False` at 600px, while `split.set_collapsed(True)` called after
        allocation returned `True` every time. The setter therefore runs before
        the split view finishes setting itself up, and the split view then
        overwrites it - and a breakpoint only re-applies when its *condition*
        changes, so a window that never changes width is never corrected again.

        So the condition is evaluated here as well, on every width change. It
        goes through `_set_panels_visible`, not `set_collapsed` alone, because
        collapsing *and* leaving the drawer open covers the whole window with
        the panel list - measured at 640px: the render showed the panel list
        filling the screen with the conversation nowhere in it, which is the
        same failure `_set_panels_visible`'s own docstring records for F9. The
        pair (`collapsed` for the layout, `show_content` for which pane is on
        top) is the contract, and one half of it is a bug.

        Widening back does not re-open the panels if the person had closed them:
        it only stops the window from *forcing* them shut.

        **Which is why this runs on a transition and not on every notification.**
        The first version re-evaluated the condition every time the width
        changed, and at a wide width its "widening" branch was
        `if collapsed: set_collapsed(False)` - so *any* late width notification
        re-opened panels the person had just closed with the toggle. Measured as
        `tests/test_sidebar_toggle.py`'s two round-trip tests failing
        intermittently (2 of 5 runs with this branch, 0 of 5 without it), which
        is the shape of a race: the notification lands after the press about
        half the time.
        """
        width = self.get_width()
        if width <= 0:
            return
        if getattr(self, "_mode_strip", None) is not None:
            self._mode_strip.set_compact(width < _ModeStrip.COMPACT_BELOW)
        narrow = width <= self.SIDEBAR_COLLAPSE_WIDTH
        if narrow != self._narrow_applied:
            self._narrow_applied = narrow
            if narrow:
                self._set_panels_visible(False)
            elif self._split.get_collapsed():
                self._split.set_collapsed(False)
            self._fold_header(narrow)
        self._sync_sidebar_toggle()

    def _build_header_overflow(self) -> Gtk.MenuButton:
        """A "More" button holding Quick ask, Browse and Help on a narrow window.

        Setup and Settings stay in the bar at every width - an action reachable
        only from a closed menu is the dead end `test_setup_button_is_reachable`
        exists for. These three are the ones a narrow window can spare: each
        also has a shortcut, and each opens a second window rather than acting
        on this one. Labelled buttons, not a menu model, so each is a real
        control with its own tooltip and accessible name.
        """
        button = Gtk.MenuButton()
        button.set_icon_name("view-more-symbolic")
        button.add_css_class("flat")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text("More")
        button.update_property([Gtk.AccessibleProperty.LABEL], ["More actions"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover()
        popover.set_child(box)

        def item(label, icon, action=None, handler=None):
            entry = Gtk.Button()
            entry.add_css_class("flat")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.append(Gtk.Image.new_from_icon_name(icon))
            line.append(Gtk.Label(label=label, xalign=0.0))
            entry.set_child(line)
            entry.update_property([Gtk.AccessibleProperty.LABEL], [label])
            if action:
                entry.set_action_name(action)
            if handler:
                entry.connect("clicked", handler)
            entry.connect("clicked", lambda _b: popover.popdown())
            box.append(entry)
            return entry

        item("Ask one quick question", "mail-message-new-symbolic", action="app.quick-ask")
        self._overflow_browser = item("Browse the web", "web-browser-symbolic",
                                      action="app.open-browser")
        item("What can Chronoa do?", "help-browser-symbolic",
             handler=lambda _b: self.open_help())
        button.set_popover(popover)
        button.set_visible(False)
        return button

    def _fold_header(self, narrow: bool) -> None:
        """Move the three spare header buttons into "More", or back out."""
        self._quick_button.set_visible(not narrow)
        self._help_button.set_visible(not narrow)
        self._browser_button.set_visible(self._browser_available and not narrow)
        self._overflow_button.set_visible(narrow)

    def _on_window_activated(self, *_args) -> None:
        """The window came forward: re-read the modes from their owners.

        Settings flips plan mode, privacy and the wake word through the same
        actions this strip does, so the only way a chip learns about a change
        made elsewhere is to look again. Reading is cheap - three gsetting
        reads and one module flag - and it happens on focus, not on a timer.
        """
        if self._mode_strip is not None:
            self._mode_strip.refresh()
        if getattr(self, "_task_card", None) is not None:
            self._task_card.refresh()

    def set_state(self, state: AssistantState) -> None:
        """Set the assistant state. The single entry point for it."""
        self._state = state
        self._sync_from_state()
        # The rail answers "what is it doing" from the state line, so it is
        # refreshed here as well as on its tick: a state change is the moment
        # the rail's answer must not lag behind the orb.
        if getattr(self, "_rail", None) is not None:
            self._on_rail_tick()
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
        # The halo pulses only while the mic is hot or the reply is being heard;
        # the last level must not freeze on a thinking/idle halo.
        if self._state not in (AssistantState.LISTENING, AssistantState.SPEAKING, AssistantState.INTERRUPTING):
            self._orb.set_level(0.0)
        # `set_can_answer` wins over the state machine's own label, because "no
        # model" is true of every state at once while "Ready" is only true of
        # one - and on a machine with nothing installed the label was showing the
        # one.
        if self._can_answer is False:
            self._state_label.set_label("No model yet")
            self._state_label.add_css_class("dim-label")
        else:
            self._state_label.set_label(self._state.label)
            self._state_label.remove_css_class("dim-label")
        # The mic is genuinely hot in exactly two states.
        hot = self._state in (AssistantState.LISTENING, AssistantState.INTERRUPTING)
        self._mic_icon.set_visible(True)
        self._mic_icon.set_from_icon_name(
            "audio-input-microphone-symbolic" if hot else "microphone-disabled-symbolic"
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

    def set_playback_level(self, level: float) -> None:
        """Feed the playback level to the orb (sayri's orb-follows-playback).

        Ignored unless speaking, so a level that arrives after the turn ended
        cannot make the halo pulse at nothing.
        """
        if self._state is AssistantState.SPEAKING:
            self._orb.set_level(level)

    def _on_close_request(self) -> bool:
        """A close with the wake word on hides, rather than quitting.

        The wake word is the only way a user can reach Chronoa without this
        window; quitting it over "close" would silence the hands-free path
        the user explicitly turned on. With the wake word off there is no
        hands-free path to keep alive, so the close proceeds normally and
        quits the app. Hold/release keeps the application alive while
        hidden; a real quit still ends it.
        """
        try:
            enabled = self._config.wake_word_enabled
        except Exception:            # noqa: BLE001 - never leave the user unable to close
            enabled = False
        if not enabled:
            return False
        self._app.hold()
        self._holding = True
        self.set_visible(False)
        return True

    def show(self) -> None:
        """Bring the window back, releasing the hold the close-request took."""
        self.present()
        if getattr(self, "_holding", False):
            self._app.release()
            self._holding = False
        release_hidden = getattr(self._app, "_release_hidden_hold", None)
        if callable(release_hidden):
            release_hidden()

    # -- the right rail ----------------------------------------------------

    def _rail_state(self) -> str:
        """The live state word, read from the state line itself.

        `_state_label` is the single place "what is Chronoa doing" is spelled
        out - `set_state` derives it, and `set_can_answer` may override it for
        the no-model case. Reading the widget rather than keeping a third copy
        is the point: a rail that tracked state separately would be a second
        answer to that question, free to disagree with the orb and the line
        under it, which is the failure this window keeps finding in smaller
        forms.
        """
        label = getattr(self, "_state_label", None)
        return (label.get_label() if label is not None else "") or "Idle"

    def _rail_context(self):
        """The last request's context report, straight from the assistant.

        Through the application object, because that is where the assistant
        lives: `window.assistant` is the *window's* placeholder text helper in
        some builds, and a meter wired to the wrong `assistant` reads zero
        forever - which looks exactly like an assistant that uses no context.
        """
        app = getattr(self, "_app", None)
        assistant = getattr(app, "assistant", None)
        report = getattr(assistant, "context_report", None)
        return report() if callable(report) else None

    def _rail_tool(self) -> str:
        """The tool currently running, as the status line reports it.

        Read out of the detail label rather than kept separately: the detail
        line is what a person is already looking at for this, and a second
        record would drift.
        """
        label = getattr(self, "_detail_label", None)
        text = label.get_label() if label is not None else ""
        return text.strip()

    def _on_rail_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._rail is not None:
            self._rail.set_visible(button.get_active())

    def _on_rail_tick(self) -> bool:
        """Refresh on a two-second tick, so a countdown is a countdown."""
        if self._rail is not None:
            try:
                self._rail.refresh()
            except Exception as exc:  # noqa: BLE001 - the tick must never die
                logger.warning("The Now rail failed to refresh: %s", exc)
        return True

    def toggle_rail(self) -> None:
        if self._rail_toggle is not None:
            self._rail_toggle.set_active(not self._rail_toggle.get_active())

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

    def set_can_answer(self, can: bool, why: str = "") -> None:
        """Whether anything can answer at all, which the state line must show.

        **The orb said "Ready" on a machine with no model**, and the line under
        it said "LLM unavailable" - two states, one screen, one of them false.
        "Ready" is the IDLE label, so it was true by construction on every
        machine: the state machine describes what the assistant is *doing*, and
        doing nothing is what idle means. What it must also answer is whether
        there is anything here to do it *with*.

        So this overrides the text when there is no model, and restores it when
        there is. Deliberately only the text and the dimming - the orb's colour
        still belongs to the state machine, because "nothing can answer" is not
        an error and painting it red would be the opposite kind of lie.
        """
        self._can_answer = bool(can)
        self._no_model_reason = why or ""
        # Shown only while there is nothing to answer with, so it cannot become
        # a second thing offering setup on a machine that is already set up.
        #
        # The header's own setup button is deliberately *not* hidden here. The
        # CTA answers "I cannot answer, fix it" on the screen that says so; the
        # header button answers "open the wizard", which is wanted on a working
        # machine too - to change the model, the voice, or to re-run a step. A
        # first version hid both and `tests/test_setup_button_is_reachable.py`
        # failed on it (`the setup button is not mapped`), which is the test
        # earning its keep: an action that is registered, named in the shortcuts
        # window and unreachable is the exact gap that file was written for.
        #
        # The CTA activates `app.setup` through a handler rather than through
        # `set_action_name`, and that is load-bearing rather than incidental.
        # Two widgets declaring the same action leaves "the setup button"
        # ambiguous, and a finder that walks the tree then picks whichever it
        # reaches first - which here is a control that is deliberately
        # invisible until there is no model. The header button stays the one
        # widget that declares the action; the CTA reaches the same
        # `GAction` through it, so there is still one implementation.
        self._setup_cta.set_visible(not can)
        if not can:
            self._state_label.set_tooltip_text(
                why or "Chronoa cannot answer until a language model is set up")
        else:
            self._state_label.remove_css_class("dim-label")
            self._state_label.set_tooltip_text("")

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

    # ── addressable pages ──────────────────────────────────────────────────
    #
    # The main window is one conversation view, so there is nothing here to
    # scroll to - but three of its states are real destinations someone else may
    # want to name: the conversation itself, the quick-question panel, and the
    # panel containing the conversation list. Each is a page id so a
    # notification or a keybinding can open one, and so a test does not have to
    # click a button whose position it guessed.
    def show_page(self, page_id: str) -> bool:
        from shani_chronoa import pages as page_registry
        page_id = page_registry.resolve("main", page_id) or page_id
        if page_id == "conversation":
            self.present()
            return True
        if page_id == "quick-ask":
            action = self.lookup_action("app.quick-ask")
            if action is None:
                return False
            action.activate(None)
            return True
        if page_id in ("conversations", "panels"):
            self.toggle_sidebar()
            return True
        return False

    def toggle_sidebar(self) -> None:
        """F9, and anything else that wants to flip the panels."""
        if self._sidebar_toggle is not None:
            self._sidebar_toggle.set_active(not self._sidebar_toggle.get_active())

    def _set_panels_visible(self, visible: bool) -> None:
        """Show the panels as a column beside the chat, or take them away.

        **`Adw.NavigationSplitView` has two properties, and "hidden" is not
        either of them.** `collapsed` chooses the *layout* - a column beside the
        content, or a drawer over it - and `show_content` chooses which pane is
        on top while collapsed. Neither one alone hides the sidebar. Measured on
        libadwaita 1.5, in a 1100x700 window:

        | state | collapsed | show_content | sidebar | chat | toggle mapped |
        |---|---|---|---|---|---|
        | column | False | False | 275px | 825px at x=275 | yes |
        | **hidden** | True | **True** | behind | **1100px at x=0** | **yes** |
        | drawer | True | False | 1100px over everything | behind | **no** |

        The last row is the bug that was reported as "clicking expands the
        sidebar but there is no way to retract it". This used to do
        `set_collapsed(button.get_active())` alone, which is row three: pressing
        a button labelled *show or hide the panels* covered the entire window
        with the panel list, and because the drawer is drawn over the content it
        also covered the button that put it there - the header's own toggle went
        `get_mapped() == False`. F9 was then the only way back, and the sidebar's
        "Conversation" row did not close it either.

        So the pair is the contract: `show_content` decides what a person can
        see, `collapsed` decides whether it is a column or an overlay, and
        "hidden" is the one combination where the toggle stays on screen.

        **`show_content` is named for the *content*, not for the sidebar** -
        `True` means the chat is on top. A first version passed `visible`
        straight through and got the drawer instead of the hidden sidebar, so the
        first fix reproduced the very symptom it was written for; measured in
        both directions, which is the only reason this is stated rather than
        assumed.

        **The button is written last, and the notify handler stands down while
        this runs.** The two properties notify *between* them, and the handler's
        job is to set the button - which emits `toggled`, which calls straight
        back in here. Measured: without the guard, `set_show_content(True)`
        notified, the handler read the half-updated split (still `collapsed`,
        which means "a column", which means "the panels are showing"), put the
        button back to active, and that re-entered with `visible=True` and wrote
        `show_content=False` - so a hide ended in the drawer again, having been
        asked for the hidden sidebar. Two property writes are not a transaction
        and GTK does not batch them; this guard is what makes them one.
        """
        self._writing_panels = True
        try:
            self._split.set_show_content(not visible)
            self._split.set_collapsed(not visible)
        finally:
            self._writing_panels = False
        self._sidebar_toggle.set_active(visible)
        self._sidebar_page.set_drawer_mode(self._panels_as_drawer())

    def _panels_visible(self) -> bool:
        """Whether the panels are on screen, in whichever layout is in force.

        The single read the button's state is derived from, so the two cannot
        disagree. Not `get_collapsed()`: that is a layout decision the
        breakpoint makes on a narrow window, where the panels are *showing* as a
        drawer - which is why a button reading "collapsed" was showing "off" while
        the panel list was on screen.
        """
        if not self._split.get_collapsed():
            return True
        return not self._split.get_show_content()

    def _hide_panels(self) -> None:
        """The drawer's own close button: put the panel list away, change nothing else.

        Separate from `_show_chat` because the drawer can be sitting over a panel
        that is already open, and closing a drawer must not also pop the content
        stack out from under it.
        """
        self._set_panels_visible(False)
        self._sync_sidebar_toggle()

    def _panels_as_drawer(self) -> bool:
        """Whether the panels are an overlay covering the content.

        The one state where the window's own toggle is not on screen - measured,
        the overlay is allocated the whole window and covers the header - and so
        the one state where the sidebar has to carry a close control of its own.
        """
        return bool(self._split.get_collapsed()
                    and not self._split.get_show_content())

    def _on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        """Show or hide the panels - but only for a press, not for a sync.

        The button is active when the panels are on screen, so this is the only
        place the polarity is decided; every other route calls
        `_set_panels_visible` and lets `_sync_sidebar_toggle` follow.

        The `_writing_panels` guard is load-bearing and was found by a test, not
        by reading. `_sync_sidebar_toggle` writes this button to match the split
        view, and `set_active` emits `toggled`, which lands here - so a
        *notification* from the split view was being answered by *overwriting* the
        split view. Concretely: the breakpoint collapsed the sidebar, this
        handler put the button where it belonged, and the handler that fired
        because of that write immediately set both properties back to "panels
        hidden", undoing the collapse. `tests/test_sidebar_toggle.py` catches it
        as "the drawer state was never reached".
        """
        if getattr(self, "_writing_panels", False):
            return
        self._set_panels_visible(button.get_active())

    def _on_split_collapsed(self, _split: object, _param: object) -> None:
        """Keep the toggle honest when something *else* moves the sidebar.

        The split view changes both properties without touching the button: the
        `max-width: 720px` breakpoint on a narrow window, a drag of the sidebar's
        edge, and `set_collapsed` from any other code. Without this the button
        reads "panels hidden" while the panel list is on screen, which is the
        control-whose-state-is-a-lie failure.

        `_sync_sidebar_toggle` only sets the button when it disagrees, so this
        cannot loop: `set_active` emits `toggled`, which writes both properties
        the values they already hold, which emits nothing further.

        It does nothing at all while `_set_panels_visible` is mid-write; see
        there for the measurement that made that guard necessary.
        """
        if getattr(self, "_writing_panels", False):
            return
        self._sync_sidebar_toggle()

    def _sync_sidebar_toggle(self) -> None:
        """Follow the split view, not the other way round.

        Without this, collapsing the sidebar by dragging its edge, or by the
        breakpoint on a narrow window, leaves the button pressed while the panels
        are off screen - or unpressed while they are on it.
        """
        visible = self._panels_visible()
        # The same guard as `_set_panels_visible`, for the same reason and in the
        # other direction: writing the button emits `toggled`, and without this
        # the "follow the split view" path would answer its own notification by
        # driving the split view back to whatever the button had just been set
        # from. Written inside the guard so the button and the drawer's close bar
        # always agree with each other.
        writing = getattr(self, "_writing_panels", False)
        self._writing_panels = True
        try:
            if self._sidebar_toggle.get_active() != visible:
                self._sidebar_toggle.set_active(visible)
            # The drawer gets its own close control, because in that state this
            # button is under it. Hidden everywhere else, which is the whole
            # point: in the column layout the button above is reachable and a
            # second close arrow would be the duplicate this window already had
            # once.
            self._sidebar_page.set_drawer_mode(self._panels_as_drawer())
        finally:
            self._writing_panels = writing

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
        """Back to the conversation, from any panel or from the sidebar row.

        Uses `pop_to_tag(CHAT_TAG)`, which is the only way libadwaita 1.5 offers
        to go back to a known page - there is no `set_visible_page`, and calling
        it raised `AttributeError`, so clicking "Conversation" did nothing and
        every panel's back arrow died with it.

        Popping rather than setting the page also unwinds the stack, so going
        back to the chat and opening a panel again builds it fresh instead of
        pushing a second copy on top of the first.
        """
        view = self._content_view
        if view.get_visible_page() is not view.find_page(CHAT_TAG):
            view.pop_to_tag(CHAT_TAG)
        self._sidebar_page.select(None)
        # Coming back to the conversation from inside the panel list also closes
        # the list, and it has to. While the panels are showing as a drawer they
        # are drawn over everything - including the header's own toggle, which
        # goes `get_mapped() == False` - so a drawer with no way out of itself is
        # a dead end. "Conversation" is the one row in it that means "not the
        # panels", so this row is the drawer close button. `_pop_panel` below
        # deliberately does not do this: the back arrow inside a panel means "back
        # to the chat", not "and now hide the list I just used".
        self._set_panels_visible(False)
        self._sync_sidebar_toggle()

    def _pop_panel(self) -> None:
        """The back arrow inside a panel's own header bar.

        Same route as `_show_chat`, and for the same reason: these pages are
        built by a dozen modules that none of them tag, so the only page worth
        popping *to* is the conversation - which now has a tag for exactly this.
        """
        view = self._content_view
        if view.get_visible_page() is not view.find_page(CHAT_TAG):
            view.pop_to_tag(CHAT_TAG)
        self._sidebar_page.select(None)

    def _seed_status_dots(self) -> None:
        """Ask every panel how it is, one per idle turn, and draw the dots.

        **A dot that only appears once you have opened the panel is not a
        dashboard.** Measured on the rendered window: with all twenty surfaces
        exposing `status()`, a freshly opened window still showed twenty rows
        with no dot at all, because `_show_surface` was the only caller of
        `_panel_status` - so the sidebar could only ever tell you about a panel
        you had already been inside. That defeats the entire point of a health
        column: the question is "is anything wrong *now*", asked *before* opening
        anything.

        One panel per idle turn, not all twenty at once. Building them is real
        work - they shell out to `systemctl`, `busctl` and `gsettings`, and read
        the filesystem - and measured, twenty panels cost around two seconds.
        Doing that synchronously in `_setup_ui` would put two seconds between
        the window appearing and the window being usable. Idle turns cost
        nothing a person can perceive and the dots fill in over the first second.

        Failures are the existing contract: a panel that will not build gets no
        dot, which is the same answer `_panel_status` gives for a panel that
        cannot say. A panel that fails here is not pushed onto the content view,
        so opening it later still builds it properly and reports the failure on
        screen - this pass only asks for the summary.
        """
        from shani_chronoa.gui import surfaces

        names = [n for n in surfaces.SURFACE_IDS
                 if n in self._sidebar_page._surface_rows]
        if not names:
            return

        available = surfaces.available_surfaces()

        import sys as _sys

        def prebuilt(build) -> bool:
            # A panel whose build is slow by nature (Diagnostics runs real
            # probes - a screen capture among them) sets PREBUILD = False and is
            # built when opened, not here.
            module = _sys.modules.get(getattr(build[2], "__module__", ""), None)
            return getattr(module, "PREBUILD", True)

        def one() -> bool:
            # ONE panel per main-loop turn. This used to loop over all of them
            # inside a single callback, so every panel was built back to back
            # with the main loop held - measured 2026-10-08: ~8 s just after
            # startup, during which the mic button did nothing and a spoken
            # request was lost before recording began.
            while names:
                name = names.pop(0)
                page = self._surface_pages.get(name)
                if page is None:
                    build = available.get(name)
                    if build is None or not prebuilt(build):
                        continue
                    try:
                        page = build[2](getattr(self, "_app", None))
                    except Exception:                  # noqa: BLE001
                        logger.info("panel %r did not build; its row keeps no "
                                    "dot until it is opened", name,
                                    exc_info=True)
                        continue
                    # Kept, so opening it later is instant and the dot cannot
                    # change underneath the person who is looking at it.
                    self._surface_pages[name] = page
                self._sidebar_page.set_status(name, _panel_status(page))
                return GLib.SOURCE_CONTINUE if names else GLib.SOURCE_REMOVE
            return GLib.SOURCE_REMOVE

        GLib.idle_add(one, priority=GLib.PRIORITY_DEFAULT_IDLE)

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
        # Ask the panel how it is, and put that on its sidebar row. The panel
        # answers with `page.status()` if it has one; most do not, and a panel
        # that cannot say is not a panel that is broken - it is a panel with no
        # opinion, so its row keeps no dot rather than being given a green one
        # it did not earn.
        self._sidebar_page.set_status(name, _panel_status(page))
        # A panel was chosen from the sidebar, so the sidebar has done its job.
        # `show_content` is what puts the chat back on top - setting `collapsed`
        # alone would only trade a drawer for a column and, on a wide window,
        # leave the panel's own list covering the panel.
        self._set_panels_visible(False)
        self._sync_sidebar_toggle()

    def set_browser_available(self, available: bool) -> None:
        """Hide the browser button when WebKitGTK is not installed.

        A control that opens nothing and explains why is worse than no control
        for someone who has not read the packaging notes.
        """
        self._browser_available = bool(available)
        self._browser_button.set_visible(available and not self._narrow_applied)
        if getattr(self, "_overflow_browser", None) is not None:
            self._overflow_browser.set_visible(bool(available))

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

    def add_notice_row(self, sentence: str, kind: str = "compaction") -> None:
        """A housekeeping note in the transcript - see the transcript's own
        method for why these exist rather than being only a log line."""
        self._transcript.add_notice_row(sentence, kind)

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

    def _run_command(self, text: str) -> bool:
        """Run a `/command` if the text is one. True when it was handled.

        The model is not consulted for these: on a small local model, asking it
        to translate "clear this conversation" into the right tool call is a
        coin flip. Everything still goes through the same permission layers -
        `/undo` runs the real skill, so the same consent key applies.
        """
        from shani_chronoa.gui import commands as slash
        command, argument = slash.lookup(text)
        if command is None:
            return False
        try:
            said = command.run(self, argument)
        except Exception as exc:  # noqa: BLE001 - a command must not take the window
            logger.error(f"Command /{command.name} failed: {exc}", exc_info=True)
            self.set_status(f"/{command.name} did not work: {exc}")
            return True
        if said:
            self.set_response(said)
        else:
            self.set_status(f"/{command.name} - {command.summary}")
        if self._command_menu is not None:
            self._command_menu.popdown()
        return True

    def _sync_command_menu(self) -> None:
        """Show the command list while the field starts with a slash.

        Only on a *leading* slash, and only while it is still just a slash or a
        name: the menu is a discovery aid, and leaving it up while somebody
        types an ordinary sentence would be a popover arguing with them.
        """
        text = self._input_entry.get_text()
        showing = text.startswith("/") and " " not in text
        if showing:
            prefix = text[1:].lower()
            self._fill_command_menu(prefix)
        if self._command_menu is not None:
            if showing and not self._command_menu.get_visible():
                # Parented first, here as well as on idle: `popup()` realizes
                # the popover, and realizing one with no parent segfaults in
                # GTK. Typing "/" before the idle callback had run crashed the
                # whole app - reproduced under CPU load, two of two runs.
                self._parent_command_menu()
                self._command_menu.popup()
            elif not showing and self._command_menu.get_visible():
                self._command_menu.popdown()

    def _parent_command_menu(self) -> None:
        """Attach the command popover to the entry, once. Safe to call again."""
        if self._command_menu is not None and self._command_menu.get_parent() is None:
            self._command_menu.set_parent(self._input_entry)

    def _fill_command_menu(self, prefix: str) -> None:
        from shani_chronoa.gui import commands as slash
        child = self._command_list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._command_list.remove(child)
            child = following
        for name, command in sorted(slash.commands().items()):
            if prefix and not name.startswith(prefix):
                continue
            row = Gtk.Button()
            row.add_css_class("flat")
            row.set_size_request(-1, -1)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label=f"/{name}")
            box.append(label)
            summary = Gtk.Label(label=command.summary)
            summary.add_css_class("dim-label")
            summary.set_xalign(1.0)
            summary.set_hexpand(True)
            # Right padding, because a summary that reaches the popover's edge
            # is a sentence that looks cut in half. Found by rendering the open
            # menu and looking at it: "/diff  Show what Chronoa changed, file by
            # file" ran straight into the rounded corner, with nothing between
            # the last letter and the border. Ten pixels is enough to read as
            # padding and small enough not to widen the menu noticeably.
            summary.set_margin_end(10)
            box.append(summary)
            row.set_child(box)
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                [f"/{name}: {command.summary}"])
            row.connect("clicked", lambda _b, n=name: self._complete_command(n))
            self._command_list.append(row)
        # The person's own commands (`~/.config/shani-chronoa/commands/*.md`,
        # `user_prompts`). They worked when typed and were expanded on send, but
        # this menu listed only the built-ins, so a command someone had written
        # was reachable only by remembering its name. A built-in of the same
        # name wins, as it does when the line is sent.
        try:
            from shani_chronoa import user_prompts
            own = user_prompts.commands()
        except Exception:  # noqa: BLE001 - a menu must not take the window
            own = {}
        builtin = set(slash.commands())
        for name, path in sorted(own.items()):
            if name in builtin or (prefix and not name.startswith(prefix)):
                continue
            summary_text = user_prompts.command_summary(path) or "your command"
            row = Gtk.Button()
            row.add_css_class("flat")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.append(Gtk.Label(label=f"/{name}"))
            mine = Gtk.Label(label="yours")
            mine.add_css_class("dim-label")
            mine.add_css_class("caption")
            box.append(mine)
            summary = Gtk.Label(label=summary_text)
            summary.add_css_class("dim-label")
            summary.set_xalign(1.0)
            summary.set_hexpand(True)
            summary.set_ellipsize(Pango.EllipsizeMode.END)
            summary.set_margin_end(10)
            box.append(summary)
            row.set_child(box)
            row.set_tooltip_text(str(path))
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                [f"/{name}, your command: {summary_text}"])
            row.connect("clicked", lambda _b, n=name: self._complete_command(n))
            self._command_list.append(row)

    def _complete_command(self, name: str) -> None:
        """Put `/name ` in the field and hand focus back to typing."""
        self._input_entry.set_text(f"/{name} ")
        self._input_entry.set_position(-1)
        self._input_entry.grab_focus()
        if self._command_menu is not None:
            self._command_menu.popdown()

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
        if self._run_command(text):
            return
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

    def set_input_text(self, text: str) -> None:
        """Put text in the composer without sending it.

        Separate from `submit_text` because "show this in the box" and "send
        this" are different acts, and a caller wanting the first used to have
        neither: it could only reach `_input_entry` directly, which is how the
        `?` argument of `/help` ended up nowhere.
        """
        self._input_entry.set_text(text or "")
        self._input_entry.grab_focus()

    def clear_input(self) -> None:
        self._input_entry.set_text("")

    __gsignals__ = {
        "user-input": (GObject.SignalFlags.RUN_FIRST, str, (str,)),
    }


# The main window's addressable pages, declared at import time so that
# `shani-chronoa --show-page=main:quick-ask`, a notification, or a keybinding
# can name one. `show_page` above has always understood these ids; the ids
# themselves were never registered, so `pages.show("main:conversation")`
# answered "no window called 'main'" - and the *main window* was the one
# destination every caller is most likely to name.
def _register_main_pages() -> None:
    from shani_chronoa import pages as page_registry
    page_registry.register(
        "main",
        [("conversation", "Conversation"), ("quick-ask", "Quick ask"),
         ("conversations", "Saved conversations"), ("panels", "Panels")],
        factory=lambda app, config=None: ChronoaWindow(app),
    )


_register_main_pages()
