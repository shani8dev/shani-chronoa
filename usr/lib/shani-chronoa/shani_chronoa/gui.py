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

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk, Gdk, GLib, GObject  # type: ignore

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
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.set_size_request(80, 80)
        self.add_css_class("flat")
        self.add_css_class("chronoa-orb")
        self.set_child(self._icon)
        self._apply_state(AssistantState.IDLE)

    def set_state(self, state: AssistantState) -> None:
        """Move to a state. The only mutator; everything else is derived."""
        if state is self._state:
            return
        self._state = state
        self._apply_state(state)

    def get_state(self) -> AssistantState:
        return self._state

    def _apply_state(self, state: AssistantState) -> None:
        color, icon_name = _STATE_STYLE[state]
        self._icon.set_from_icon_name(icon_name)
        self._icon.set_pixel_size(32)
        self.set_tooltip_text(state.label)
        for candidate in AssistantState:
            self.remove_css_class(f"state-{candidate.value}")
        self.add_css_class(f"state-{state.value}")
        # Idle is the only state with no glow, so every active state gains
        # contrast against it rather than merely against the previous frame.
        self.set_name(f"orb-{color.lstrip('#')}")


class TranscriptView(Gtk.ScrolledWindow):
    """An append-only list of conversation turns.

    A `ScrolledWindow` around a vertical `Gtk.Box` rather than a single label:
    the point is that turns *accumulate*, so a spoken conversation stays
    readable instead of the window showing only the most recent reply.
    """

    __gtype_name__ = 'TranscriptView'

    def __init__(self) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_propagate_natural_height(True)

        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._rows.set_margin_top(10)
        self._rows.set_margin_bottom(10)
        self._rows.set_margin_start(10)
        self._rows.set_margin_end(10)
        self.set_child(self._rows)

        self._current_assistant: Gtk.Label | None = None
        self.show_placeholder()

    def show_placeholder(self) -> None:
        """The empty state.

        Deliberately not blank: an empty box reads as a broken window, so the
        empty state names what to do instead.
        """
        self.clear()
        label = Gtk.Label(label="Ask something, or press the orb to speak.")
        label.add_css_class("transcript-placeholder")
        label.set_wrap(True)
        label.set_halign(Gtk.Align.CENTER)
        self._rows.append(label)

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
        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_xalign(1.0 if role == "user" else 0.0)
        label.add_css_class("transcript-turn")
        label.add_css_class(f"transcript-{role}")
        # A screen reader should announce a finished turn, not every word of
        # a partial one, so the role and label are set explicitly.
        label.update_property([Gtk.AccessibleProperty.LABEL], [f"{role} said"])
        self._rows.append(label)
        self._scroll_to_end()
        return label

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
            self._current_assistant.set_label(text)
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

    def __init__(self, application: Gtk.Application) -> None:
        super().__init__(application=application)
        self._state = AssistantState.IDLE
        self._setup_window()
        self._setup_ui()
        self._sync_from_state()

    def _setup_window(self) -> None:
        self.set_title("Shani Chronoa")
        self.set_default_size(460, 640)
        self.set_size_request(380, 480)
        self.set_decorated(True)
        self._apply_css()
        self._apply_motion_preference()

    def _apply_css(self) -> None:
        css_data = b"""
        window.cajita-window {
            background-color: #14141f;
        }
        .cajita-header {
            color: #e6e6ef;
            font-size: 17px;
            font-weight: 600;
        }
        .cajita-state {
            color: #e6e6ef;
            font-size: 14px;
            font-weight: 600;
        }
        .cajita-detail {
            color: #9aa0b4;
            font-size: 12px;
        }
        .transcript-turn {
            font-size: 14px;
        }
        .transcript-user {
            color: #9aa0b4;
            background-color: #1e1e30;
            border-radius: 10px;
            padding: 8px 12px;
        }
        .transcript-assistant {
            color: #e6e6ef;
            background-color: #23233a;
            border-radius: 10px;
            padding: 8px 12px;
        }
        .transcript-placeholder {
            color: #6b7280;
            font-size: 13px;
        }

        /* Per-state orb appearance. Idle is deliberately flat: the only
           state with no glow, so every active state reads as active even
           when the transition is a single frame. */
        .chronoa-orb {
            background-color: #6b7280;
            border-radius: 50%;
            min-width: 80px;
            min-height: 80px;
            box-shadow: none;
            transition: background-color 0.25s ease, box-shadow 0.25s ease;
        }
        .chronoa-orb:hover { box-shadow: 0 0 12px rgba(255,255,255,0.18); }
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

        /* Honour the desktop's reduce-motion setting by dropping the pulse
           while keeping the colour and icon that carry the same meaning. */
        .reduce-motion .chronoa-orb.state-listening {
            animation: none;
            box-shadow: 0 0 20px rgba(34,197,94,0.6);
        }
        .cajita-input {
            color: #f2f2f7;
            font-size: 14px;
            padding: 8px 10px;
            background-color: #1e1e30;
            border-radius: 8px;
            border: 1px solid #34344d;
        }
        .mic-off { color: #ef4444; }
        """
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css_data)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _apply_motion_preference(self) -> None:
        """Respect the desktop reduce-motion preference.

        Checked once at construction. This is about the assistant announcing
        itself without movement, not about suppressing every animation in the
        app, so it is scoped to the orb's pulse.
        """
        settings = Gtk.Settings.get_default()
        if settings is None:
            return
        if not settings.get_property("gtk-enable-animations"):
            self.add_css_class("reduce-motion")

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

        settings_button = Gtk.Button()
        settings_button.set_icon_name("emblem-system-symbolic")
        settings_button.add_css_class("flat")
        settings_button.set_valign(Gtk.Align.CENTER)
        settings_button.set_tooltip_text("Settings")
        settings_button.set_action_name("app.open-settings")

        header_row.append(header)
        header_row.append(self._mic_icon)
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

        self._transcript = TranscriptView()
        main_box.append(self._transcript)

        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._input_entry = Gtk.Entry()
        self._input_entry.set_placeholder_text("Type a message…")
        self._input_entry.add_css_class("cajita-input")
        self._input_entry.set_hexpand(True)
        self._input_entry.connect("activate", self._on_input_activate)

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
        main_box.append(input_row)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def set_state(self, state: AssistantState) -> None:
        """Set the assistant state. The single entry point for it."""
        self._state = state
        self._sync_from_state()

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
        text = entry.get_text()
        if text.strip():
            entry.set_text("")
            self.add_user_turn(text.strip())
            self.emit("user-input", text)

    def get_input_text(self) -> str:
        return self._input_entry.get_text()

    def clear_input(self) -> None:
        self._input_entry.set_text("")

    __gsignals__ = {
        "user-input": (GObject.SignalFlags.RUN_FIRST, str, (str,)),
    }
