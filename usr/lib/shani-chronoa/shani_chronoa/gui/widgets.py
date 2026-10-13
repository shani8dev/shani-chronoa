"""The window's building blocks: the orb, the transcript, the suggestion bar and the help window - and the assistant states they show."""

import enum
import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk, Gdk, GLib, Pango, GObject

from shani_chronoa import capabilities

from . import blocks as reply_blocks

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

#: **A turn that failed, which is not the same thing as a note about it.**
#:
#: Added *alongside* `turn-notice` rather than instead of it, because
#: `_keeps_across_replies` keys on `turn-notice` - and an error row has to
#: survive a later reply in the same turn exactly as a compaction note does, or
#: the failure vanishes the moment anything else is said.
TURN_ERROR_CSS = "turn-error"


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

    def __init__(self, suggestions, on_chosen, reduce_motion: bool = False,
                 short_words=None) -> None:
        super().__init__()
        # `short_words` maps a full question to the name its chip shows. It is
        # keyword-defaulted rather than required so every existing caller and
        # test that passes plain strings keeps working unchanged.
        short_words = short_words or {}
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
            # **The chip says the name; the question is what is sent.**
            # Measured on the real window at 460x640: the six full questions
            # need 349px in a 162px viewport, so 187px of the empty state - the
            # whole capability signal - sat below the fold, and the chips were
            # one per line while two fitted. The chip has nowhere to put a
            # sentence, so it carries `SUGGESTION_WORDS` and the tooltip, the
            # accessible name and the click payload keep the question in full.
            # A prompt with no entry falls back to the question itself, so this
            # can only ever fail by being too long - never by being missing.
            chip_text = short_words.get(suggestion, suggestion)
            chip_label = Gtk.Label(label=chip_text, ellipsize=Pango.EllipsizeMode.END,
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
        rows.append(self._build_shortcuts(config))
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

    #: What a person can press. **Hand-written, and a test proves it.**
    #:
    #: The first version of this comment claimed the list was *read* from the
    #: app's actions and could not go stale. It is not and it could - it is
    #: five rows a person typed, which is exactly the failure mode of every
    #: hand-written shortcuts panel. So instead of claiming otherwise,
    #: `test_every_shortcut_listed_is_really_bound` checks each row against the
    #: source that binds it, and a shortcut panel that drifts fails a test.
    #: The third field is **a search haystack, not a reference to a binding** -
    #: it goes into `_index` so `filter()` can find the row. Two of these five
    #: are keyval handlers with no action at all (`?` and Escape), and naming
    #: them after actions that do not exist is how the next person to read this
    #: concludes a binding is missing when it is only the label that was wrong.
    _SHORTCUT_ACTIONS = (
        ("Ctrl+K", "anything else: every panel, mode and command",
         "command palette"),
        ("Ctrl+F", "find in this conversation", "find", "search"),
        ("?", "this window", "help", "what chronoa can do"),
        ("F9", "the sidebar", "panels"),
        ("Escape", "stop what is running", "stop", "cancel"),
    )

    def _build_shortcuts(self, config) -> Gtk.Box:
        """What *you* can do, under "What Chronoa can do".

        **The distinction this section exists for.** Everything below it is what
        the assistant is able to do; nothing on the screen answers what the
        person at the keyboard can do, and two of these five keys are the only
        way to reach a control that is deliberately not on the toolbar.

        Grouped like the rest of the window, with the key in its own column, so
        a shortcut list reads as a list rather than as a paragraph.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("help-group")
        heading = Gtk.Label(label="What you can do")
        heading.add_css_class("help-group-heading")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        indexed = []
        for entry in self._SHORTCUT_ACTIONS:
            key, what = entry[0], entry[1]
            # Everything after `what` is the row's own search haystack, so a
            # shortcut can be found by any of its words.
            row = self._build_shortcut_row(key, what)
            box.append(row)
            indexed.append(
                (row, " ".join(str(part) for part in entry).lower()))
        self._index.append((box, indexed))
        return box

    def _build_shortcut_row(self, key: str, what: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(14)
        row.set_margin_end(14)
        row.set_margin_top(3)
        row.set_margin_bottom(3)
        label = Gtk.Label(label=key)
        label.add_css_class("heading")
        label.add_css_class("caption")
        label.set_xalign(0.0)
        label.set_width_chars(9)
        row.append(label)
        text = Gtk.Label(label=what)
        text.set_xalign(0.0)
        text.set_hexpand(True)
        text.set_wrap(True)
        row.append(text)
        return row

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
        #: Set by `set_rewind_handler`; None means no assistant to rewind to.
        self._on_rewind = None
        self._rewind_rows: "list[tuple[Gtk.Widget, int]]" = []
        #: Set by `set_turn_footer_handler`; `None` means a turn draws no
        #: footer, which is the empty state that was always reachable.
        self._on_turn_footer = None
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
        #: Per turn index, the sentences of any notice rows inside it. **A dict,
        #: not a second list.** `_turns` is mutated in exactly two places - the
        #: `del` in `truncate_from_turn` and the `= []` in `clear` - and a
        #: parallel list has to be updated at both or search starts reporting
        #: matches against turns that no longer exist. Keyed by index, a missed
        #: entry costs a missed match and nothing else: there is no second
        #: length that can drift.
        self._turn_notes: "dict[int, list[str]]" = {}
        #: For each turn, the child that was already in `_rows` when it began -
        #: the last row *before* it - or None when the turn started an empty
        #: transcript. An anchor widget rather than an index, because `_rows` is
        #: a `Gtk.Box`: it has no `len()` and no `get_nth_child()`, so both of the
        #: things an index would have been used for are unavailable. Measured, not
        #: assumed - `len(self._rows)` raises `TypeError: object of type 'Box'
        #: has no len()`. See `_append_turn`.
        self._turn_start: "list" = []
        #: Bumped every time the reader's position is recorded, so a follow that
        #: was scheduled *before* that can tell it is stale. Measured without it:
        #: a reader who scrolled away and then had a turn arrive was jumped to
        #: the bottom anyway, by an idle callback queued while they were still
        #: at the bottom - the click and the scroll landing in the same tick.
        self._follow_generation = 0
        self._current_assistant = None
        self._blocks: Gtk.Box | None = None
        self._block_text = ""
        self._connect_theme_changes()
        self.show_placeholder()

    def _connect_theme_changes(self) -> None:
        """Re-colour the code blocks when the desktop switches light/dark.

        **One connection for the whole transcript, owned here** rather than by
        each `CodeBlock`. A block connecting its own bound method to
        `Adw.StyleManager` - a process-wide singleton that outlives every
        widget - would keep itself alive for the life of the app, and a long
        conversation is a lot of blocks.

        libadwaita is imported inside the method rather than at module scope
        because this module is usable without it. Failing to connect costs a
        stale palette; it does not cost a window that will not open.
        """
        try:
            import gi
            gi.require_version("Adw", "1")
            from gi.repository import Adw
            Adw.StyleManager.get_default().connect(
                "dark-changed", self._on_dark_changed)
        except Exception:                               # noqa: BLE001
            logger.info("no libadwaita style manager; code keeps its first palette")

    def _on_dark_changed(self, _manager=None) -> None:
        """Walk the transcript and re-render anything that can be re-rendered.

        The whole subtree, not just the direct children of `_blocks`: a turn is
        a container holding its `CodeBlock`s, so a first-child-only walk would
        find nothing and report success.
        """
        if self._blocks is None:
            return
        stack = [self._blocks]
        while stack:
            node = stack.pop()
            refresh = getattr(node, "refresh_highlight", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:                       # noqa: BLE001
                    logger.warning("a code block would not re-render",
                                   exc_info=True)
            child = node.get_first_child() if hasattr(node, "get_first_child") else None
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()

    def set_suggestion_handler(self, handler) -> None:
        self._on_suggestion = handler
        if self._is_placeholder():
            self.show_placeholder()

    def set_turn_footer_handler(self, handler) -> None:
        """Attach (or detach) the per-turn cost footer.

        **`turn_stats()` existed and was consumed by nothing.** The assistant
        times every model call, sums the token usage the backends report, and
        returns it all from `turn_stats()` - which no caller ever read. Measured:
        zero references outside its own definition. So the number was computed
        every turn and shown to nobody, which is the same class as the stop-
        speaking button that can never appear.

        A handler rather than a stats object: the transcript has no assistant,
        and `set_regenerate_handler` below is the pattern this class already
        uses for exactly that.
        """
        self._on_turn_footer = handler

    def truncate_from_turn(self, index: int):
        """Remove turn `index` and everything after it.

        Returns the `(role, text)` of the turns removed, or None when `index`
        is not a turn - a caller that gets None has removed nothing, which is
        the only safe answer to "rewind to something that isn't there".

        **Rows, not turns, are what come off.** `_rows` also holds tool cards
        and compaction notices, so the last turn removed took its tool calls and
        notices with it. Cutting at the recorded row index gets that for free;
        cutting at the turn index would leave three orphaned tool cards above an
        answer that no longer follows them.

        The placeholder is *not* restored: the transcript is about to be sent a
        replacement turn, and showing "ask something" in between reads as a lost
        conversation. An empty transcript after a rewind still shows the user's
        edited text as soon as it is re-sent.
        """
        if not isinstance(index, int) or not 0 <= index < len(self._turns):
            return None
        removed = self._turns[index:]
        anchor = self._turn_start[index]
        # **`anchor.get_next_sibling()`, not `anchor` itself.** The anchor is the
        # last row *before* this turn, so it belongs to the previous turn and
        # removing it would take one turn too many - measured: rewinding turn 6 of
        # 7 left five rows for six turns.
        #
        # `anchor is None` means this turn began an empty transcript, so there is
        # no earlier row and everything goes.
        child = (anchor.get_next_sibling() if anchor is not None
                 else self._rows.get_first_child())
        while child is not None:
            following = child.get_next_sibling()
            self._rows.remove(child)
            child = following
        del self._turns[index:]
        del self._turn_start[index:]
        for key in [k for k in self._turn_notes if k >= index]:
            del self._turn_notes[key]
        self._current_assistant = None
        self._blocks = None
        self._block_text = ""
        return removed

    def rewind_preview(self, index: int) -> dict:
        """What rewinding to `index` would throw away, before it does.

        **Counts the rows, not the turns.** A tool call is a widget inside its
        own turn rather than a turn of its own, and a made file is inside a tool
        card - so "3 turns" said over a rewind that also discards two tool calls
        and a file is the kind of undercount that makes a destructive action
        feel cheaper than it is.

        Returns a dict with `turns`, `tool_calls`, `artifacts`, `ok`, and a
        `sentence`. `ok` is False for an index that is not a turn, so a caller
        cannot ask a question about nothing and be told "nothing".
        """
        if not isinstance(index, int) or not 0 <= index < len(self._turns):
            return {"ok": False, "turns": 0, "tool_calls": 0, "artifacts": 0,
                    "sentence": ""}
        anchor = self._turn_start[index]
        child = (anchor.get_next_sibling() if anchor is not None
                 else self._rows.get_first_child())
        tool_calls = 0
        from shani_chronoa.gui import blocks as blocks_module
        stack, seen_artifacts = [], []
        while child is not None:
            stack.append(child)
            following = child.get_next_sibling()
            child = following
        while stack:
            node = stack.pop()
            if isinstance(node, blocks_module.ToolCallCard):
                tool_calls += 1
            if isinstance(node, blocks_module.ArtifactBlock):
                seen_artifacts.extend(node._paths)
            if hasattr(node, "get_first_child"):
                grandchild = node.get_first_child()
                while grandchild is not None:
                    stack.append(grandchild)
                    grandchild = grandchild.get_next_sibling()
        # **Deduplicated.** One skill may declare the same file twice, and
        # "2 files" for one file is a lie in the direction that makes a
        # destructive action look more destructive than it is.
        unique = []
        for item in seen_artifacts:
            if item not in unique:
                unique.append(item)
        parts = [f"{len(self._turns) - index} turn(s)"]
        if tool_calls:
            parts.append(f"{tool_calls} tool call(s)")
        if unique:
            parts.append(f"{len(unique)} file(s) it made")
        return {"ok": True, "turns": len(self._turns) - index,
                "tool_calls": tool_calls, "artifacts": len(unique),
                "sentence": " and ".join(parts)}

    def turn_at(self, index: int):
        """The `(role, text)` of turn `index`, or None."""
        if 0 <= index < len(self._turns):
            return self._turns[index]
        return None

    def set_rewind_handler(self, handler) -> None:
        """Attach (or detach) what secondary-clicking a turn does.

        The window is built before the application knows whether there is an
        assistant to rewind *to*, so the handler arrives after construction - the
        same shape `set_regenerate_handler` uses. None means no assistant, and
        then no gesture is attached either: a right-click menu whose only item
        cannot work is worse than no menu.
        """
        self._on_rewind = handler
        for row, index in list(getattr(self, "_rewind_rows", ())):
            self._attach_rewind_gesture(row, index, None)
        self._rewind_rows = []

    def _attach_rewind_gesture(self, row, index, handler):
        """Put a right-click on one row, or take the existing one off."""
        if handler is None:
            for gesture in list(getattr(row, "_rewind_gestures", ())):
                row.remove_controller(gesture)
            row._rewind_gestures = []
            return
        gesture = Gtk.GestureClick()
        # Secondary button only: a left-click that popped a menu would fight the
        # text selection the label is explicitly selectable for.
        #
        # **`Gdk`, not `Gtk`.** `Gtk.BUTTON_SECONDARY` is a GTK 3 constant and
        # GTK 4 removed it - measured, `hasattr(Gtk, "BUTTON_SECONDARY")` is
        # False while `Gdk.BUTTON_SECONDARY` is 3. Using the `Gtk` spelling
        # raised `AttributeError: 'gi.repository.Gtk' object has no attribute
        # 'BUTTON_SECONDARY'` on every call that attached a handler, which is the
        # live path: two of `test_regenerate.py`'s tests failed this way and the
        # gesture is built at :996 with a real handler. The whole class of bug
        # this repo records as its first.
        gesture.set_button(Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_rewind_pressed, row, index, handler)
        row.add_controller(gesture)
        row._rewind_gestures = list(getattr(row, "_rewind_gestures", ())) + [gesture]
        row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            ["Secondary-click to edit this and rerun from here"])

    def _on_rewind_pressed(self, gesture, n_press, x, y, row, index, handler):
        """Offer one action, and say plainly what it will do."""
        turn = self._turns[index] if 0 <= index < len(self._turns) else None
        if turn is None or turn[0] != "user":
            # Only a *user* turn is a place to branch from: rewinding to the
            # middle of an answer has no question to re-ask.
            return False
        popover = Gtk.Popover()
        popover.set_parent(row)
        popover.set_has_arrow(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        button = Gtk.Button(label="Edit this and rerun from here")
        button.set_valign(Gtk.Align.START)
        note = Gtk.Label(label="Everything after it is removed")
        note.add_css_class("dim-label")
        note.set_wrap(True)
        note.set_max_width_chars(32)
        box.append(button)
        box.append(note)
        popover.set_child(box)
        button.connect("clicked", lambda _b: (popover.popdown(), handler(index, turn[1])))
        popover.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                ["Edit this turn and rerun from here"])
        popover.popup()
        return True

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
        # **The shortcuts are in this label, not a row of their own.**
        #
        # Ctrl+K is now the home of four mode controls - when it is, nobody will
        # ever find them, and that is a regression the strip caused. The
        # placeholder already exists to give a new user something to act on,
        # and a keyboard shortcut nobody can discover is a control that is not
        # there. **Both keys are named with what they do**, because "Ctrl+K"
        # alone is not a reason to press it and the word *palette* appears
        # nowhere else in the interface.
        #
        # It was a **second row** at first, and
        # `test_empty_state_names_what_to_do` failed with *"unexpected row in
        # the empty state"*. That test's invariant is the right one - the empty
        # state is the placeholder and the suggestion chips, and a third sibling
        # is indistinguishable from a stray row that a cleared transcript grew -
        # so the sentence went **into** the placeholder instead. One widget
        # fewer, and the invariant survives untouched.
        label = Gtk.Label(label="Ask something, or press the orb to speak.")
        label.add_css_class("transcript-placeholder")
        label.set_wrap(True)
        label.set_halign(Gtk.Align.CENTER)
        self._rows.append(label)

        # **A row of its own, carrying `dim-label`, and not a `<span>` inside
        # the placeholder.** Both alternatives were tried and measured:
        #
        # - **`size='small'` inside the placeholder's markup** works, keeps the
        #   empty state at two rows, and is *not* the app's dim style - so the
        #   shortcuts would not follow a theme change to `dim-label` that every
        #   other quiet line in the app does follow.
        # - **`<span class="dim-label">`** is refused by GTK, measured on this
        #   box rather than assumed: *"Failed to set text ... due to error
        #   parsing markup: Attribute 'class' is not allowed on the <span>
        #   tag"*, and `set_markup` leaves the label **empty** rather than
        #   falling back to plain text - so the prompt and the shortcuts both
        #   disappear, and `get_text()` answers `""`. There is no `class`
        #   attribute on a Pango `<span>` to reach GTK's CSS with.
        #
        # So the hint is a label with the same class every other dimmed line
        # uses, and `test_window_ux`'s invariant is taught the second
        # deliberate row rather than weakened.
        keys = Gtk.Label(
            label="Ctrl+K for anything else  ·  Ctrl+F to find in this "
                  "conversation  ·  ? for what Chronoa can do")
        keys.add_css_class("dim-label")
        keys.set_wrap(True)
        keys.set_halign(Gtk.Align.CENTER)
        self._rows.append(keys)

        suggestions = capabilities.startup_suggestions(self._caps)
        if suggestions and self._on_suggestion is not None:
            bar = SuggestionBar(
                suggestions, self._on_suggestion, self._reduce_motion,
                capabilities.SUGGESTION_WORDS,
            )
            bar.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ["Suggested questions you can send"],
            )
            self._rows.append(bar)

    def clear(self) -> None:
        self._turns = []
        self._turn_start = []
        self._turn_notes = {}
        child = self._rows.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._rows.remove(child)
            child = following
        self._current_assistant = None
        self._blocks = None
        self._block_text = ""

    def _footer(self, row, handler=None) -> None:
        """A dim line under a turn saying what it cost, when there is anything.

        **Time and tokens, never money.** `turn_stats()`' own docstring draws
        that line - when cloud fallback is on a per-call price depends on the
        provider and the model, and a wrong figure invented here would be worse
        than none.

        **Silent when there is nothing to say.** No handler, or no model call
        yet, leaves no label at all rather than a zero - because "0.0s" on a turn
        that has not happened is a claim.
        """
        if handler is None:
            return
        try:
            stats = handler()
        except Exception:                               # noqa: BLE001
            return
        if not stats or not stats.get("model_calls"):
            return
        parts = []
        calls = int(stats.get("model_calls") or 0)
        parts.append(f"{calls} model call" + ("s" if calls != 1 else ""))
        wall = stats.get("wall_seconds")
        if isinstance(wall, (int, float)) and wall > 0:
            parts.append(f"{wall:.1f}s")
            # **Model time, said apart from wall time.** On a local model they
            # are almost the whole story and are not the same number: wall time
            # includes the skills this turn ran, and "how much of the wait was
            # the model" is the question you ask when deciding whether to ask a
            # smaller tier. `model_seconds` was computed and shown nowhere.
            #
            # **Only past two seconds, and only the gap.** Under that the
            # distinction is one decimal place of noise, and a footer that
            # reported it would be a footer nobody reads.
            model = stats.get("model_seconds")
            if isinstance(model, (int, float)) and model > 0 and wall >= 2.0:
                parts.append(f"{model:.1f}s of it the model")
        usage = stats.get("usage") or {}
        total = sum(int(v) for v in usage.values() if isinstance(v, (int, float)))
        if total:
            parts.append(f"{total:,} tokens")
        if stats.get("over_budget"):
            parts.append("over its time budget")

        # **What the model did not see, in the same line as what it cost.**
        # `turn_stats` counts the characters a tool result lost to clipping and
        # whether the turn left the machine. Both are things a reader cannot
        # infer from the answer, and both are the shape of the confident wrong
        # answer this repository keeps finding - so they are stated where the
        # cost is already stated, rather than in a panel nobody opens.
        withheld = int(stats.get("withheld") or 0)
        if withheld > 0:
            parts.append(f"{withheld:,} characters withheld from the model")
        if stats.get("cloud"):
            parts.append("answered off this machine")

        # **What this turn wrote to your machine**, in the same line as what it
        # cost and what it did not see. The pre-image ring records which turn
        # made each change precisely so this can be said; before that, the only
        # answer available was session-wide, and a session-wide answer to "what
        # did *this* turn do to my files" is not an answer.
        changed = [str(c) for c in (stats.get("changed") or ())]
        if changed:
            parts.append(f"changed {len(changed)} file"
                         + ("" if len(changed) == 1 else "s"))

        # The tools, but **deduplicated and only when there are few enough to
        # read.** A turn that ran twelve skills is already a wall of tool cards;
        # repeating their names in the footer adds length without adding
        # information, so it stops at three and counts the rest.
        tools = [str(t) for t in (stats.get("tools") or [])]
        if tools:
            unique = []
            for name in tools:
                if name not in unique:
                    unique.append(name)
            shown = ", ".join(unique[:3])
            if len(unique) > 3:
                shown += f" and {len(unique) - 3} more"
            parts.append(f"used {shown}")

        label = Gtk.Label(label=" · ".join(parts))
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        label.set_xalign(0.0)
        label.set_margin_start(34)      # under the copy button, not the window edge
        label.update_property([Gtk.AccessibleProperty.LABEL],
                              [f"this turn used {' and '.join(parts)}"])
        row.append(label)

    def _append_turn(self, role: str, text: str) -> Gtk.Label:
        """Append one turn, styled by role, and return its label."""
        self._note_position()
        if self._is_placeholder():
            # Clears the search index as well, so the record below is the first
            # entry in it rather than the one that gets wiped.
            self.clear()
        # **Recorded before anything is appended.** `_rows` holds tool cards and
        # compaction notices too, so turn *i* is not row *i*, and truncating
        # "from turn 7" cannot be arithmetic on either list.
        #
        # **`get_last_child()`, not `get_first_child()`** - and the first version
        # used the first child, so every anchor pointed at the opening row and a
        # rewind from turn 2 cleared the entire conversation while still
        # reporting the right turns removed. Measured by comparing anchor
        # identity against the row list; it looked entirely reasonable in a diff.
        self._turn_start.append(self._rows.get_last_child())
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
            self._footer(row, handler=self._on_turn_footer)
            self._turns.append((role, text))
            self.emit("turn-added", role)
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
            # **Only a user turn is a branch point**, so only a user turn gets the
            # gesture - an answer has no question to re-ask from the middle of.
            if self._on_rewind is not None:
                index = len(self._turns)
                self._attach_rewind_gesture(label, index, self._on_rewind)
                self._rewind_rows.append((label, index))
        else:
            self._rows.append(self._with_copy_button(label, text))
        self._turns.append((role, text))
        self.emit("turn-added", role)
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

    #: Emitted once per turn, after the row is in place. **A signal rather than
    #: polling**, because the only other way for the minimap to learn the turn
    #: count is to re-derive it on every scroll tick - work proportional to the
    #: whole conversation, on every pixel of scrolling, to learn something that
    #: changed once. The payload is the turn's role, which is all a map needs.
    __gsignals__ = {
        "turn-added": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

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
        notes = getattr(self, "_turn_notes", None) or {}
        found = []
        for index, (_role, text) in enumerate(self._turns):
            # **The turn's own sentences count as findable.** A notice is drawn
            # inside a turn but was never in `_turns`, so "find" could not reach
            # it: a person looking for the compaction that lost them an answer,
            # or the failure that ate a question, had no way to search for it.
            # The failure case was briefly worse than that - an error used to be
            # the turn's text and so *was* findable, and marking it as a notice
            # row quietly took that away.
            extra = notes.get(index)
            haystack = text if not extra else text + "\n" + "\n".join(extra)
            if lowered in haystack.lower():
                found.append(index)
        return found

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
                      ok: bool = True, artifacts: tuple = ()) -> None:
        """Show what a skill did, above the reply it led to.

        A card per call, in the order they ran, inside the assistant's turn: the
        status line said a name was running and then nothing, so the one moment
        the user can check the arguments is after it is over. `result` may be
        filled in later by calling this again with the same `name`.

        `artifacts` are the files that dispatch **declared** it produced. It is
        a separate argument rather than something read out of `result` because
        `result` is prose the model wrote and reads: a path inside it is a
        claim, not a fact, and rendering a file on the strength of one would let
        any reply containing a path put it on screen.
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
                                         on_show_diff=self._on_show_diff,
                                         artifacts=artifacts)
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
        if kind == "error":
            row.add_css_class(TURN_ERROR_CSS)
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
        # An icon that means *the kind of note*. `clock-symbolic` for a catch-up
        # line, because it is about elapsed time rather than about the
        # conversation; a third icon for a third kind of housekeeping beats a
        # chain of `if kind ==` in every caller.
        icons = {"cloud": "network-transmit-receive-symbolic",
                 "away": "preferences-system-time-symbolic",
                 "compaction": "format-justify-fill-symbolic",
                 # Checked in Adwaita and Yaru by path, not by `has_icon`,
                 # which has been wrong in this repo before.
                 "error": "dialog-warning-symbolic"}
        icon = Gtk.Image.new_from_icon_name(
            icons.get(kind, icons["compaction"]))
        icon.add_css_class("dim-label")
        row.append(icon)
        label = Gtk.Label(label=sentence)
        # **A failure is not dimmed.** Every other kind here is housekeeping and
        # is *meant* to recede behind the reply; a turn that did not happen is
        # the thing a person most needs to see, and dimming it would make the
        # notice quieter than the fake assistant bubble it replaces.
        if kind != "error":
            label.add_css_class("dim-label")
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_hexpand(True)
        row.append(label)
        # **A failure carries its own "Ask that again".**
        #
        # This button used to exist on a failed turn by accident. The failure was
        # drawn as an assistant answer, and every assistant answer goes through
        # `_with_copy_button`, which puts an "Ask that again" beside it - so the
        # retry was there, on a bubble reading `Error: <repr>`, and nobody
        # designed it. Marking the failure as a notice row (to stop it reading as
        # something Chronoa said) took the row off that path and **silently
        # removed the retry**: a failed turn became `icon + label` and nothing
        # else. The turn that most needs redoing became the one turn that cannot
        # be redone.
        #
        # So capability here comes from **which builder ran**, not from what the
        # row is about, and those are not the same question. The retry goes back
        # **on the failure itself** rather than by restoring the fake answer.
        #
        # **Only for `error`, and that is the design.** A compaction or cloud
        # note is housekeeping about a turn that *did* answer - there is nothing
        # to retry, so a button there would be an offer the app cannot honour.
        if kind == "error" and self._on_regenerate is not None:
            again = Gtk.Button()
            again.set_icon_name("edit-undo-symbolic")
            again.add_css_class("flat")
            again.add_css_class("circular")
            again.set_tooltip_text("Ask that again")
            again.update_property([Gtk.AccessibleProperty.LABEL],
                                  ["Ask that again"])
            again.connect("clicked", self._on_regenerate_clicked)
            row.append(again)
        # **Recorded for search, against the turn it is inside.** Computed after
        # the `_append_turn` above may have created one, so `len - 1` is the
        # turn this row actually belongs to and not the one before it.
        if self._turns:
            self._turn_notes.setdefault(len(self._turns) - 1, []).append(sentence)
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
                # **The empty state is read from the top; a conversation is
                # followed to the bottom.** `__init__` sets `_following = True`
                # and nothing has scrolled it away, so this branch runs on a
                # transcript that has no turns in it - and it was jumping to the
                # bottom of the suggestion bar, which took the placeholder and
                # the shortcut hints off the top with it.
                #
                # Measured on a real window at its own 460x640: `_rows` is
                # **375px** of content in a **201px** viewport and the scrollbar
                # read `value=194`, so the prompt (y=0..18) and the hints
                # (y=28..65) were both scrolled out of sight and the first
                # screen showed nothing but the middle of a chip row.
                #
                # **So "the newest turn" has to mean something.** With the
                # placeholder up there is no turn to follow, and the content is a
                # prompt rather than a tail - which is the whole reason it is at
                # the top. The suggestion bar is 294px of the 375 and still
                # overflows, so a scrollbar remains; that is a window with more
                # to show, not a control that does nothing.
                #
                # `_append_turn` clears the placeholder before it scrolls, so the
                # first real turn is followed to the bottom exactly as before.
                adjustment.set_value(
                    0.0 if self._is_placeholder()
                    else max(0.0, adjustment.get_upper() - adjustment.get_page_size()))
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
