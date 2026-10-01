"""GTK4 GUI module for Shani Chronoa.

The orb widget and the main window.

Design notes that are not obvious from the widget tree
-----------------------------------------------------

**One state, one source of truth.** `AssistantState` is the only description
of what the assistant is doing. The orb's colour, its icon, the status line's
wording and whether the stop button appears are all *derived* from it in
`_sync_from_state()`. This is a structural fix, not a tidiness one: the
previous window had `set_listening`/`set_processing`/`set_error` as three
independent booleans on the orb plus a separate `set_status()` string, so the
orb could show green while the label said "Ready" and nothing detected it. A
state cannot disagree with itself.

**State and transient notices are different channels.** `set_status()` writes
the *detail* line ("Wake word: ON", "Wake word unavailable"). It deliberately
cannot change the state line, which is why a toggle can no longer erase the
fact that the assistant is mid-turn - which is exactly what happened before:
`_toggle_privacy`, `_set_wake_word_active` and six other handlers all wrote to
the one label the state was also written to.

**Turns accumulate.** The response area used to be a single `Gtk.Label` that
each response replaced, so the window showed the last thing said and nothing
else. It is now a list of turns, so a voice conversation is readable
afterwards rather than only while it is happening.

**Not colour-only.** Every state pairs its colour with a distinct icon and a
distinct text label. Colour alone is not an accessible signal, and
`set_orb_state`'s old green/amber/red mapping conveyed three of six states by
hue alone.
"""

import enum
import logging
import re
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk, Gdk, GLib, GObject  # type: ignore

from shani_chronoa import capabilities, markdown_lite

logger = logging.getLogger(__name__)


class AssistantState(enum.Enum):
    """What the assistant is doing right now.

    `INTERRUPTING` is deliberately a real state rather than an instant jump
    from SPEAKING to LISTENING. Cutting straight across makes the UI look
    broken - the user cannot tell whether the stop registered or the mic
    simply opened - so the handover gets its own brief, differently-shaped
    state.
    """

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTING = "interrupting"
    ERROR = "error"

    @property
    def label(self) -> str:
        return _STATE_LABELS[self]


# Colour + icon + wording per state. Red is reserved for "the microphone is
# off" and for errors, following the convention Amazon's AVS and Apple's
# system indicators both use, so a red control always means the same thing.
# The icon differs per state so the signal survives greyscale and colour
# vision deficiency.
_STATE_STYLE = {
    AssistantState.IDLE: ("#6b7280", "audio-input-microphone-symbolic"),
    AssistantState.LISTENING: ("#22c55e", "audio-input-microphone-symbolic"),
    AssistantState.THINKING: ("#f59e0b", "content-loading-symbolic"),
    AssistantState.SPEAKING: ("#3b82f6", "audio-volume-high-symbolic"),
    AssistantState.INTERRUPTING: ("#a855f7", "media-playback-stop-symbolic"),
    AssistantState.ERROR: ("#ef4444", "dialog-error-symbolic"),
}

_STATE_LABELS = {
    AssistantState.IDLE: "Ready",
    AssistantState.LISTENING: "Listening…",
    AssistantState.THINKING: "Thinking…",
    AssistantState.SPEAKING: "Speaking…",
    AssistantState.INTERRUPTING: "Interrupting…",
    AssistantState.ERROR: "Something went wrong",
}

# Kept for the old caller-facing spelling; `thinking` is what it always meant.
_LEGACY_STATE_ALIASES = {"processing": AssistantState.THINKING, "error": AssistantState.ERROR}

# The recorder emits a level every 80ms frame (~12.5 Hz), which is too coarse to
# look like a waveform. Easing toward that target at ~60 Hz is what makes the orb
# appear to follow the voice rather than jump between values.
_TICK_MS = 16
_EASE_FACTOR = 0.25

# The halo starts just outside the 80px orb and grows to fill the 124px button.
_HALO_MIN_PX = 84
_HALO_MAX_PX = 120


def _halo_size(level: float) -> tuple[int, int]:
    """Halo diameter in pixels for a 0.0-1.0 input level.

    Pure so the level-to-size mapping can be asserted without a display.
    """
    span = _HALO_MAX_PX - _HALO_MIN_PX
    px = _HALO_MIN_PX + int(round(span * max(0.0, min(1.0, level))))
    return px, px


class ChronoaOrbWidget(Gtk.Button):
    """The orb - the window's single visual statement of assistant state.

    Remains a real `Gtk.Button` rather than a bare drawing area so it keeps
    keyboard focus, activation and the accessible role for free.
    """

    __gtype_name__ = 'ChronoaOrbWidget'

    def __init__(self) -> None:
        super().__init__()
        self._state = AssistantState.IDLE
        self._icon = Gtk.Image()
        self._level = 0.0
        self._level_target = 0.0
        self._tick_id = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        # Roomier than the 80px orb so the level halo has somewhere to go.
        self.set_size_request(124, 124)
        self.add_css_class("flat")
        self.add_css_class("chronoa-orb")

        # The halo is a plain styled box whose size is driven by the level,
        # not a `Gtk.DrawingArea`. A draw callback needs the cairo foreign
        # struct converter, which only exists once something has imported the
        # `cairo` gi override - and `python3-cairo` is not a declared
        # dependency in either the Arch or the Debian manifest, so on a
        # machine without it every frame logged a TypeError and drew nothing.
        # Sizing a CSS ring needs no extra dependency and cannot fail that way.
        self._halo = Gtk.Box()
        self._halo.add_css_class("chronoa-halo")

        overlay = Gtk.Overlay()
        overlay.set_child(self._halo)
        overlay.add_overlay(self._icon)
        self.set_child(overlay)

        self._apply_state(AssistantState.IDLE)
        self._resize_halo()

    def set_level(self, level: float) -> None:
        """Set the input level, 0.0-1.0.

        The raw value comes from the recorder's own RMS at 12.5 Hz, which is
        too coarse to look like a waveform, so it is treated as a target and
        eased toward on a display-rate tick.
        """
        self._level_target = max(0.0, min(1.0, float(level)))
        if self._tick_id == 0 and self._level != self._level_target:
            self._tick_id = GLib.timeout_add(_TICK_MS, self._tick)

    def get_level(self) -> float:
        return self._level

    def _tick(self) -> bool:
        delta = self._level_target - self._level
        if abs(delta) < 0.01:
            self._level = self._level_target
            self._tick_id = 0
            self._resize_halo()
            return GLib.SOURCE_REMOVE
        self._level += delta * _EASE_FACTOR
        self._resize_halo()
        return GLib.SOURCE_CONTINUE

    def _resize_halo(self) -> None:
        self._halo.set_size_request(*_halo_size(self._level))

    def set_state(self, state: AssistantState) -> None:
        """Move to a state. The only mutator; everything else is derived."""
        if state is self._state:
            return
        self._state = state
        self._apply_state(state)

    def get_state(self) -> AssistantState:
        return self._state

    def _apply_state(self, state: AssistantState) -> None:
        _color, icon_name = _STATE_STYLE[state]
        self._icon.set_from_icon_name(icon_name)
        self._icon.set_pixel_size(32)
        self.set_tooltip_text(state.label)
        for candidate in AssistantState:
            self.remove_css_class(f"state-{candidate.value}")
            self._halo.remove_css_class(f"halo-{candidate.value}")
        self.add_css_class(f"state-{state.value}")
        self._halo.add_css_class(f"halo-{state.value}")


class SuggestionBar(Gtk.FlowBox):
    """Clickable example prompts, shown only while the transcript is empty.

    The empty state is the one moment the app can teach itself, and until this
    existed it taught nothing: a bare "Ask something" label tells a new user
    nothing about a machine that can move their pointer, set a timer, or check
    their battery, and the obvious fix - a hardcoded list of example sentences -
    advertises skills that a given build may not have loaded. The prompts come
    from `capabilities.startup_suggestions()`, which reads the live registry,
    so a skill that is not there is not offered.

    Clicking fills the input rather than submitting. Sending straight away would
    be one click fewer, and would also make the suggestion uneditable and
    unrecoverable if it guessed wrong, which is the wrong trade for a control
    that is only being offered to be tried.
    """

    __gtype_name__ = 'SuggestionBar'

    def __init__(self, suggestions, on_chosen, reduce_motion: bool = False) -> None:
        super().__init__()
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        # Fill, not centre: a FlowBox wraps against the width it is *given*, and
        # a centred one is handed only its single-line natural width - so it
        # never had room to wrap and laid out one chip per row regardless of
        # how wide the window was. Each chip is left at its natural width, so
        # the row still hugs its contents.
        self.set_halign(Gtk.Align.FILL)
        self.set_hexpand(True)
        self.set_homogeneous(False)
        self.set_column_spacing(6)
        self.set_row_spacing(6)
        self.set_margin_top(6)
        self.add_css_class("suggestion-bar")
        self._prompts = list(suggestions)
        if reduce_motion:
            self.add_css_class("reduce-motion")

        for suggestion in suggestions:
            button = Gtk.Button(label=suggestion)
            button.add_css_class("suggestion-chip")
            button.set_tooltip_text(f"Send: {suggestion}")
            button.update_property(
                [Gtk.AccessibleProperty.LABEL], [f"Suggestion: {suggestion}"]
            )
            button.connect("clicked", on_chosen, suggestion)
            self.append(button)

        # The actual cause of one-chip-per-row: `FlowBox.append` wraps every
        # child in a `FlowBoxChild` whose halign is FILL, so the child's minimum
        # width is the whole allocation and nothing can ever share a line. START
        # makes each one shrink-wrap, which is what lets the row wrap.
        for child in self.observe_children():
            child.set_halign(Gtk.Align.START)

        if suggestions:
            self.reveal()

    def reveal(self) -> None:
        """Run the entrance animation.

        The class is removed when the animation ends so that a later
        `reveal()` re-runs it - and so a rebuild does not inherit a
        finished animation's final frame as its starting state.
        """
        if self.get_css_classes().__contains__("reduce-motion"):
            return
        self.add_css_class("entering")
        GLib.timeout_add(400, self._finish_reveal)

    def _finish_reveal(self) -> bool:
        self.remove_css_class("entering")
        return GLib.SOURCE_REMOVE

    def prompts(self) -> list[str]:
        """The suggestions this bar was built with.

        Read from the stored list rather than by walking the widget tree: a
        `Gtk.FlowBox` wraps every appended widget in a `Gtk.FlowBoxChild`, so
        the buttons are grandchildren and no amount of `get_first_child()`
        reaches them. The tree also has nothing authoritative to say about
        order once a child is wrapped.
        """
        return list(self._prompts)


class HelpWindow(Gtk.Window):
    """What Chronoa can do, grouped by intent, with consent state shown.

    Exists because of a specific silence rather than general discoverability:
    the gated skills - the ones that move the pointer, type text, notify, or
    capture the screen - **refuse to run** when their consent key is off, and a
    refusal with no visible reason is indistinguishable from the assistant
    being broken. A user who asks Chronoa to click something gets silence and no
    way to find out why. So every gated row names the switch that governs it and
    says which way that switch is currently set.
    """

    __gtype_name__ = 'HelpWindow'

    def __init__(self, caps, config, on_try=None, parent=None) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="What Shani Chronoa can do",
        )
        self.set_default_size(520, 620)
        self._caps = caps
        self._on_try = on_try

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(14)
        header.set_margin_bottom(6)
        header.set_margin_start(14)
        header.set_margin_end(14)
        title = Gtk.Label(label="What Shani Chronoa can do")
        title.add_css_class("cajita-header")
        title.set_hexpand(True)
        title.set_halign(Gtk.Align.START)
        close = Gtk.Button()
        close.set_icon_name("window-close-symbolic")
        close.add_css_class("flat")
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda _b: self.close())
        header.append(title)
        header.append(close)
        root.append(header)

        summary = Gtk.Label(
            label=f"{len(self._visible())} available"
            f"  ·  {len(self._blocked())} need a setting switched on"
        )
        summary.add_css_class("cajita-detail")
        summary.set_halign(Gtk.Align.START)
        summary.set_margin_start(14)
        summary.set_margin_bottom(8)
        root.append(summary)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self._build_rows(config))
        root.append(scroller)

    def _visible(self) -> list:
        return [c for c in self._caps if not c.consent_key]

    def _blocked(self) -> list:
        return [c for c in self._caps if c.consent_key]

    def _build_rows(self, config) -> Gtk.Box:
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        ordered = [g for g in capabilities.GROUP_ORDER if any(
            c.group == g for c in self._caps
        )]
        for extra in sorted({c.group for c in self._caps} - set(ordered)):
            ordered.append(extra)

        for group in ordered:
            group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            group_box.add_css_class("help-group")
            heading = Gtk.Label(label=group)
            heading.add_css_class("help-group-heading")
            heading.set_halign(Gtk.Align.START)
            group_box.append(heading)
            for capability in [c for c in self._caps if c.group == group]:
                group_box.append(self._build_row(capability, config))
            rows.append(group_box)
        return rows

    def _build_row(self, capability, config) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        row.add_css_class("help-row")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(label=capability.label)
        name.add_css_class("help-row-label")
        name.set_hexpand(True)
        name.set_halign(Gtk.Align.START)
        top.append(name)

        if capability.example and self._on_try is not None:
            try_button = Gtk.Button(label="Try")
            try_button.add_css_class("flat")
            try_button.add_css_class("circular")
            try_button.set_valign(Gtk.Align.CENTER)
            try_button.set_tooltip_text(f"Send: {capability.example}")
            try_button.connect("clicked", self._on_try, capability.example)
            top.append(try_button)

        row.append(top)

        if capability.description:
            detail = Gtk.Label(label=capability.description)
            detail.add_css_class("help-row-detail")
            detail.set_wrap(True)
            detail.set_halign(Gtk.Align.START)
            detail.set_xalign(0.0)
            row.append(detail)

        if capability.consent_key:
            open_now = capability.gate_is_open(config)
            gate = Gtk.Label(label=capability.gate_help(open_now))
            gate.add_css_class("help-row-gate")
            gate.add_css_class("help-row-gate-open" if open_now else "help-row-gate-closed")
            gate.set_wrap(True)
            gate.set_halign(Gtk.Align.START)
            gate.set_xalign(0.0)
            row.append(gate)
        return row


class TranscriptView(Gtk.ScrolledWindow):
    """An append-only list of conversation turns.

    A `ScrolledWindow` around a vertical `Gtk.Box` rather than a single label:
    the point is that turns *accumulate*, so a spoken conversation stays
    readable instead of the window showing only the most recent reply.
    """

    __gtype_name__ = 'TranscriptView'

    def __init__(self, config=None, caps=None, on_suggestion=None,
                 reduce_motion: bool = False) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_propagate_natural_height(True)

        self._config = config
        self._caps = caps or []
        self._reduce_motion = reduce_motion
        self._on_suggestion = on_suggestion
        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._rows.set_margin_top(10)
        self._rows.set_margin_bottom(10)
        self._rows.set_margin_start(10)
        self._rows.set_margin_end(10)
        self.set_child(self._rows)

        self._current_assistant: Gtk.Label | None = None
        self.show_placeholder()

    def set_suggestion_handler(self, handler) -> None:
        self._on_suggestion = handler
        if self._is_placeholder():
            self.show_placeholder()

    def show_placeholder(self) -> None:
        """The empty state: a prompt *and* something to press.

        Deliberately not blank, and deliberately not just words. A single
        "ask something" line leaves a new user with nothing to act on, so the
        suggestions are part of the empty state rather than a separate panel
        that has to be discovered and dismissed.
        """
        self.clear()
        label = Gtk.Label(label="Ask something, or press the orb to speak.")
        label.add_css_class("transcript-placeholder")
        label.set_wrap(True)
        label.set_halign(Gtk.Align.CENTER)
        self._rows.append(label)

        suggestions = capabilities.startup_suggestions(self._caps)
        if suggestions and self._on_suggestion is not None:
            bar = SuggestionBar(
                suggestions, self._on_suggestion, self._reduce_motion
            )
            bar.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ["Suggested questions you can send"],
            )
            self._rows.append(bar)

    def clear(self) -> None:
        child = self._rows.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._rows.remove(child)
            child = following
        self._current_assistant = None

    def _append_turn(self, role: str, text: str) -> Gtk.Label:
        """Append one turn, styled by role, and return its label."""
        if self._is_placeholder():
            self.clear()
        label = Gtk.Label()
        label.set_wrap(True)
        # A reply arrives with light markdown in it, and showing that verbatim
        # leaves the user reading `**38G**` and `- / is 32% full` - the most
        # visibly unfinished thing about a chat window. Only the assistant's
        # text is rendered: a user's own message is what they typed, and
        # re-rendering it would change what they see into what they did not
        # write. Selectable either way, so the text is still reachable without
        # the copy button.
        label.set_selectable(True)
        if role == "user":
            label.set_text(text)
        else:
            label.set_markup(markdown_lite.to_pango(text))
        label.set_xalign(1.0 if role == "user" else 0.0)
        label.add_css_class("transcript-turn")
        label.add_css_class(f"transcript-{role}")
        # A screen reader should announce a finished turn, not every word of
        # a partial one, so the role and label are set explicitly.
        label.update_property([Gtk.AccessibleProperty.LABEL], [f"{role} said: {text}"])
        if role == "user":
            self._rows.append(label)
        else:
            self._rows.append(self._with_copy_button(label, text))
        self._scroll_to_end()
        return label

    def _with_copy_button(self, label: Gtk.Label, text: str) -> Gtk.Box:
        """An assistant turn with a copy button beside it.

        The reply is the only thing in the window worth keeping - a command, a
        path, a sentence to paste somewhere - and selecting wrapped text by
        dragging across a bubble is the wrong gesture for it. The button is
        flat and dim until hovered or focused, so a transcript of twenty turns
        is not twenty competing buttons.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_hexpand(True)
        label.set_hexpand(True)
        row.append(label)

        button = Gtk.Button()
        button.set_icon_name("edit-copy-symbolic")
        button.add_css_class("flat")
        button.add_css_class("circular")
        button.set_valign(Gtk.Align.START)
        button.set_tooltip_text("Copy this reply")
        button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Copy this reply"]
        )
        button.connect("clicked", self._on_copy_clicked, text)
        row.append(button)
        return row

    @staticmethod
    def _on_copy_clicked(button: Gtk.Button, text: str) -> None:
        """Copy, and say so.

        A copy button that gives no feedback cannot be distinguished from one
        that did not work, so the icon confirms and reverts. `set_content` is
        the current clipboard API; `set` is the pre-4.10 one, still needed
        because the two disagree about which is available.
        """
        clipboard = button.get_clipboard()
        try:
            clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
        except AttributeError:
            clipboard.set(text)
        button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(1200, lambda: (button.set_icon_name("edit-copy-symbolic"),
                                        GLib.SOURCE_REMOVE)[1])

    def _is_placeholder(self) -> bool:
        first = self._rows.get_first_child()
        if first is None:
            return True
        return first.get_css_classes().__contains__("transcript-placeholder")

    def add_user_turn(self, text: str) -> None:
        self._current_assistant = None
        self._append_turn("user", text)

    def add_assistant_turn(self, text: str) -> None:
        """Add or update the assistant's turn.

        Updating rather than appending matters: the assistant's reply is
        written once when it is known and replaced when a tool call produces a
        better version, and a voice turn that appended twice would read as
        the assistant having answered twice.
        """
        if self._current_assistant is not None:
            self._current_assistant.set_markup(markdown_lite.to_pango(text))
            self._scroll_to_end()
            return
        self._current_assistant = self._append_turn("assistant", text)

    def _scroll_to_end(self) -> None:
        """Scroll to the newest turn, after layout has been computed.

        Deferred because the adjustment's upper bound is only meaningful once
        the box has been allocated its natural height, which has not happened
        during the call that appended the row.
        """
        def _apply() -> bool:
            adj = self.get_vadjustment()
            if adj is not None:
                adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_apply)


class CajitaWindow(Gtk.ApplicationWindow):
    """The main window: state surface, transcript, and input."""

    def __init__(self, application: Gtk.Application, config=None) -> None:
        super().__init__(application=application)
        self._state = AssistantState.IDLE
        self._config = config if config is not None else self._default_config()
        self._caps = self._load_capabilities()
        self._help_window: HelpWindow | None = None
        self._setup_window()
        self._setup_ui()
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

    def _setup_window(self) -> None:
        self.set_title("Shani Chronoa")
        self.set_default_size(460, 640)
        self.set_size_request(380, 480)
        self.set_decorated(True)
        self._apply_css()
        self._apply_motion_preference()

    def _apply_css(self) -> None:
        """Layout, shape and the orb's state palette - but no theme colours.

        This used to hardcode a dark window (`background-color: #14141f`) and
        hand-pick every foreground to match, so a user on a light GTK theme got
        a dark dialog with white borders that were invisible against it. The
        theme is the user's, and it already knows the answer: every colour here
        that describes *chrome* is now a named palette entry
        (`@theme_fg_color`, `@theme_base_color`, `alpha(...)`), which follows
        light, dark and high-contrast without this file knowing they exist.

        What stays hardcoded is the orb's state palette, and that is deliberate:
        green/amber/blue/violet/red are not decoration, they are how the
        assistant says what it is doing. A theme cannot supply "thinking", and
        diluting them into theme greys would cost the one signal the window
        depends on. The state is also carried by the icon and the state text, so
        colour is reinforcement rather than the only channel.
        """
        css_data = b"""
        /* The window takes the desktop's background. Naming a colour here at
           all is what made a light theme unusable. */
        .cajita-header {
            color: @theme_fg_color;
            font-size: 17px;
            font-weight: 600;
        }
        .cajita-state {
            color: @theme_fg_color;
            font-size: 14px;
            font-weight: 600;
        }
        .cajita-detail {
            color: alpha(@theme_fg_color, 0.65);
            font-size: 12px;
        }
        .transcript-turn {
            font-size: 14px;
            color: @theme_fg_color;
        }
        /* Bubbles are a tint of the foreground rather than a fixed dark grey,
           so they read as raised on a light theme and on a dark one without a
           second rule. */
        .transcript-user {
            color: @theme_fg_color;
            background-color: alpha(@theme_fg_color, 0.08);
            border-radius: 10px;
            padding: 8px 12px;
        }
        .transcript-assistant {
            color: @theme_fg_color;
            background-color: alpha(@theme_fg_color, 0.14);
            border-radius: 10px;
            padding: 8px 12px;
        }
        .transcript-placeholder {
            color: alpha(@theme_fg_color, 0.5);
            font-size: 13px;
        }

        /* Per-state orb appearance. Idle is deliberately flat and drawn from
           the theme, since "nothing is happening" is not a colour anyone
           picked. Every active state is a real signal, so those stay fixed.
           All active states glow; idle is the only one with no glow, so a
           single-frame transition still reads as active. */
        .chronoa-orb {
            background-color: alpha(@theme_fg_color, 0.45);
            border-radius: 50%;
            min-width: 80px;
            min-height: 80px;
            box-shadow: none;
            transition: background-color 0.25s ease, box-shadow 0.25s ease;
        }
        .chronoa-orb:hover { box-shadow: 0 0 12px alpha(@theme_fg_color, 0.18); }
        .chronoa-orb:focus-visible {
            box-shadow: 0 0 0 3px alpha(@accent_bg_color, 0.9);
        }
        .chronoa-orb.state-listening {
            background-color: #22c55e;
            box-shadow: 0 0 22px rgba(34,197,94,0.55);
            animation: chronoa-listen 1.4s ease-in-out infinite;
        }
        .chronoa-orb.state-thinking {
            background-color: #f59e0b;
            box-shadow: 0 0 18px rgba(245,158,11,0.5);
        }
        .chronoa-orb.state-speaking {
            background-color: #3b82f6;
            box-shadow: 0 0 22px rgba(59,130,246,0.55);
        }
        .chronoa-orb.state-interrupting {
            background-color: #a855f7;
            box-shadow: 0 0 20px rgba(168,85,247,0.6);
        }
        .chronoa-orb.state-error {
            background-color: #ef4444;
            box-shadow: 0 0 20px rgba(239,68,68,0.55);
        }
        @keyframes chronoa-listen {
            0%   { box-shadow: 0 0 14px rgba(34,197,94,0.35); }
            50%  { box-shadow: 0 0 30px rgba(34,197,94,0.75); }
            100% { box-shadow: 0 0 14px rgba(34,197,94,0.35); }
        }

        /* The level halo. Its size comes from the recorder's RMS, so all CSS
           has to supply is the ring; the border colour tracks the orb's state
           so the two never disagree. The idle ring is drawn from the theme,
           because white-on-white is invisible and this used to be exactly
           that on a light theme. */
        .chronoa-halo {
            border-radius: 50%;
            border: 3px solid alpha(@theme_fg_color, 0.28);
            background-color: transparent;
        }
        .chronoa-halo.halo-idle { border-color: alpha(@theme_fg_color, 0.18); }
        .chronoa-halo.halo-listening { border-color: rgba(34,197,94,0.65); }
        .chronoa-halo.halo-thinking { border-color: rgba(245,158,11,0.6); }
        .chronoa-halo.halo-speaking { border-color: rgba(59,130,246,0.6); }
        .chronoa-halo.halo-interrupting { border-color: rgba(168,85,247,0.7); }
        .chronoa-halo.halo-error { border-color: rgba(239,68,68,0.6); }

        /* Reduce-motion also stops the halo easing, since a ring that keeps
           changing size is motion too. */
        .reduce-motion .chronoa-halo { border-width: 2px; }

        /* Honour the desktop's reduce-motion setting by dropping the pulse
           while keeping the colour and icon that carry the same meaning. */
        .reduce-motion .chronoa-orb.state-listening {
            animation: none;
            box-shadow: 0 0 20px rgba(34,197,94,0.6);
        }
        .cajita-input {
            color: @theme_fg_color;
            font-size: 14px;
            padding: 8px 10px;
            background-color: @theme_base_color;
            border-radius: 8px;
            border: 1px solid alpha(@theme_fg_color, 0.2);
        }
        .cajita-input:focus {
            border-color: @accent_color;
        }
        .mic-off { color: #ef4444; }

        /* Suggestion chips. Outlined rather than filled so a screen full of
           them does not compete with the transcript for attention, and so the
           default-action styling stays reserved for Send. */
        .suggestion-chip {
            color: @theme_fg_color;
            font-size: 13px;
            padding: 6px 12px;
            border-radius: 15px;
            border: 1px solid alpha(@theme_fg_color, 0.25);
            background-color: alpha(@theme_fg_color, 0.04);
            transition: background-color 0.18s ease,
                        border-color 0.18s ease,
                        color 0.18s ease;
        }
        .suggestion-chip:hover {
            background-color: alpha(@accent_color, 0.16);
            border-color: alpha(@accent_color, 0.55);
        }
        .suggestion-chip:focus-visible {
            border-color: @accent_color;
        }

        /* Entrance for the chip row. Staggering is not expressible in GTK CSS,
           so the whole row fades together - a row that appeared one chip at a
           time would read as content still loading. */
        .suggestion-bar.entering {
            animation: chronoa-reveal 0.32s ease-out;
        }
        @keyframes chronoa-reveal {
            from { opacity: 0; }
            to   { opacity: 1; }
        }

        /* A new turn arriving, so the transcript does not jump. */
        .transcript-turn {
            animation: chronoa-turn-in 0.22s ease-out;
        }
        @keyframes chronoa-turn-in {
            from { opacity: 0; }
            to   { opacity: 1; }
        }

        /* Reduce-motion drops the entrances but keeps every colour, border and
           state cue - the animations here are decoration on top of a layout
           that is already complete, so removing them loses no information. */
        .reduce-motion .suggestion-bar.entering,
        .reduce-motion .suggestion-bar,
        .reduce-motion .transcript-turn {
            animation: none;
        }
        .reduce-motion .suggestion-chip {
            transition: none;
        }

        .help-group-heading {
            color: @theme_fg_color;
            font-size: 13px;
            font-weight: 700;
            padding: 14px 14px 4px 14px;
        }
        .help-row {
            padding: 6px 14px;
            border-bottom: 1px solid alpha(@theme_fg_color, 0.07);
        }
        .help-row-label {
            color: @theme_fg_color;
            font-size: 14px;
            font-weight: 600;
        }
        .help-row-detail {
            color: alpha(@theme_fg_color, 0.72);
            font-size: 12px;
        }
        /* The gate line is the whole reason this window exists, so it is the
           only coloured text in it: green when the skill is usable, amber when
           it will silently do nothing. */
        .help-row-gate {
            font-size: 12px;
            padding-top: 2px;
        }
        .help-row-gate-open { color: rgba(34,197,94,0.9); }
        .help-row-gate-closed { color: rgba(245,158,11,0.95); }
        """
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css_data)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _apply_motion_preference(self) -> None:
        """Respect the desktop reduce-motion preference.

        Checked once at construction. This is about the assistant announcing
        itself without movement, so it is scoped to the window's own animations
        rather than suppressing every animation in the app: the orb's
        listening pulse and the level halo are how a voice app shows it is
        *hearing* you, and a still orb during recording reads as a dead control
        rather than a considerate one.
        """
        settings = Gtk.Settings.get_default()
        if settings is None:
            return
        if not settings.get_property("gtk-enable-animations"):
            self.add_css_class("reduce-motion")

    def _motion_is_reduced(self) -> bool:
        return self.get_css_classes().__contains__("reduce-motion")

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
        self.set_child(main_box)

        # Header: title, mic state, settings. The mic indicator is here rather
        # than only in settings because "is this thing listening to me" has to
        # be answerable at a glance, from outside the window too.
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header = Gtk.Label(label="Shani Chronoa")
        header.add_css_class("cajita-header")
        header.set_hexpand(True)
        header.set_halign(Gtk.Align.START)

        self._mic_icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        self._mic_icon.set_pixel_size(16)
        self._mic_icon.set_tooltip_text("Microphone is in use")
        self._mic_icon.add_css_class("flat")

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

        header_row.append(header)
        header_row.append(self._mic_icon)
        header_row.append(help_button)
        header_row.append(settings_button)
        main_box.append(header_row)

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
        )
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

        input_row.append(self._input_entry)
        input_row.append(self._stop_button)
        input_row.append(self._send_button)
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
        self._stop_button.set_visible(self._state is AssistantState.SPEAKING)

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

    def add_user_turn(self, text: str) -> None:
        """Show what the user said, so a spoken turn is visible too."""
        self._transcript.add_user_turn(text)

    def clear_transcript(self) -> None:
        self._transcript.show_placeholder()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

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
        self.add_user_turn(text)
        # The stripped text, not the raw entry contents: the turn the user can
        # see is the stripped one, and emitting the raw string meant the
        # assistant received something the transcript never showed.
        self.emit("user-input", text)

    def show_question(self, question: str, options: list, resolve) -> None:
        """Put a question with its options on screen, resolved by `resolve`.

        `resolve` is passed in rather than returned because the caller is
        off-thread: it has to hold the event *now* and queue the widget work for
        the GTK thread, so a method that returned the event would be read before
        GLib had run it.
        """
        if self._pending_question is not None:
            self._resolve_question("")

        self._clear_question_widgets()

        label = Gtk.Label(label=question)
        label.set_wrap(True)
        label.set_xalign(0.0)
        self._question_widgets.append(label)
        self._question_row.append(label)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for option in options:
            btn = Gtk.Button(label=option)
            btn.set_hexpand(True)
            btn.connect("clicked", lambda _b, o=option: self._resolve_question(o))
            buttons.append(btn)
        self._question_widgets.append(buttons)
        self._question_row.append(buttons)

        hint = Gtk.Label(label="Tap an option, or just say your answer.")
        hint.add_css_class("dim-label")
        hint.set_xalign(0.0)
        self._question_widgets.append(hint)
        self._question_row.append(hint)

        self._question_row.set_visible(True)
        self._input_entry.set_placeholder_text("Say or type your answer…")
        self._pending_question = resolve
        self._pending_options = list(options)

    def has_pending_question(self) -> bool:
        return self._pending_question is not None

    def answer_pending_question(self, spoken: str) -> bool:
        """Answer the question on screen with what was SAID; False if none is open.

        The hint promises "just say your answer", but a voice transcript used to
        open a new turn while the tool sat blocked waiting for this one. And it
        must be mapped to an option: permissions compares the answer exactly,
        and whisper writes "Allow this once." - full stop - which would have
        been read as a refusal.
        """
        if self._pending_question is None:
            return False
        self._resolve_question(match_spoken_option(spoken, getattr(self, "_pending_options", [])))
        return True

    def abandon_pending_question(self) -> None:
        """Resolve a prompt on screen as unanswered, and touch nothing else.

        This is the dismissal path for the one case where the user never gets to
        answer: the window is going away with the question still up. Leaving the
        event unset holds the tool loop for the whole decision timeout
        (`permissions.DECISION_TIMEOUT_SECONDS`) after the user has already quit,
        which is how a prompt turns into a hung exit.

        Deliberately resolves the event and stops there. `_resolve_question`
        would also tear the widgets down, un-hide the input row and steal focus -
        all correct while the window lives, all wrong here, because the window is
        mid-destruction and the widgets are about to die with it anyway.
        """
        resolve = self._pending_question
        if resolve is None:
            return
        self._pending_question = None
        resolve("")

    def _clear_question_widgets(self) -> None:
        """Drop the prompt's widgets.

        GTK4's `Gtk.Box` has no `get_children()` - that is GTK3 - so the widgets
        are tracked as they are added and removed by reference.
        """
        for widget in self._question_widgets:
            self._question_row.remove(widget)
        self._question_widgets = []

    def _resolve_question(self, answer: str) -> None:
        resolve = self._pending_question
        self._pending_question = None
        if resolve is not None:
            resolve(answer)
        self._clear_question_widgets()
        self._question_row.set_visible(False)
        self._input_entry.set_placeholder_text("Type a message…")
        self._input_entry.grab_focus()

    def get_input_text(self) -> str:
        return self._input_entry.get_text()

    def clear_input(self) -> None:
        self._input_entry.set_text("")

    __gsignals__ = {
        "user-input": (GObject.SignalFlags.RUN_FIRST, str, (str,)),
    }


def _norm_words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", text.lower().replace("\u2019", "'"))


def match_spoken_option(spoken: str, options: list) -> str:
    """The option a spoken answer names, else the words as said.

    Strict on purpose, because some options grant permissions: the answer
    must equal an option or begin with ALL of it ("allow this once, please"),
    after case and punctuation are dropped, and exactly one option may match.
    Anything else is passed through as said, which a permission prompt reads
    as no - the same as an unrecognised typed answer.
    """
    said = _norm_words(spoken)
    hits = []
    for option in options:
        want = _norm_words(option)
        if want and said[: len(want)] == want:
            hits.append(option)
    # "allow this once" must not also count as a shorter option it begins with
    if len(hits) > 1:
        longest = max(len(_norm_words(h)) for h in hits)
        hits = [h for h in hits if len(_norm_words(h)) == longest]
    return hits[0] if len(hits) == 1 else spoken.strip()


def make_question_presenter(window_getter):
    """Build the `ask_bridge` presenter that asks on the main window.

    This is the missing half of the permission-prompt path. `permissions.decide()`
    and the `ask_user` skill both call `ask_bridge.ask()`, and `ask_bridge` is
    only ever useful once something installs a presenter - `has_presenter()` is
    literally `_presenter is not None`, so with nothing installed every gated
    tool silently refuses and the questions are never asked. Nothing in the
    application did install one, which is the whole reason both features were
    dead in the running app.

    The threading is the whole difficulty and it is not negotiable:
    `ask()` is called from the assistant's tool loop, which runs on
    `AsyncBridge`'s background thread, and constructing or touching a widget off
    the GTK thread is undefined behaviour. So this presenter does only the two
    things that are safe off-thread - it makes the event, and it queues the
    widget work with `GLib.idle_add`, which GLib runs on the main loop whatever
    thread queued it. The calling thread blocks on that event; the GTK side sets
    it. One hand-off, one direction, and the answer lives on the event object
    because that is the only place `ask_bridge` reads it from
    (`make_event()` returns the pair precisely so no implementation invents its
    own stash).

    `window_getter` is a callable, not a window, because the window does not
    exist yet when the presenter is installed: `do_startup` runs before
    `do_activate` builds it. It is resolved per question instead. A `None` window
    means nobody is present, and is answered immediately with `""` rather than
    held for the full timeout on an answer that cannot arrive.

    **Capacity is one.** `ask_bridge` admits four prompts at once; this window
    has a single question row, and a second prompt supersedes the first -
    resolving it as no answer - rather than queueing behind it. That is the same
    call `ask_bridge` makes at its own limit (refuse, do not queue), it fails in
    the safe direction, and in the shipped app both prompts originate on the one
    tool loop thread, so they are sequential in any case.
    """
    from shani_chronoa import ask_bridge

    def _show(window, question, options, resolve) -> bool:
        """Runs on the GTK thread: build the prompt, or fail honestly."""
        try:
            window.show_question(question, options, resolve)
        except Exception as exc:  # noqa: BLE001 - a prompt that cannot appear is not a choice
            logger.warning("question could not be put on screen: %s", exc)
            resolve("")
        return GLib.SOURCE_REMOVE

    def present(question: str, options: list) -> "threading.Event":
        """Queue `question` for the window and hand back the event to wait on."""
        done, resolve = ask_bridge.make_event()
        window = window_getter()
        if window is None:
            # No window is not a question anyone can answer, and "nobody
            # answered" is what "" already means everywhere else in this module.
            # Resolving now is what keeps a headless-ish call from sitting on a
            # 120-second wait for a prompt that will never appear.
            resolve("")
            return done
        # `list(options)`: ask_bridge already copies, and show_question reads the
        # list on the GTK thread long after this call has returned.
        GLib.idle_add(_show, window, question, list(options), resolve)
        return done

    return present
