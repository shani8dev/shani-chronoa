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

# The package's public API - every name the module had, from where it now lives.
from .widgets import (  # noqa: F401
    AssistantState,
    ChronoaOrbWidget,
    HelpWindow,
    SuggestionBar,
    TranscriptView,
    _EASE_FACTOR,
    _HALO_MAX_PX,
    _HALO_MIN_PX,
    _LEGACY_STATE_ALIASES,
    _STATE_LABELS,
    _STATE_STYLE,
    _TICK_MS,
    _halo_size,
)
from .questions import (  # noqa: F401
    _norm_words,
    make_question_presenter,
    match_spoken_option,
)
from .window import (  # noqa: F401
    ChronoaWindow,
)
from .style import StyleMixin  # noqa: F401
from .asking import AskingMixin  # noqa: F401
from .attaching import AttachingMixin  # noqa: F401
from .conversations_menu import ConversationsMenuMixin  # noqa: F401
from .quick_ask import QuickAskWindow  # noqa: F401
from .browser import BrowserWindow, is_available as browser_available  # noqa: F401
from .blocks import ToolCallCard, widgets_for as reply_widgets  # noqa: F401
from .about import AboutWindow, ShortcutsWindow  # noqa: F401
