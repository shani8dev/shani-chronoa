"""The window's building blocks: the orb, the transcript, the suggestion bar and the help window - and the assistant states they show."""

import enum

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk, Gdk, GLib, Pango

from shani_chronoa import capabilities

from . import blocks as reply_blocks


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
    #: "I heard you, and I am waiting for the model to finish before I
    #: transcribe." `assistd` carries the same state as `VoiceCaptureState::
    #: Queued` - "waiting for the GPU to free up before transcribing" - and
    #: having built the gate that produces it, the only thing missing was for
    #: anyone to be able to *see* it. Without this the wait is a pause in the
    #: orb between listening and thinking, which is indistinguishable from the
    #: microphone having stopped working.
    QUEUED = "queued"
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
    # Teal, not listening's green: `tests/test_window_ux.py` requires that no
    # two states share a colour, because colour alone excludes colourblind users
    # - and that invariant outranks the "it is still listening" reading. Teal is
    # the nearest distinct hue that still sits between listening (green) and
    # thinking (amber), which is where this state actually sits.
    AssistantState.QUEUED: ("#14b8a6", "content-loading-symbolic"),
    AssistantState.THINKING: ("#f59e0b", "content-loading-symbolic"),
    AssistantState.SPEAKING: ("#3b82f6", "audio-volume-high-symbolic"),
    AssistantState.INTERRUPTING: ("#a855f7", "media-playback-stop-symbolic"),
    AssistantState.ERROR: ("#ef4444", "dialog-error-symbolic"),
}

_STATE_LABELS = {
    AssistantState.IDLE: "Ready",
    AssistantState.LISTENING: "Listening…",
    AssistantState.QUEUED: "Heard you — waiting for the model…",
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

#: Widest the conversation and the composer get, in px. A maximised window put
#: a reply in a ~950px line and the composer the same width - past the length
#: a line can be read at. Wider than any window the narrow layout serves, so the
#: clamp only ever bites on wide ones.
READING_WIDTH_PX = 860


def reading_width(child: Gtk.Widget) -> Gtk.Widget:
    """`child` capped at `READING_WIDTH_PX` and centred, when libadwaita is here.

    Without it the child is returned as it was; the window is usable either
    way, only less comfortable on a very wide screen.
    """
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
    except (ImportError, ValueError):
        return child
    clamp = Adw.Clamp(maximum_size=READING_WIDTH_PX, tightening_threshold=READING_WIDTH_PX)
    clamp.set_child(child)
    return clamp


#: Marks a housekeeping notice inside a turn, so a new reply text keeps it.
TURN_NOTICE_CSS = "turn-notice"


def _keeps_across_replies(child) -> bool:
    """A turn's child that is about the turn rather than the reply's text."""
    return (isinstance(child, reply_blocks.ToolCallCard)
            or TURN_NOTICE_CSS in child.get_css_classes())


#: Widest a user's own message bubble wraps at, in characters.
USER_TURN_MAX_CHARS = 56
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
            button = Gtk.Button()
            # A label inside the button rather than on it, so it can ellipsise.
            # `Gtk.Button(label=...)` has no ellipsize: a long suggestion made
            # one chip 300px+ wide with nothing to shrink it, and the row's own
            # wrapping cannot help a single chip that is wider than the window.
            # Thirty of these fit; this one did not.
            chip_label = Gtk.Label(label=suggestion, ellipsize=Pango.EllipsizeMode.END,
                                   xalign=0)
            button.set_child(chip_label)
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

        # No title or close button of our own: the window's title bar already
        # carries both, and rendered, the window showed "What Shani Chronoa can
        # do" twice and two close buttons one above the other. That space holds
        # a search instead, because the list is over a hundred rows long and
        # scrolling it for "timer" was the only way to find the timer.
        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Find something Chronoa can do")
        self._search.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Find something Chronoa can do"])
        self._search.set_margin_top(12)
        self._search.set_margin_start(14)
        self._search.set_margin_end(14)
        self._search.connect("search-changed", lambda e: self.filter(e.get_text()))
        root.append(self._search)

        summary = Gtk.Label(
            label=f"{len(self._visible())} available"
            f"  ·  {len(self._blocked())} need a setting switched on"
        )
        summary.add_css_class("cajita-detail")
        summary.set_halign(Gtk.Align.START)
        summary.set_margin_start(14)
        summary.set_margin_top(6)
        summary.set_margin_bottom(8)
        root.append(summary)
        self._summary = summary

        #: (group box, [(row, haystack)]) for `filter()`.
        self._index: list = []
        self._empty = Gtk.Label(label="Nothing matches. Try a shorter word.")
        self._empty.add_css_class("dim-label")
        self._empty.set_margin_top(24)
        self._empty.set_visible(False)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body = self._build_rows(config)
        body.append(self._empty)
        scroller.set_child(body)
        root.append(scroller)

    def filter(self, needle: str) -> int:
        """Show only the rows whose name, description or switch mentions `needle`.

        Returns how many rows are left. A group with nothing left is hidden
        with its heading, so a search never shows a heading over nothing.
        """
        words = (needle or "").lower().split()
        shown = 0
        for group_box, rows in self._index:
            any_left = False
            for row, haystack in rows:
                hit = all(w in haystack for w in words)
                row.set_visible(hit)
                any_left = any_left or hit
                shown += hit
            group_box.set_visible(any_left)
        self._empty.set_visible(shown == 0)
        return shown

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

        rows.append(self._build_tasks(config))
        everything = Gtk.Label(label="Everything Chronoa can do")
        everything.add_css_class("title-4")
        everything.set_halign(Gtk.Align.START)
        everything.set_margin_start(14)
        everything.set_margin_top(18)
        rows.append(everything)

        for group in ordered:
            group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            group_box.add_css_class("help-group")
            heading = Gtk.Label(label=group)
            heading.add_css_class("help-group-heading")
            heading.set_halign(Gtk.Align.START)
            group_box.append(heading)
            indexed = []
            for capability in [c for c in self._caps if c.group == group]:
                row = self._build_row(capability, config)
                group_box.append(row)
                indexed.append((row, " ".join(filter(None, (
                    capability.label, capability.description, group,
                    capability.example or "",
                    capabilities.GATE_NAMES.get(capability.consent_key or "", ""),
                ))).lower()))
            self._index.append((group_box, indexed))
            rows.append(group_box)
        return rows

    def _build_tasks(self, config) -> Gtk.Box:
        """The jobs first: how Chronoa helps with a day, each one ready to send.

        Above the full list, because "what can you do?" is asked by someone with
        something to get done, and a list of tool names does not answer it.
        Searchable with the rest; a card says which switches its job still needs.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("help-group")
        box.set_margin_start(14)
        box.set_margin_end(14)
        heading = Gtk.Label(label="Get things done")
        heading.add_css_class("title-4")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)
        intro = Gtk.Label(label="Say what you want done, the way you would ask a person. "
                                "Chronoa works out the steps, asks before anything that "
                                "pays or deletes, and tells you what it did.")
        intro.add_css_class("dim-label")
        intro.set_wrap(True)
        intro.set_xalign(0.0)
        box.append(intro)
        indexed = []
        for task in capabilities.EVERYDAY_TASKS:
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("card")
            card.add_css_class("help-task")
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            text.set_hexpand(True)
            for side in ("start", "top", "bottom"):
                getattr(text, f"set_margin_{side}")(10)
            title = Gtk.Label(label=task.title, xalign=0.0)
            title.add_css_class("heading")
            text.append(title)
            how = Gtk.Label(label=task.how, xalign=0.0, wrap=True)
            text.append(how)
            said = Gtk.Label(label=f"\u201c{task.prompt}\u201d", xalign=0.0, wrap=True)
            said.add_css_class("dim-label")
            said.add_css_class("caption")
            text.append(said)
            needs = capabilities.task_needs(task, config)
            if needs:
                gate = Gtk.Label(label="Needs switched on: " + "; ".join(needs), xalign=0.0, wrap=True)
                gate.add_css_class("help-row-gate")
                gate.add_css_class("help-row-gate-closed")
                text.append(gate)
            card.append(text)
            if self._on_try is not None:
                try_button = Gtk.Button(label="Try it")
                try_button.add_css_class("suggested-action")
                try_button.set_valign(Gtk.Align.CENTER)
                try_button.set_margin_end(10)
                try_button.set_tooltip_text(f"Send: {task.prompt}")
                try_button.update_property([Gtk.AccessibleProperty.LABEL], [f"Try: {task.title}"])
                try_button.connect("clicked", self._on_try, task.prompt)
                card.append(try_button)
            box.append(card)
            indexed.append((card, " ".join((task.title, task.how, task.prompt)).lower()))
        self._index.append((box, indexed))
        return box

    def _build_row(self, capability, config) -> Gtk.Box:
        # Text in one column, Try beside the whole of it. Try used to sit in the
        # title line, and a button is taller than a label, so every row with an
        # example rendered with a gap under its name that the rows without one
        # did not have - two rhythms in one list.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("help-row")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_hexpand(True)
        row.append(text)

        name = Gtk.Label(label=capability.label)
        name.add_css_class("help-row-label")
        name.set_halign(Gtk.Align.START)
        text.append(name)

        if capability.description:
            detail = Gtk.Label(label=capability.description)
            detail.add_css_class("help-row-detail")
            detail.set_wrap(True)
            detail.set_halign(Gtk.Align.START)
            detail.set_xalign(0.0)
            text.append(detail)

        if capability.consent_key:
            open_now = capability.gate_is_open(config)
            gate = Gtk.Label(label=capability.gate_help(open_now))
            gate.add_css_class("help-row-gate")
            gate.add_css_class("help-row-gate-open" if open_now else "help-row-gate-closed")
            gate.set_wrap(True)
            gate.set_halign(Gtk.Align.START)
            gate.set_xalign(0.0)
            text.append(gate)

        if capability.example and self._on_try is not None:
            try_button = Gtk.Button(label="Try")
            try_button.add_css_class("flat")
            try_button.add_css_class("circular")
            try_button.set_valign(Gtk.Align.CENTER)
            try_button.set_tooltip_text(f"Send: {capability.example}")
            try_button.connect("clicked", self._on_try, capability.example)
            row.append(try_button)
        return row


class TranscriptView(Gtk.ScrolledWindow):
    """An append-only list of conversation turns.

    A `ScrolledWindow` around a vertical `Gtk.Box` rather than a single label:
    the point is that turns *accumulate*, so a spoken conversation stays
    readable instead of the window showing only the most recent reply.
    """

    __gtype_name__ = 'TranscriptView'

    def __init__(self, config=None, caps=None, on_suggestion=None,
                 reduce_motion: bool = False, on_regenerate=None,
                 on_show_diff=None) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_propagate_natural_height(True)

        self._config = config
        self._caps = caps or []
        self._reduce_motion = reduce_motion
        self._on_suggestion = on_suggestion
        #: Called with no arguments when the user asks for the last answer again.
        #: None means the window offers no such control, rather than offering one
        #: that goes nowhere.
        self._on_regenerate = on_regenerate
        #: Opens the Diff panel; given to every tool card that rewrote a file.
        self._on_show_diff = on_show_diff
        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._rows.set_margin_top(10)
        self._rows.set_margin_bottom(10)
        self._rows.set_margin_start(10)
        self._rows.set_margin_end(10)

        # Shown only when the view is not already at the newest turn. It exists
        # because the alternative is worse: either the transcript follows every
        # streamed chunk (dragging the reader away from what they were reading)
        # or it does not (and a new reply arrives off-screen with nothing
        # saying so). The button is the third option.
        jump = Gtk.ToggleButton()
        jump.set_icon_name("go-down-symbolic")
        jump.add_css_class("flat")
        jump.add_css_class("circular")
        jump.set_tooltip_text("Jump to the newest message")
        jump.update_property([Gtk.AccessibleProperty.LABEL], ["Jump to the newest message"])
        jump.set_halign(Gtk.Align.END)
        jump.set_valign(Gtk.Align.END)
        jump.set_visible(False)
        jump.connect("clicked", self._on_jump_clicked)
        self._jump_button = jump
        overlay = Gtk.Overlay()
        overlay.set_child(reading_width(self._rows))
        overlay.add_overlay(jump)
        overlay.set_measure_overlay(jump, False)
        self.set_child(overlay)

        #: Whether the view should follow new text. Recomputed from the scroll
        #: position on every layout (see `_apply_follow`), so it is a record of
        #: what the reader is doing rather than a guess.
        self._following = True
        self._follow_scheduled = False
        #: (role, text) of every turn currently on screen, in order. Search needs
        #: the *text*, and reading it back out of the widget tree means walking
        #: labels whose contents include a code block's language tag and a table
        #: cell - which would find "bash" in a reply that never mentions it.
        self._turns: "list[tuple[str, str]]" = []
        #: Bumped every time the reader's position is recorded, so a follow that
        #: was scheduled *before* that can tell it is stale. Measured without it:
        #: a reader who scrolled away and then had a turn arrive was jumped to
        #: the bottom anyway, by an idle callback queued while they were still
        #: at the bottom - the click and the scroll landing in the same tick.
        self._follow_generation = 0
        self._current_assistant = None
        self._blocks: Gtk.Box | None = None
        self._block_text = ""
        self.show_placeholder()

    def set_suggestion_handler(self, handler) -> None:
        self._on_suggestion = handler
        if self._is_placeholder():
            self.show_placeholder()

    def set_regenerate_handler(self, handler) -> None:
        """Attach (or detach) "Ask that again".

        Turns already on screen keep whatever they were built with; the next
        reply is the first one with the control, which is the same rule the
        suggestion chips follow (a redraw is needed to change the empty state).
        """
        self._on_regenerate = handler

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
        self._turns = []
        child = self._rows.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._rows.remove(child)
            child = following
        self._current_assistant = None
        self._blocks = None
        self._block_text = ""

    def _append_turn(self, role: str, text: str) -> Gtk.Label:
        """Append one turn, styled by role, and return its label."""
        self._note_position()
        if self._is_placeholder():
            # Clears the search index as well, so the record below is the first
            # entry in it rather than the one that gets wiped.
            self.clear()
        if role == "assistant":
            # Blocks, not one label: a command, a table and an equation each
            # need their own controls, and all three arrive in the same reply.
            # The escaping is still `markdown_lite`'s - `reply_blocks` decides
            # what a thing *is*, never what markup it is allowed to contain.
            stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            stack.set_hexpand(True)
            for widget in reply_blocks.widgets_for(text):
                stack.append(widget)
            label = Gtk.Label()
            label.set_visible(False)
            self._blocks = stack
            self._block_text = text
            stack.add_css_class("transcript-turn")
            stack.add_css_class("transcript-assistant")
            stack.update_property([Gtk.AccessibleProperty.LABEL], [f"assistant said: {text}"])
            row = self._with_copy_button(stack, text)
            # The turn's text on the row itself. A turn is several widgets now,
            # so there is no label to read it out of - and `update_property` is
            # write-only in practice on this build: reading a Box's accessible
            # label back with `[None]` segfaults, which is how this was found.
            row._turn_text = text
            self._rows.append(row)
            self._turns.append((role, text))
            self._scroll_to_end()
            return stack

        # A user's own message is what they typed, and re-rendering it would
        # change what they see into what they did not write. Selectable, so the
        # text is still reachable without a copy button.
        label = Gtk.Label()
        label.set_wrap(True)
        label.set_selectable(True)
        label.set_text(text)
        if role == "user":
            # Hug the text on the right rather than fill the row. With the
            # default FILL the bubble spanned the whole transcript and the words
            # sat at its far right edge - rendered, a 28-character question was a
            # 660px grey bar. The text inside is left-aligned so a wrapped
            # message reads as a paragraph, and the width cap plus the left
            # margin keep a long message from becoming a wall again.
            label.set_halign(Gtk.Align.END)
            label.set_xalign(0.0)
            label.set_max_width_chars(USER_TURN_MAX_CHARS)
            label.set_margin_start(48)
        else:
            label.set_xalign(1.0)
        label.add_css_class("transcript-turn")
        label.add_css_class(f"transcript-{role}")
        # A screen reader should announce a finished turn, not every word of
        # a partial one, so the role and label are set explicitly.
        label.update_property([Gtk.AccessibleProperty.LABEL], [f"{role} said: {text}"])
        if role == "user":
            self._rows.append(label)
        else:
            self._rows.append(self._with_copy_button(label, text))
        self._turns.append((role, text))
        self._scroll_to_end()
        return label

    def _with_copy_button(self, label, text: str) -> Gtk.Box:
        """An assistant turn with copy buttons beside it.

        The reply is the only thing in the window worth keeping - a command, a
        path, a sentence to paste somewhere - and selecting wrapped text by
        dragging across a bubble is the wrong gesture for it. The buttons are
        flat and dim until hovered or focused, so a transcript of twenty turns
        is not twenty competing buttons.

        A reply with a fenced code block gets a second button, because the two
        things worth copying out of such a reply are different: the command to
        run, and the explanation of what it does. Copying the whole reply to get
        `sudo systemctl restart NetworkManager` means also pasting three
        sentences of prose into the terminal. Alpaca copies per block for the
        same reason. Only what is inside the fences is copied - the fences and
        the language tag are the markup, not the code.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_hexpand(True)
        label.set_hexpand(True)
        row.append(label)

        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        buttons.set_valign(Gtk.Align.START)
        if self._on_regenerate is not None:
            again = Gtk.Button()
            again.set_icon_name("edit-undo-symbolic")
            again.add_css_class("flat")
            again.add_css_class("circular")
            again.set_tooltip_text("Ask that again")
            again.update_property([Gtk.AccessibleProperty.LABEL], ["Ask that again"])
            again.connect("clicked", self._on_regenerate_clicked)
            buttons.append(again)
        # No code button here any more: each `CodeBlock` carries its own, next to
        # the code it copies. A turn-level one had to guess which block was meant
        # and could only offer to copy all of them, which is the wrong answer for
        # a reply with two scripts in it.
        buttons.append(self._copy_button("Copy this reply", "Copy this reply", text))
        row.append(buttons)
        return row

    def _on_regenerate_clicked(self, _button: Gtk.Button) -> None:
        """Hand the request to the window.

        This widget does not know how to re-ask anything - the assistant and the
        model live in the application - so it only asks. A callback that is not
        there means no button was built, so this cannot be reached unconnected.
        """
        if self._on_regenerate is not None:
            self._on_regenerate()

    @staticmethod
    def _copy_button(tooltip: str, accessible: str, payload: str) -> Gtk.Button:
        button = Gtk.Button()
        button.set_icon_name("edit-copy-symbolic")
        button.add_css_class("flat")
        button.add_css_class("circular")
        button.set_tooltip_text(tooltip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [accessible])
        button.connect("clicked", TranscriptView._on_copy_clicked, payload)
        return button

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

    # -- finding something in the conversation ---------------------------

    def visible_turns(self) -> "list[tuple[str, str]]":
        """(role, text) for every turn on screen, in order.

        The list search filters against, and the transcript's own record - so
        "what does this window show" has one answer here rather than two.
        """
        return list(self._turns)

    def find(self, needle: str) -> "list[int]":
        """Indexes of the turns containing `needle`, case-insensitively.

        Empty for an empty needle, deliberately: matching every turn is not what
        opening a search box with nothing in it means.
        """
        if not (needle or "").strip():
            return []
        lowered = needle.strip().lower()
        return [index for index, (_role, text) in enumerate(self._turns)
                if lowered in text.lower()]

    def highlight(self, needle: str) -> int:
        """Mark every matching turn, and return how many there were.

        The mark is a CSS class on the turn's row rather than markup on the text:
        the text is model output that has been through the escaping already, and
        the only thing that should change here is how it looks.
        """
        matches = set(self.find(needle))
        for index, row in enumerate(self._turn_rows()):
            if index in matches:
                row.add_css_class("transcript-match")
            else:
                row.remove_css_class("transcript-match")
        return len(matches)

    def scroll_to_turn(self, index: int) -> bool:
        """Bring one turn into view, if there is one at that index.

        Computed from the row's own allocation rather than handed to a widget
        method: `Gtk.Widget.scroll_to_visible` **does not exist** on GTK 4.14
        (`hasattr` is False and the class has no scroll methods at all), so the
        obvious one-liner raises `AttributeError` on the first search hit. The
        adjustment is moved by hand, and `forced` because this is a deliberate
        jump rather than the user's own scrolling.
        """
        rows = self._turn_rows()
        if not (0 <= index < len(rows)):
            return False
        adjustment = self.get_vadjustment()
        if adjustment is None:
            return False
        # `get_allocation` is deprecated and returns a `Gdk.Rectangle`, whose
        # fields are attributes rather than getters - so `get_y()` on it is an
        # AttributeError, found by running it.
        allocation = rows[index].get_allocation()
        top, height = allocation.y, allocation.height
        page = adjustment.get_page_size()
        # The scrolled window's own height sits above this box, so the box's y is
        # already relative to the top of the visible area.
        target = top - max(0.0, (page - height) / 2)
        adjustment.set_value(max(0.0, min(target, adjustment.get_upper() - page)))
        # A jump the user did not make: follow it, so the next chunk of a reply
        # does not yank them back to the bottom.
        self._following = False
        return True

    def _turn_rows(self) -> "list[Gtk.Widget]":
        """One row per turn, the placeholder excluded.

        A turn's text lives on the row (`_turn_text`) with its widgets hanging
        off it, so this is in the same order `visible_turns()` is in.
        """
        out = []
        child = self._rows.get_first_child()
        while child is not None:
            if "transcript-placeholder" not in child.get_css_classes():
                out.append(child)
            child = child.get_next_sibling()
        return out

    def _is_placeholder(self) -> bool:
        first = self._rows.get_first_child()
        if first is None:
            return True
        return first.get_css_classes().__contains__("transcript-placeholder")

    def add_user_turn(self, text: str) -> None:
        # Both halves of "no turn is in progress": clearing only the label left
        # `_blocks` pointing at the previous reply, so the next assistant turn
        # replaced it instead of starting a new one and the second answer never
        # appeared at all.
        self._current_assistant = None
        self._blocks = None
        self._block_text = ""
        self._append_turn("user", text)
        # A question the user just asked is a question they want to watch being
        # answered, wherever they had scrolled to.
        self._scroll_to_end(force=True)

    def add_assistant_turn(self, text: str) -> None:
        """Add or update the assistant's turn.

        Updating rather than appending matters: the assistant's reply is
        written once when it is known and replaced when a tool call produces a
        better version, and a voice turn that appended twice would read as
        the assistant having answered twice.
        """
        # Recorded here, at the public entry, rather than only inside
        # `_replace_blocks`: a streamed update whose text happens to be unchanged
        # returns early from there, and this path then acted on an intent that
        # was recorded before the reader had scrolled away.
        self._note_position()
        if self._blocks is not None:
            self._replace_blocks(text)
            self._scroll_to_end()
            return
        self._current_assistant = self._append_turn("assistant", text)

    def _replace_blocks(self, text: str) -> None:
        """Rebuild the current turn's blocks in place.

        The old widgets are removed one at a time and the new ones appended,
        because the stack is inside a row that also holds the copy buttons: a
        `set_child` here would take those with it.
        """
        if text == self._block_text:
            return
        self._note_position()
        # **Only the reply's own widgets go.** This removed every child of the
        # turn, and the turn also holds the tool cards and the housekeeping
        # notices - which are added *before* the reply text arrives, because the
        # skills run first. So every card was deleted the moment the answer
        # landed: rendered, a turn that edited a file showed the reply and no
        # trace of the edit, while the rail beside it listed the changed file.
        # Tests built cards on their own and never followed one with a reply.
        child = self._blocks.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            if not _keeps_across_replies(child):
                self._blocks.remove(child)
            child = following
        for widget in reply_blocks.widgets_for(text):
            self._blocks.append(widget)
        self._block_text = text
        self._blocks.update_property([Gtk.AccessibleProperty.LABEL], [f"assistant said: {text}"])
        row = self._blocks.get_parent()
        if row is not None:
            row._turn_text = text

    def add_tool_call(self, name: str, arguments: dict, result: str = "",
                      ok: bool = True) -> None:
        """Show what a skill did, above the reply it led to.

        A card per call, in the order they ran, inside the assistant's turn: the
        status line said a name was running and then nothing, so the one moment
        the user can check the arguments is after it is over. `result` may be
        filled in later by calling this again with the same `name`.
        """
        self._note_position()
        if self._blocks is None:
            self._current_assistant = self._append_turn("assistant", "")
        for child in list(self._blocks_tool_cards()):
            if getattr(child, "name", "") == name and not getattr(child, "result", ""):
                child.result = result
                child.ok = ok
                self._rebuild_card(child, result, ok)
                return
        card = reply_blocks.ToolCallCard(name, arguments, result, ok,
                                         on_show_diff=self._on_show_diff)
        # Above the reply, in the order the skills ran: that order *is* the
        # record, and reversing it to put the newest on top would make a
        # six-call turn unreadable as a sequence of decisions.
        cards = [child for child in self._blocks_tool_children()
                 if isinstance(child, reply_blocks.ToolCallCard)]
        if not cards:
            self._blocks.prepend(card)
        else:
            # Gtk.Box has no insert_child_before; placing after the last card is
            # the same position and is the only one of the two that exists.
            self._blocks.insert_child_after(card, cards[-1])
        self._scroll_to_end()

    def add_notice_row(self, sentence: str, kind: str = "compaction") -> None:
        """One dim line in the transcript about the turn's own housekeeping.

        Two kinds today, both things a person would otherwise have to guess at:
        `compaction` (older context was shortened to fit the window - cline's
        `CompactionRow`, OpenHands' `CondensationEvent`) and `cloud` (this turn
        was answered off the machine). Placed *inside* the assistant's turn
        rather than as its own message, because it is a note about the turn, not
        something the assistant said.
        """
        self._note_position()
        if self._blocks is None:
            self._current_assistant = self._append_turn("assistant", "")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class(TURN_NOTICE_CSS)
        # Neither `cloud-symbolic` nor `view-convert-symbolic` is a glyph this
        # theme ships - checked with `Gtk.IconTheme.has_icon`, which is what
        # `test_ui_layout_contract.py::test_every_surface_icon_exists_on_this_machine`
        # now checks for every `-symbolic` literal in this package. A name the
        # theme lacks draws nothing and leaves the gap, which reads as a broken
        # row rather than as a missing glyph.
        #
        # `network-transmit-receive-symbolic` for a turn answered off this
        # machine, because that is the thing it depicts. `view-convert` was
        # standing in for compaction, where it meant nothing; a justified block
        # of text is what compaction acts on.
        icon = Gtk.Image.new_from_icon_name(
            "network-transmit-receive-symbolic" if kind == "cloud"
            else "format-justify-fill-symbolic")
        icon.add_css_class("dim-label")
        row.append(icon)
        label = Gtk.Label(label=sentence)
        label.add_css_class("dim-label")
        label.set_xalign(0.0)
        label.set_wrap(True)
        row.append(label)
        self._blocks.insert_child_after(row, self._blocks.get_first_child())
        self._scroll_to_end()

    def _blocks_tool_cards(self):
        return [child for child in self._blocks_tool_children()
                if isinstance(child, reply_blocks.ToolCallCard)]

    def _blocks_tool_children(self):
        if self._blocks is None:
            return []
        child, out = self._blocks.get_first_child(), []
        while child is not None:
            out.append(child)
            child = child.get_next_sibling()
        return out

    @staticmethod
    def _rebuild_card(card, result: str, ok: bool) -> None:
        """Fill a card in after the fact, keeping its open/closed state."""
        card.update(result, ok)

    #: How close to the bottom still counts as "following along", in pixels.
    #: Alpaca uses the same 150 and for the same reason: a reply streams in over
    #: seconds, and a reader who scrolled up to re-read something is dragged back
    #: to the bottom by every chunk that arrives.
    STICKY_PX = 150

    def _scroll_to_end(self, force: bool = False) -> None:
        """Follow the newest turn, unless the reader has gone back to look at
        something.

        `force` is for the moment a *new turn starts*: the user asked a question
        and wants to watch it being answered, so it follows regardless of where
        they were scrolled to. Everything after that follows only while they are
        at the bottom.

        This used to be a single deferred jump, and that was not enough: the
        adjustment's upper bound only means anything after allocation, so the
        jump ran against a stale (often zero) bound and the newest reply
        arrived off-screen. Following has to happen *as the content grows*, which
        is what `_on_size_allocate` is for.
        """
        if force:
            self._following = True
        self._apply_follow()

    def _note_position(self) -> None:
        """Record where the reader was, *before* the content changes.

        This has to happen before the append, not after. Measured: with the
        decision taken after the fact, a first reply that made the transcript
        3000px tall arrived with the view at the top - "was I at the bottom?"
        was answered about a position that no longer existed, and the reply the
        user had just asked for was off-screen. The same test after the change is
        what a chat window does when it is not looking at you.
        """
        adjustment = self.get_vadjustment()
        if adjustment is None:
            return
        distance_from_bottom = (adjustment.get_upper() - adjustment.get_page_size()
                                - adjustment.get_value())
        self._following = distance_from_bottom <= self.STICKY_PX
        self._follow_generation += 1

    def _apply_follow(self) -> bool:
        """Act on the recorded intent, against the adjustment as it is now."""
        adjustment = self.get_vadjustment()
        if adjustment is not None:
            if adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value() <= 1:
                # Scrolled back to the bottom by hand: follow again from here.
                self._following = True
            if self._following:
                adjustment.set_value(
                    max(0.0, adjustment.get_upper() - adjustment.get_page_size()))
            self._set_jump_button(not self._following)
        return GLib.SOURCE_REMOVE

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        """Re-evaluate the follow rule every time this widget is laid out.

        A vfunc rather than a signal: `size-allocate` is not connectable from
        Python, and the transcript needs it because following has to happen *as
        the content grows* - a single deferred jump runs against a bound that is
        still zero, which is how the newest reply ended up off-screen.

        The work is deferred one turn of the loop, coalesced by a flag, because
        calling `set_value` from inside `size-allocate` re-enters layout.
        """
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        if not self._follow_scheduled:
            self._follow_scheduled = True
            generation = self._follow_generation
            GLib.idle_add(self._apply_follow_later, generation)

    def _apply_follow_later(self, generation: int) -> bool:
        self._follow_scheduled = False
        if generation != self._follow_generation:
            # The reader moved between the layout and now; whatever this callback
            # would have done is stale, and a new layout has queued its own.
            return GLib.SOURCE_REMOVE
        self._apply_follow()
        return GLib.SOURCE_REMOVE

    def _on_jump_clicked(self, button: Gtk.Button) -> None:
        """Return to the newest turn, then stand down.

        `Gtk.ToggleButton` for the look, so the pressed state has to be cleared:
        a button left active promises that pressing it again does something, and
        it does not.
        """
        button.set_active(False)
        self._scroll_to_end(force=True)

    def _set_jump_button(self, show: bool) -> None:
        """Offer a way back to the newest turn when the view has left it."""
        if getattr(self, "_jump_button", None) is None:
            return
        if bool(self._jump_button.get_visible()) == show:
            return
        self._jump_button.set_visible(show)
