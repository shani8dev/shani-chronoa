"""Settings window: real Adwaita pages, every sense reachable, no hardcoded theme.

Three things were wrong with the previous version, and only the first was
cosmetic.

**The consent model was unreachable.** Seventeen senses ship, each behind its
own `*-sense-enabled` key, and the settings window mentioned none of them -
zero. A user could install Chronoa, be told a sense is turned off, and have no
way to turn it on except `gsettings set` from a terminal. The switches here are
generated from the registry and the consent table rather than hand-listed, so a
sense added tomorrow appears without anyone editing this file, and one whose
key is missing from the installed schema is shown as ungrantable rather than
silently missing.

**It hardcoded a dark palette.** `window { background-color: #1a1a2e; }` and
hand-picked entry colours meant a user with a light GTK theme got a dark
dialog, and the window fought Adwaita rather than joining it. There are no
colours here now: the window uses whatever the desktop theme provides, which
is the only version that looks right on the light, dark and high-contrast
themes a user may already have chosen.

**It hand-rolled its rows out of `Gtk.Box`.** Every switch and entry was a
label plus a widget in a box, so there were no Adwaita group semantics, no
keyboard navigation the way a settings dialog is expected to behave, and no
accessibility roles. These are `Adw.SwitchRow` / `Adw.EntryRow` /
`Adw.PreferencesGroup`, which is what they should have been.

On top of that: a search entry that filters as you type, because twenty-odd
rows in one scroll is not navigable; API-key rows that can reveal what you
pasted instead of leaving you unable to check it; and a Models section showing
which model is *actually* in effect and why, via `model_choice.py`, so the hardware
tier's guess is visible as a guess rather than presented as a decision.

## What this window is not

There is deliberately no "approve this" dialog here. The prompt that appears
while Chronoa is waiting on an answer is raised from the assistant's tool loop
and rendered by the main window's presenter, and a second dialog built in a
settings window would be a second place where Escape could mean something
different - which is the exact ambiguity the three-stage approval flow exists to
remove. What belongs here is the *policy view*: what the three answers are, what
"allow for this session" would actually permit for each capability, whether
anyone is present to answer at all, and what has already been answered this
session.
"""

# The package's public API - every name the module had, from where it now lives.
from .senses import (  # noqa: F401
    SENSE_CATEGORIES,
    SENSE_LABELS,
    SUGGESTED,
)
from .voice import (  # noqa: F401
    _stt_model_is_ready,
)
from .activity import (  # noqa: F401
    _Call,
    _EXAMPLE_TARGETS,
    _LogTail,
    _TOOL_LOG_TAIL_BYTES,
    _TOOL_LOG_TAIL_LINES,
    _TOOL_ROW_LIMIT,
    _TOOL_TEXT_CHARS,
    _VERDICT_WORDS,
    _args_digest,
    _call_key,
    _calls_from_log,
    _clip,
    _flatten,
    _live_calls,
    _log_tail,
    _merge_calls,
    _number,
    _text,
    _verdict_value,
    _verdict_words,
    _when,
)
from .window import (  # noqa: F401
    SettingsWindow,
)
from .senses import SensesPage  # noqa: F401
from .privacy import PrivacyPage  # noqa: F401
from .voice import VoicePage  # noqa: F401
from .activity import ActivityPage  # noqa: F401
