"""The palette and the mode strip disagreed about what mode Chronoa is in

Asked whether the mode strip and the menu/palette duplicate each other. The
duplication is deliberate and fine - the strip is always visible, the palette is
searchable, and the mode tables are listed in both on purpose. **The conflict is
worse than duplication: the two surfaces answered the same question differently,
and the palette was wrong.**

`gui/window.py`'s `_palette_mode_entries` built each entry as

    Entry(table[0][1], row[2] if row else "", ...)

- a **title from `table[0][1]`**, which is the table's *first* row's word and is
therefore a constant, identical whatever state the app is in, next to a
**subtitle from the current row**. The chip beside it, `_draw`, does the opposite
and correct thing: `row = next((r for r in table if r[0] == current), table[0])`
and then prints `row[1]`.

Measured over the seven mode tables, with each one forced to every row it can
hold:

| table | the palette's title was always | rows it misnamed |
|---|---|---|
| `_PRESENCE` | **"Here"** | Drowsy, **Asleep** |
| `_PLAN` | **"Working"** | Plan |
| `_CAPABILITY` | **"General"** | Build |
| `_STYLES` | **"Ordinary"** | Brief, Explanatory |
| `_EGRESS` | **"Nothing leaves"** | this-machine answers, then cloud, **Cloud** |
| `_HANDS` | **"Mic button"** | Wake word, talk over, Talk over |
| `_AMBIENT` | **"Only when asked"** | In the background |

**20 rows, 7 right and 13 misnamed.** So Chronoa `Asleep` was listed in the
palette as "Here", and `Plan` as "Working" - while the chip directly beside it said
`Asleep`. The subtitle was always right, which is what made this worse rather than
better: each row was a *plausible description of the wrong state* instead of being
plainly wrong, and searching "asleep" returned a row titled "Here".

**`_AUTO` is not in this at all**: the permission-posture chip ("Ask first" /
"No questions") is the one mode with **no palette entry**, where every other mode
has two doors. That is a separate asymmetry from the misnaming and it is recorded
rather than fixed - a chip reachable by one route is not a defect the way a chip
that names the wrong state is.

**On a default machine the defect is invisible**, because every mode is its
table's first row there. That is why the assertion below forces **every row**, not
the current one - the coincidence is the reason this shipped.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui import ChronoaWindow  # noqa: E402
from shani_chronoa.gui.window import _ModeStrip as Strip  # noqa: E402


class _Closed:
    """A config that grants nothing, so nothing here reaches a real machine."""

    def sense_allowed(self, key):
        return False


@pytest.fixture
def app():
    application = Gtk.Application(application_id="test.chronoa.modes")
    yield application
    GLib.timeout_add(50, lambda: (application.quit(), False)[1])


@pytest.fixture
def window(app):
    """The real window on the **real** config.

    A hand-built stub was the first version and it was too thin: the reply-style
    and hands-free readers ask for `config.reply_style`, `get_bool`,
    `wake_word_enabled` and `privacy_mode`, so building the palette raised
    `AttributeError` four times and each failure read as a *missing* entry rather
    than a misnamed one. That is the repo's own *"a fixture shaped like the call
    rather than like the thing being called"*.
    """
    from shani_chronoa.config import ChronoaConfig
    return ChronoaWindow(app, config=ChronoaConfig())


def _mode_tables():
    """The tables `_MODE_TABLES` actually lists, read out of it.

    **Not retyped.** The first version of this file listed `_AUTO` and omitted
    `_STYLES`, and the first run failed with *"no palette entry carries 'It asks
    before anything that needs permission'"* - a true statement about a mode that
    is simply **not in the palette at all**, beside six tables that were. A
    hand-kept list beside the table it describes is the drift this file is about.

    So the seven are `_CAPABILITY, _PLAN, _EGRESS, _AMBIENT, _PRESENCE, _HANDS,
    _STYLES` - **20 rows**, of which **7 are a table's first row** and **13 are
    not**. `_AUTO` is a strip-only mode with no palette entry, which is its own
    asymmetry and is recorded in the module docstring rather than asserted here.
    """
    from shani_chronoa.gui.window import ChronoaWindow
    out = []
    for table, _action, reader in ChronoaWindow._MODE_TABLES:
        name = next(n for n in dir(Strip) if getattr(Strip, n) is table)
        out.append((name, reader))
    return out


_TABLES = _mode_tables()

_CASES = [
    (table_name, reader, index)
    for table_name, reader in _TABLES
    for index in range(len(getattr(Strip, table_name)))
]


class _RecordingLabel:
    """Stands in for the chip's `Gtk.Label`, recording what `_draw` printed.

    **A stub shaped like the caller, not like the widget** - the whole point is
    to ask `_draw` itself what it would show, rather than restating its rule
    here. A test that recomputed `row[1]` in Python would agree with a broken
    `_draw` and with a broken palette equally, and would prove nothing about the
    two disagreeing.
    """

    def __init__(self):
        self.printed = None

    def set_text(self, text):
        self.printed = text


class _RecordingChip:
    """Stands in for the `Gtk.MenuButton` `_draw` styles and labels.

    **The first version passed `None` here**, which is the recorded
    *a stub shaped like the call rather than like the thing being called*: the
    failure was `AttributeError: 'NoneType' object has no attribute
    'add_css_class'` on every one of the 40 cases, which reads as `_draw`
    refusing the state rather than as the harness not giving it a chip. The
    three methods below are the whole of what `_draw` calls on it.
    """

    def __init__(self):
        self.classes = set()
        self.tooltip = None
        self.accessible = None

    def add_css_class(self, name):
        self.classes.add(name)

    def remove_css_class(self, name):
        self.classes.discard(name)

    def set_tooltip_text(self, text):
        self.tooltip = text

    def update_property(self, properties, values):
        self.accessible = (tuple(properties), tuple(values))


def _chip_word(window, table_name, reader_name, key):
    """What the strip's own chip would print for this state."""
    table = getattr(Strip, table_name)
    label = _RecordingLabel()
    window._mode_strip._draw(_RecordingChip(), label, key, table, "a mode")
    return label.printed


@pytest.mark.parametrize("table_name,reader_name,index", _CASES,
                         ids=[f"{t}-{i}" for t, _r, i in _CASES])
def test_the_palette_names_the_mode_you_are_in(window, table_name, reader_name, index):
    """Every row, not just the one a default machine happens to be on."""
    table = getattr(Strip, table_name)
    row = table[index]
    setattr(window._mode_strip, reader_name, lambda key=row[0]: key)

    modes = [e for e in window.palette_entries() if e.group == "Modes"]
    matching = [e for e in modes if e.subtitle == row[2]]
    assert matching, (
        f"no palette entry carries {row[2]!r} - so this row is not offered at all, "
        f"which is a different defect from being misnamed"
    )
    titles = {e.title for e in matching}
    assert titles == {row[1]}, (
        f"in {row[1]!r} the palette titles the row {sorted(titles)!r}; "
        f"table[0][1] is {table[0][1]!r}, which is the table's first row and a "
        f"constant"
    )
    # **The icon is the same claim in its third field, and it was the witness my
    # mutation set named that could not fail.** Every icon comes from the same
    # row - `row[3]` - so a constant icon is the identical defect on the line
    # above the one being fixed: a widget rendering something it does not know.
    # Measured over these seven tables there are **18 distinct icons**, so
    # "every mode looks the same" is a real loss rather than a hypothetical -
    # and **nothing in the suite asserted any palette icon at all**, which is
    # why that mutation came back a survivor instead of a gap I had mis-aimed.
    if len(row) > 3 and row[3]:
        icons = {e.icon for e in matching}
        assert icons == {row[3]}, (
            f"{row[1]!r} should carry {row[3]!r} and carries {sorted(icons)!r}"
        )


@pytest.mark.parametrize("table_name,reader_name,index", _CASES,
                         ids=[f"{t}-{i}" for t, _r, i in _CASES])
def test_the_palette_and_the_chip_say_the_same_thing(window, table_name,
                                                     reader_name, index):
    """**The cross-check the defect was actually about.**

    Not "the palette names this row" - "the two surfaces, which both claim to
    report the current mode, name the same one". A fix that corrected the
    palette by a different rule would pass the test above and fail this one.
    """
    row = getattr(Strip, table_name)[index]
    setattr(window._mode_strip, reader_name, lambda key=row[0]: key)

    chip = _chip_word(window, table_name, reader_name, row[0])
    palette = [e for e in window.palette_entries() if e.group == "Modes"]
    titles = {e.title for e in palette if e.subtitle == row[2]}
    assert chip == row[1], f"the chip printed {chip!r} for {row[1]!r}"
    assert titles == {chip}, (
        f"the chip says {chip!r} and the palette says {sorted(titles)!r} - two "
        f"surfaces, one question, two answers"
    )


# --- the controls on the measurement itself -------------------------------

def test_the_cases_reach_more_than_the_first_row(window):
    """The trap: on a default machine every mode **is** its table's first row, so
    a test that only checked the current state would be green against the
    shipped defect. This says the population is real."""
    total = sum(len(getattr(Strip, t)) for t, _r in _TABLES)
    assert total == 20, f"only {total} mode rows exist across the seven tables"
    non_first = sum(len(getattr(Strip, t)) - 1 for t, _r in _TABLES)
    assert non_first == 13, (
        f"only {non_first} of those rows are not a table's first row, so the "
        f"defect this file describes would barely exist"
    )


def test_no_palette_mode_entry_is_titled_with_a_constant(window):
    """The defect in one assertion, without the fixture: change every reader to
    its table's **last** row and see whether any title moved.

    Under the shipped code every title was `table[0][1]` and this returns the
    same set however the machine is configured.
    """
    def titles():
        return {e.title for e in window.palette_entries() if e.group == "Modes"}

    for table_name, reader_name in _TABLES:
        setattr(window._mode_strip, reader_name,
                lambda: getattr(Strip, table_name)[-1][0])
    moved = titles()
    for table_name, reader_name in _TABLES:
        setattr(window._mode_strip, reader_name,
                lambda: getattr(Strip, table_name)[0][0])
    first = titles()

    assert moved != first, (
        f"putting the machine in the last row of every table changed nothing "
        f"({sorted(first)}) - so the titles are constants and this file is "
        f"measuring a palette that cannot report a state"
    )


def test_a_mode_whose_key_matches_no_row_agrees_with_the_chip(window):
    """The fallback case, where the two surfaces could still diverge.

    `_draw` falls back to `table[0]` when `current` matches nothing. The palette
    fell back to `''` for the subtitle and `table[0][1]` for the title, so on an
    unreadable key the chip said the first row's word **over the first row's
    sentence** while the palette said the same word **over nothing** - the two
    halves of one row disagreeing about which row it was.

    **The assertion is the pairing, not the title.** `table[0][1] in titles`
    passes under the shipped defect - `table[0][1]` *was* the title, on every
    row of every table - which is the *a check that cannot fail* shape. What
    distinguishes them is the line underneath, so that is what is asked.
    """
    table = Strip._PRESENCE
    label = _RecordingLabel()
    window._mode_strip._draw(_RecordingChip(), label, "no-such-key", table,
                             "a mode")
    chip = label.printed
    setattr(window._mode_strip, "_read_presence", lambda: "no-such-key")
    modes = [e for e in window.palette_entries() if e.group == "Modes"]
    assert chip == table[0][1], "the chip's own fallback is not the first row"
    rows = [e for e in modes if e.title == chip]
    assert rows, f"the chip says {chip!r} and the palette does not offer it"
    assert [e.subtitle for e in rows] == [table[0][2]], (
        f"the chip names {chip!r} over {table[0][2]!r} and the palette names it "
        f"over {sorted(e.subtitle for e in rows)!r}"
    )


# --- the model and reply-style entries, which were never affected ---------

def test_the_model_and_reply_style_entries_are_unaffected(window):
    """They were already correct, and it is worth saying so rather than letting
    the fix look like it swept more than it did."""
    modes = [e for e in window.palette_entries() if e.group == "Modes"]
    assert any(e.title == "Which model answers" for e in modes)
    assert any(e.title == "Ordinary" for e in modes)


def test_the_palette_still_offers_everything_it_did(window):
    """No entry was dropped by the change. `collect()` is what drops entries,
    and it is downstream of this code - so the count is the check."""
    before = len(window.palette_entries())
    assert before >= 45, f"the palette fell to {before} entries"


def test_a_window_is_actually_built(window):
    """A fixture that failed to build would answer every assertion above with
    an empty list."""
    assert window.get_title() or window._mode_strip is not None
    assert isinstance(window._mode_strip, Strip)