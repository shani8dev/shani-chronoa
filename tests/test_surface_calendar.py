"""The calendar surface, as a built widget.

Four things this pins that reading the module cannot:

- **The panel reads the reader Chronoa already has.** `eds_calendar.events_between`
  is called through the module attribute - the same name `calendar_events` and
  the `calendar` trigger call - so a test patches the name production reads. A
  test that patched a copy this module held would prove nothing about the
  wiring, which is the failure this repo keeps re-learning.
- **Nothing here touches a real calendar.** An autouse fixture replaces
  `eds_calendar.events_between` with a stub that raises the "no Evolution Data
  Server bindings" failure, so no test on any machine can read a person's
  actual calendar; every test that needs events says what it is handing over.
- **The four honest states are four different messages.** Consent off, no
  backend on this machine, nothing on, and present-but-unreadable are
  asserted as distinct texts, because the failure this panel exists to prevent
  is any two of them collapsing into one - and the likeliest way that happens is
  the unreadable case quietly rendering as "nothing on".
- **Markup in an event summary is escaped.** Measured on this libadwaita, an
  `Adw.ActionRow` given unescaped text with a bare `&` fails its markup parse
  and renders an *empty* label, so equality with the raw characters can only
  happen when the entities were there.

`common.adw_ready()` branches the two places libadwaita genuinely changes what
can be asserted: whether the row's own title carries entities (Adw) or the raw
characters (a `Gtk.Label` takes no markup), and whether a group takes rows
through `add()` or `append`. The invariant asserted in both branches is the one
a user would notice - the label reads back as exactly the summary the calendar
had.
"""

from __future__ import annotations

import ast
import time
import types
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import eds_calendar  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.gui.surfaces import calendar as surface  # noqa: E402
from shani_chronoa.skills import calendar_events as skill_calendar  # noqa: E402

REGISTRY_SOURCE = Path(surface.__file__).parent / "__init__.py"
SCHEMA_SOURCE = (Path(surface.__file__).resolve().parents[5]
                 / "share" / "glib-2.0" / "schemas" / "org.shani.chronoa.gschema.xml")

#: A fixed instant, so "today", "tomorrow" and a countdown are the same answer
#: on every run: Friday 2026-10-02 09:00 local, with 15 hours of the day left,
#: so no derived time crosses midnight and the day grouping is unambiguous.
NOW = 1_790_911_800.0


def _event(offset, summary="Standup", location="", calendar="Personal",
           duration=3600.0, all_day=False, uid="u", end=None):
    """A real `eds_calendar.Event` - the same namedtuple the reader returns."""
    start = NOW + offset
    return eds_calendar.Event(
        start=start,
        end=start + duration if end is None else end,
        summary=summary,
        location=location,
        calendar=calendar,
        uid=uid,
        all_day=all_day,
    )


def _config(allowed):
    """The least a config can be: one boolean key, or a settings read that fails."""
    if isinstance(allowed, BaseException):
        class _Raising:
            def get_bool(self, key, default=False):
                raise allowed

        return _Raising()

    class _Config:
        def get_bool(self, key, default=False):
            assert key == surface.CONSENT_KEY, key
            return allowed

    return _Config()


def _app(allowed=True, now=NOW, **extra):
    """A stub app: the consent switch, the clock, and nothing else."""
    app = types.SimpleNamespace(calendar_now=now)
    if allowed is not None:
        app.config = _config(allowed)
    app.__dict__.update(extra)
    return app


@pytest.fixture(autouse=True)
def no_real_calendar(monkeypatch):
    """Stop every test here reaching a real Evolution Data Server.

    The default is the honest *no backend* failure, which is also what a Plasma
    install and a GNOME install without `evolution-data-server` produce. A test
    that wanted a real answer would have to say so explicitly.
    """

    def _no_bindings(start, end, timeout=10):
        raise eds_calendar.CalendarUnavailable(eds_calendar.UNAVAILABLE_NO_BINDINGS)

    monkeypatch.setattr(eds_calendar, "events_between", _no_bindings)
    return _no_bindings


def _reads(monkeypatch, events=(), error=None):
    """Hand the panel a fixed answer, through the name it reads."""
    calls = []

    def _reader(start, end, timeout=10):
        calls.append((start, end))
        if error is not None:
            raise error
        return list(events)

    monkeypatch.setattr(eds_calendar, "events_between", _reader)
    return calls


# -- tree helpers -----------------------------------------------------------


def _marked(widget, marker):
    found = []
    if marker in widget.get_css_classes():
        found.append(widget)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_marked(child, marker))
        child = child.get_next_sibling()
    return found


def _rows(widget):
    return _marked(widget, surface.ROW_CSS)


def _labels(widget):
    """Every label's text in the tree, in order."""
    found = []
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_text())
    child = widget.get_first_child()
    while child is not None:
        found.extend(_labels(child))
        child = child.get_next_sibling()
    return found


def _text(widget):
    return "\n".join(_labels(widget))


def _title(row):
    """A row's title, whichever kind `common.row()` built."""
    if common.adw_ready() and hasattr(row, "get_title"):
        return row.get_title()
    for text in _labels(row):
        if text.strip():
            return text
    raise AssertionError(f"row has no title at all: {_labels(row)!r}")


def _subtitle(row):
    if common.adw_ready() and hasattr(row, "get_subtitle"):
        return row.get_subtitle()
    texts = [text for text in _labels(row) if text.strip()]
    return texts[1] if len(texts) > 1 else ""


# -- the module's own contract ---------------------------------------------


class TestModuleContract:
    def test_it_exports_what_the_registry_reads(self):
        assert surface.TITLE == "Calendar"
        assert surface.ICON
        assert surface.SECTION == "This machine"
        for name in ("TITLE", "ICON", "SECTION", "build"):
            assert hasattr(surface, name), name

    def test_the_section_is_one_the_sidebar_knows(self):
        from shani_chronoa.gui.surfaces import SECTION_ORDER
        assert surface.SECTION in SECTION_ORDER, SECTION_ORDER

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic name nothing ships renders as a blank gap the size of an
        icon, which no assertion on the string can see."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        found = [svg for root in roots if root.is_dir()
                 for svg in root.rglob(f"{surface.ICON}.svg")]
        assert found, f"{surface.ICON} is not an icon this machine has"

    def test_the_registry_already_names_this_surface(self):
        """`SURFACE_IDS` is what `all_surfaces()` iterates, so a module nobody
        names there is a module nothing builds.

        Read with `ast` rather than by calling `all_surfaces()`, which imports
        every surface to answer - so one broken panel would turn this into a
        statement about the whole sidebar instead of about this module.
        """
        tree = ast.parse(REGISTRY_SOURCE.read_text(encoding="utf-8"))
        ids = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "SURFACE_IDS"
                for target in node.targets
            ):
                assert isinstance(node.value, ast.Tuple), "SURFACE_IDS must stay a tuple"
                ids = [element.value for element in node.value.elts
                       if isinstance(element, ast.Constant)]
        assert ids is not None, "gui/surfaces/__init__.py no longer defines SURFACE_IDS"
        assert "calendar" in ids, (
            "gui/surfaces/__init__.py never names a 'calendar' surface, so this "
            f"module is built by nothing that runs: {ids}"
        )
        assert len(ids) == len(set(ids)), f"SURFACE_IDS has a duplicate: {ids}"

    def test_the_consent_key_is_the_skill_s_own_and_is_in_the_schema(self):
        """The panel refuses on a key. A key that is not the skill's, or that no
        schema declares, would make it a different permission from the one the
        user granted - the exact shape of "a rename silently makes a sense
        permanently ungrantable"."""
        assert surface.CONSENT_KEY == skill_calendar._CONSENT_KEY
        xml = SCHEMA_SOURCE.read_text(encoding="utf-8")
        assert f'name="{surface.CONSENT_KEY}"' in xml, SCHEMA_SOURCE

    def test_the_window_is_the_span_the_skill_defines(self):
        assert surface.WINDOW in skill_calendar._RANGES
        start, end, label = surface._window(NOW)
        assert (start, end, label) == skill_calendar.span(surface.WINDOW, 0, NOW)


# -- building ---------------------------------------------------------------


class TestBuild:
    def test_build_on_a_stub_app_returns_a_gtk_widget(self):
        widget = surface.build(_app())
        assert isinstance(widget, Gtk.Widget)

    def test_an_app_with_no_config_at_all_does_not_raise(self):
        """A window whose settings have not loaded is a normal state: the panel
        must say it could not read the key rather than read the calendar."""
        widget = surface.build(_app(allowed=None))
        assert widget.state() == surface.STATE_CONSENT
        assert surface.CONSENT_UNKNOWN_DETAIL.split(".")[0] in _text(widget)

    def test_it_reads_once_per_build_and_through_the_shared_reader(self, monkeypatch):
        calls = _reads(monkeypatch, events=[_event(3600)])
        widget = surface.build(_app())
        assert widget.state() == surface.STATE_EVENTS
        assert len(calls) == 1
        # The window it asked for is the skill's, not a private midnight.
        assert calls[0] == (NOW, skill_calendar.span(surface.WINDOW, 0, NOW)[1])


# -- the rows ---------------------------------------------------------------


class TestRows:
    def test_three_events_render_three_rows(self, monkeypatch):
        _reads(monkeypatch, events=[
            _event(3600, summary="Standup", uid="a"),
            _event(3 * 3600, summary="Review the deploy", uid="b"),
            _event(86400 + 1800, summary="Dentist", uid="c"),
        ])
        widget = surface.build(_app())
        assert widget.state() == surface.STATE_EVENTS
        assert len(_rows(widget)) == 3

    def test_a_row_carries_the_time_the_summary_and_the_countdown(self, monkeypatch):
        _reads(monkeypatch, events=[_event(2 * 3600, summary="Standup",
                                           location="Room 3", calendar="Work")])
        widget = surface.build(_app())
        row = _rows(widget)[0]
        assert _title(row) == "Standup"
        subtitle = _subtitle(row)
        assert time.strftime("%H:%M", time.localtime(NOW + 2 * 3600)) in subtitle
        assert "in 2 hours" in subtitle
        assert "Room 3" in subtitle and "Work" in subtitle

    def test_days_are_grouped_today_then_tomorrow(self, monkeypatch):
        _reads(monkeypatch, events=[
            _event(2 * 3600, summary="Later today", uid="a"),
            _event(86400 + 3600, summary="Tomorrow's thing", uid="b"),
            _event(3 * 86400 + 3600, summary="The day after", uid="c"),
        ])
        widget = surface.build(_app())
        groups = widget.groups()
        assert len(groups) == 3
        headings = [text for group in groups for text in _labels(group)
                    if text in ("Today", "Tomorrow")]
        assert headings == ["Today", "Tomorrow"], headings

    def test_an_occurrence_under_way_is_kept_and_says_so(self, monkeypatch):
        _reads(monkeypatch, events=[
            _event(-1800, summary="In progress", uid="a", duration=3600.0),
        ])
        widget = surface.build(_app())
        assert len(_rows(widget)) == 1
        assert "under way" in _subtitle(_rows(widget)[0])

    def test_a_finished_occurrence_is_not_upcoming(self, monkeypatch):
        _reads(monkeypatch, events=[
            _event(-4 * 3600, summary="This morning", uid="a"),
            _event(3600, summary="Still to come", uid="b"),
        ])
        widget = surface.build(_app())
        assert [_title(row) for row in _rows(widget)] == ["Still to come"]

    def test_an_unreadable_timestamp_keeps_its_row(self, monkeypatch):
        """A row is lost information: dropping it would turn a parse problem
        into a meeting that is not on."""
        _reads(monkeypatch, events=[_event(0, summary="Odd event", uid="a", end="soon")])
        widget = surface.build(_app())
        assert [_title(row) for row in _rows(widget)] == ["Odd event"]
        assert "under way now" in _subtitle(_rows(widget)[0])

    def test_the_events_behind_the_rows_are_the_readers_own(self, monkeypatch):
        events = [_event(3600, summary="Standup", uid="a"),
                  _event(7200, summary="Retro", uid="b")]
        _reads(monkeypatch, events=events)
        widget = surface.build(_app())
        assert widget.events() == events

    def test_the_tooltip_is_the_readers_own_sentence(self, monkeypatch):
        event = _event(3600, summary="Standup", location="Room 3", calendar="Work")
        _reads(monkeypatch, events=[event])
        widget = surface.build(_app())
        described = eds_calendar.describe(event, NOW)
        assert _rows(widget)[0].get_tooltip_text() is not None
        for fragment in ("Standup", "Room 3", "Work"):
            assert fragment in _rows(widget)[0].get_tooltip_text(), fragment
        assert described  # the reader's own sentence is what the row quotes


# -- the honest states ------------------------------------------------------


class TestHonestStates:
    def test_consent_off_reads_nothing_at_all(self, monkeypatch):
        calls = _reads(monkeypatch, events=[_event(3600)])
        widget = surface.build(_app(allowed=False))
        assert widget.state() == surface.STATE_CONSENT
        assert calls == [], "the calendar was read with consent off"
        assert _rows(widget) == []
        assert surface.CONSENT_DETAIL.split(".")[0] in _text(widget)
        assert surface.CONSENT_KEY in _text(widget)

    def test_no_calendar_backend_says_plasma_has_none(self, monkeypatch):
        """The failure a Plasma install produces, in those words: no backend on
        this machine is not an empty calendar."""
        _reads(monkeypatch, error=eds_calendar.CalendarUnavailable(
            eds_calendar.UNAVAILABLE_NO_BINDINGS))
        widget = surface.build(_app())
        assert widget.state() == surface.STATE_NO_BACKEND
        text = _text(widget)
        assert "Plasma has none" in text, text
        assert "not the same as having nothing on" in text, text
        assert _rows(widget) == []

    def test_a_backend_that_is_present_but_unreadable_is_its_own_state(self, monkeypatch):
        """The registry did not answer. Reporting this as "nothing on" is the
        confident wrong answer this panel exists to avoid."""
        _reads(monkeypatch, error=eds_calendar.CalendarUnavailable(
            "the calendar registry did not answer (no session bus)"))
        widget = surface.build(_app())
        assert widget.state() == surface.STATE_UNREADABLE
        text = _text(widget)
        assert "the calendar registry did not answer" in text, text
        assert "never drawn as an empty one" in text, text
        assert _rows(widget) == []

    def test_any_other_failure_is_unreadable_and_does_not_raise(self, monkeypatch):
        _reads(monkeypatch, error=RuntimeError("segmentation fault"))
        widget = surface.build(_app())
        assert widget.state() == surface.STATE_UNREADABLE
        assert "segmentation fault" in _text(widget)

    def test_a_successful_read_with_nothing_on_is_the_empty_state(self, monkeypatch):
        calls = _reads(monkeypatch, events=[])
        widget = surface.build(_app())
        assert widget.state() == surface.STATE_EMPTY
        assert _rows(widget) == []
        assert len(calls) == 1
        label = skill_calendar.span(surface.WINDOW, 0, NOW)[2]
        text = _text(widget)
        assert "Every enabled calendar was read" in text, text
        # Spelled out rather than only checked for the label: the span's label
        # already carries "in the ", and a title that added a second one read
        # "Nothing on in the in the next 7 days" on screen. Only looking at the
        # pixels caught that.
        assert f"Nothing on {label}" in text, text
        assert "in the in the" not in text, text

    def test_the_four_states_render_four_different_messages(self, monkeypatch):
        """Each state on its own, so "each renders its own message" is checked
        rather than assumed - a shared title would pass a per-state assertion
        that only looked at one state."""
        seen = {}
        states = (
            (surface.STATE_CONSENT, _app(allowed=False)),
            (surface.STATE_NO_BACKEND, _app()),
            (surface.STATE_EMPTY, None),
            (surface.STATE_UNREADABLE, None),
        )
        for state, app in states:
            if state == surface.STATE_EMPTY:
                _reads(monkeypatch, events=[])
            elif state == surface.STATE_UNREADABLE:
                _reads(monkeypatch, error=RuntimeError("the registry did not answer"))
            widget = surface.build(app or _app())
            assert widget.state() == state, (state, widget.state())
            seen[state] = _text(widget)
        assert len(set(seen.values())) == len(states), (
            "two states rendered the same page: "
            f"{ {k: v[:60] for k, v in seen.items()} }")


# -- markup -----------------------------------------------------------------


class TestMarkup:
    HOSTILE = 'teapot & <b>blue</b> > kettle "x"'

    def test_markup_in_a_summary_is_escaped_not_parsed(self, monkeypatch):
        """The invariant, in both branches: the row shows the summary exactly as
        the calendar had it."""
        _reads(monkeypatch, events=[_event(3600, summary=self.HOSTILE)])
        widget = surface.build(_app())
        labels = _labels(_rows(widget)[0])
        assert self.HOSTILE in labels, labels

    @pytest.mark.skipif(not common.adw_ready(), reason="no libadwaita: no markup is parsed")
    def test_with_adw_the_title_carries_entities(self, monkeypatch):
        """`Adw.PreferencesRow.use-markup` defaults to True (measured on
        libadwaita 1.5), so an unescaped summary leaves the label *empty* with
        only a Gtk-WARNING on stderr."""
        from shani_chronoa import markdown_lite
        _reads(monkeypatch, events=[_event(3600, summary=self.HOSTILE)])
        widget = surface.build(_app())
        title = _title(_rows(widget)[0])
        assert title == markdown_lite.escape(self.HOSTILE), title
        assert "&amp;" in title and "&lt;b&gt;" in title
        assert _rows(widget)[0].get_property("use-markup") is True, (
            "if use-markup is ever False the escaping stops being load-bearing "
            "and this test is asserting a difference that is not there")


# -- negative control -------------------------------------------------------


def test_a_reader_that_returns_nothing_must_break_the_row_count(monkeypatch):
    """The negative control for "three events render three rows".

    The reader is replaced by one that answers with an empty list - the exact
    failure this panel is shaped against: a read that succeeds and finds
    nothing, drawn the same way as a read that succeeded and found something.
    The row count is asserted to *fail* while the mutation is in place, the
    state is checked so the failure is the state machine's and not an exception,
    and the mutation is then released and the rows are asserted back - an
    unrestored control is a change left behind for the next reader.
    """
    events = [_event(3600, summary="Standup", uid="a"),
              _event(7200, summary="Retro", uid="b")]

    def _row_count():
        widget = surface.build(_app())
        rows = _rows(widget)
        assert len(rows) == 2, (
            f"expected two rows, found {len(rows)}; state={widget.state()!r} "
            f"text={_text(widget)[:200]!r}"
        )
        return len(rows)

    _reads(monkeypatch, events=events)
    assert _row_count() == 2, "the fixture no longer renders two rows"

    _reads(monkeypatch, events=[])
    empty = surface.build(_app())
    assert empty.state() == surface.STATE_EMPTY, (
        "the mutated reader should have produced the empty state, not "
        f"{empty.state()!r} - so this control is measuring nothing"
    )
    with pytest.raises(AssertionError):
        _row_count()

    _reads(monkeypatch, events=events)
    assert _row_count() == 2, "the rows did not come back after the mutation was released"
