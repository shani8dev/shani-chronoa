"""The triggers surface as a built widget.

Every store here is built at an explicit `tmp_path`, never against the real
per-user rules file: a `RuleStore()` with no arguments resolves
`~/.local/share/shani-chronoa/triggers/rules.json` on every call, so a test
that "forgot" would write armed rules into a real home - the leak this repo has
recorded three times. The one test that exercises the default path (the corrupt
file) sets `XDG_DATA_HOME` at that tmp dir and then asserts where the surface
says it read from, so the redirect is checked rather than assumed.

Pinned here because these are the properties a reader cannot see wrong from the
diff: that an armed rule shows the event it is waiting on, that a rule's name is
escaped rather than parsed as Pango, that an unreadable store is a sentence
instead of an exception *and* an exception instead of a silent empty list, and
that nothing here starts the engine or rewrites the file it read.
"""

from __future__ import annotations

import json
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

# The surfaces package __init__ imports Gtk through gi.repository; seed the
# short name too so a bare `import Gtk` anywhere below resolves.
sys.modules.setdefault("Gtk", Gtk)

from shani_chronoa import triggers as trigger_engine  # noqa: E402

# `surfaces/__init__.py` writes `Surfaces = Dict[str, Tuple[str, str, Callable[...]]]`
# - a value, not an annotation, so `from __future__ import annotations` does not
# defer it - while importing only `Any, Callable, Dict` from typing. Importing
# *any* surface module therefore raises `NameError: name 'Tuple' is not defined`
# (measured: `test_surface_activity.py` and `test_surface_conversations.py` both
# fail collection on it). This file may not edit that registry, so the name is
# supplied for the duration of this one import and taken away again; the surface
# module needs nothing from it. When the registry is fixed the branch simply
# stops firing and this import goes through unchanged.
import builtins  # noqa: E402
import typing  # noqa: E402

_REGISTRY_MISSING_TUPLE = not hasattr(builtins, "Tuple")
if _REGISTRY_MISSING_TUPLE:
    builtins.Tuple = typing.Tuple
try:
    from shani_chronoa.gui.surfaces import triggers  # noqa: E402
finally:
    if _REGISTRY_MISSING_TUPLE:
        del builtins.Tuple

from shani_chronoa.triggers import (  # noqa: E402
    MATCH_SUBSTRING,
    EventRule,
    EventRuleStore,
    RuleStore,
    TriggerRule,
)


class _StubApp:
    """The minimum the surface is allowed to need."""

    def __init__(self, trigger_rule_store=None, event_rule_store=None, config=None):
        self.trigger_rule_store = trigger_rule_store
        self.event_rule_store = event_rule_store
        self.config = config


class _StubConfig:
    def __init__(self, on):
        self._on = on

    def get_bool(self, key, default=False):
        return self._on


def _percept_store(tmp_path, rules=()):
    store = RuleStore(tmp_path / "rules.json")
    for rule in rules:
        store.add(rule)
    return store


def _event_store(tmp_path, rules=()):
    store = EventRuleStore(tmp_path / "event_rules.json")
    for rule in rules:
        store.add(rule)
    return store


def _percept_rule(name="pizza", sense="memory", enabled=True, substring="pizza"):
    return TriggerRule(
        name=name,
        sense=sense,
        match_mode=MATCH_SUBSTRING,
        actuator="notify",
        arguments={"summary": "dinner"},
        substring=substring,
        enabled=enabled,
    )


def _event_rule(name="unlock", event_type="screenlock", source="locked", enabled=True, **kwargs):
    return EventRule(
        name=name,
        event_type=event_type,
        source=source,
        actuator="notify",
        arguments={"summary": "screen"},
        **kwargs,
    )


def _texts(widget):
    """Every label's rendered text, in tree order.

    Recurses through any widget: GTK4 dropped `Gtk.Container`, so
    `get_first_child()` on a widget is the only way down the tree.
    """
    out = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append(child.get_text())
        out.extend(_texts(child))
        child = child.get_next_sibling()
    return out


def _row_texts(surface):
    return [_texts(row) for row in surface.rows()]


def _row_names(surface):
    """Each row's rule name - the first label in it is the name, by construction."""
    return [_texts(row)[0] for row in surface.rows()]


class TestImports:
    def test_module_exports(self):
        assert isinstance(triggers.TITLE, str) and triggers.TITLE
        assert isinstance(triggers.ICON, str) and triggers.ICON
        assert callable(triggers.build)

    def test_it_is_the_module_the_registry_names(self):
        """`surfaces/__init__.py` resolves this module by name; a typo there is
        a hole in the sidebar that only a lookup notices."""
        assert sys.modules["shani_chronoa.gui.surfaces.triggers"] is triggers


class TestBuild:
    def test_build_returns_a_gtk_widget(self, tmp_path):
        surface = triggers.build(_StubApp(_percept_store(tmp_path), _event_store(tmp_path)))
        assert isinstance(surface, Gtk.Widget)

    def test_build_with_no_stores_at_all_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        surface = triggers.build(_StubApp())
        assert isinstance(surface, Gtk.Widget)


class TestEmptyState:
    def test_an_empty_store_shows_the_empty_state(self, tmp_path):
        surface = triggers.build(_StubApp(_percept_store(tmp_path), _event_store(tmp_path)))
        assert surface.row_count() == 0
        assert surface.empty() is True
        assert surface.problems() == []

    def test_an_empty_state_is_not_an_error(self, tmp_path):
        surface = triggers.build(_StubApp(_percept_store(tmp_path), _event_store(tmp_path)))
        joined = " ".join(_texts(surface)).lower()
        assert "no trigger rules armed" in joined
        assert "traceback" not in joined and "error" not in joined


class TestRows:
    def test_one_row_per_rule_across_both_stores(self, tmp_path):
        app = _StubApp(
            _percept_store(tmp_path, [_percept_rule("pizza"), _percept_rule("wifi")]),
            _event_store(tmp_path, [_event_rule("unlock")]),
        )
        surface = triggers.build(app)
        assert surface.row_count() == 3
        assert surface.empty() is False

    def test_zero_rows_render_without_raising(self, tmp_path):
        surface = triggers.build(_StubApp(_percept_store(tmp_path), _event_store(tmp_path)))
        surface.row_count()
        surface.rows()
        surface.empty()
        surface.problems()
        surface.source_paths()

    def test_an_armed_rule_shows_its_source_event(self, tmp_path):
        app = _StubApp(
            _percept_store(tmp_path, [_percept_rule("pizza", sense="memory")]),
            _event_store(tmp_path),
        )
        surface = triggers.build(app)
        detail = " ".join(_row_texts(surface)[0])
        assert "memory" in detail, detail
        assert "armed" in detail, detail

    def test_an_event_rule_names_its_event_type_and_watched_source(self, tmp_path):
        app = _StubApp(
            _percept_store(tmp_path),
            _event_store(tmp_path, [_event_rule("unlock", event_type="netstate", source="wlan0")]),
        )
        surface = triggers.build(app)
        detail = " ".join(_row_texts(surface)[0])
        assert "netstate on wlan0" in detail, detail

    def test_a_disarmed_rule_says_so_and_an_armed_one_does_not(self, tmp_path):
        app = _StubApp(
            _percept_store(tmp_path, [
                _percept_rule("still-armed", enabled=True),
                _percept_rule("turned-off", enabled=False),
            ]),
            _event_store(tmp_path),
        )
        surface = triggers.build(app)
        # `RuleStore.all()` sorts by name, so pair on the name rather than on
        # position: an ordering assumption here would be a false pass.
        by_name = dict(zip(_row_names(surface), (_texts(row) for row in surface.rows())))
        assert "disarmed" in by_name["turned-off"][1]
        assert "disarmed" not in by_name["still-armed"][1]

    def test_a_parked_rule_says_why(self, tmp_path):
        app = _StubApp(
            _percept_store(tmp_path),
            _event_store(tmp_path, [
                _event_rule("stuck", parked=True, parked_reason="actuator kept failing")
            ]),
        )
        surface = triggers.build(app)
        assert "actuator kept failing" in " ".join(_row_texts(surface)[0])

    def test_rows_carry_a_readable_tooltip(self, tmp_path):
        app = _StubApp(
            _percept_store(tmp_path, [_percept_rule("pizza")]), _event_store(tmp_path)
        )
        surface = triggers.build(app)
        tooltip = surface.rows()[0].get_tooltip_text()
        assert tooltip and "memory" in tooltip

    def test_the_surface_says_which_file_it_read(self, tmp_path):
        store = _percept_store(tmp_path, [_percept_rule("pizza")])
        surface = triggers.build(_StubApp(store, _event_store(tmp_path)))
        assert str(store.path) in surface.source_paths()
        assert str(store.path) in " ".join(_texts(surface))


class TestPangoEscaping:
    HOSTILE = '<b>pizza & "quotes"</b> <i>x</i>'

    def test_a_markup_rule_name_is_shown_not_parsed(self, tmp_path):
        app = _StubApp(
            _percept_store(tmp_path, [_percept_rule(self.HOSTILE, substring="a & b")]),
            _event_store(tmp_path),
        )
        surface = triggers.build(app)
        shown = " ".join(_texts(surface))
        assert self.HOSTILE in shown, shown
        # The entities GLib would have produced, unescaped back, are the tell.
        assert "&amp;" not in shown and "&lt;b&gt;" not in shown, shown

    def test_a_hostile_source_and_arguments_do_not_raise(self, tmp_path):
        app = _StubApp(
            _percept_store(tmp_path),
            _event_store(tmp_path, [
                _event_rule("<i>x</i>", event_type="journalmatch", source="<b>/var/log/a&b</b>")
            ]),
        )
        surface = triggers.build(app)
        assert surface.row_count() == 1
        assert "<b>/var/log/a&b</b>" in " ".join(_row_texts(surface)[0])


class TestDegrades:
    def test_a_corrupt_rules_file_is_a_plain_message(self, tmp_path, monkeypatch):
        data = tmp_path / "data"
        monkeypatch.setenv("XDG_DATA_HOME", str(data))
        real = trigger_engine.triggers_dir()
        real.mkdir(parents=True)
        (real / "rules.json").write_text("{ not json", encoding="utf-8")
        # The other store is still readable, and must still list: an unreadable
        # percept file does not stop the event rules being shown.
        EventRuleStore().add(_event_rule("unlock"))
        with pytest.raises(trigger_engine.RuleStoreError):
            RuleStore()  # the store itself still refuses to load it
        surface = triggers.build(_StubApp())
        assert surface.row_count() == 1, "one unreadable store hid the other one"
        assert surface.empty() is False
        assert len(surface.problems()) == 1
        assert "could not be read" in surface.problems()[0]
        assert str(real / "rules.json") in " ".join(_texts(surface))

    def test_the_default_store_resolves_under_the_test_directory(self, tmp_path, monkeypatch):
        data = tmp_path / "data"
        monkeypatch.setenv("XDG_DATA_HOME", str(data))
        surface = triggers.build(_StubApp())
        assert surface.source_paths(), "the surface did not say where it read from"
        for path in surface.source_paths():
            assert path.startswith(str(data)), f"{path} is outside the test's directory"

    def test_a_store_that_raises_is_reported_not_propagated(self, tmp_path):
        class Exploding:
            path = tmp_path / "rules.json"

            def all(self):
                raise trigger_engine.RuleStoreError("the rules file is corrupt or truncated")

        surface = triggers.build(_StubApp(Exploding(), _event_store(tmp_path)))
        assert "corrupt or truncated" in " ".join(surface.problems())
        assert surface.row_count() == 0


class TestReadOnly:
    def test_building_writes_nothing(self, tmp_path):
        store = _percept_store(tmp_path, [_percept_rule("pizza")])
        before = store.path.read_bytes()
        triggers.build(_StubApp(store, _event_store(tmp_path)))
        assert store.path.read_bytes() == before
        assert store.count() == 1

    def test_it_does_not_start_an_engine(self, tmp_path, monkeypatch):
        built = []

        def _refuse(self, *args, **kwargs):
            built.append(self)
            raise AssertionError("the trigger surface constructed a TriggerEngine")

        monkeypatch.setattr(trigger_engine.TriggerEngine, "__init__", _refuse)
        app = _StubApp(
            _percept_store(tmp_path, [_percept_rule("pizza")]), _event_store(tmp_path)
        )
        surface = triggers.build(app)
        assert surface.row_count() == 1
        assert built == []

    def test_it_does_not_flip_a_rule(self, tmp_path):
        store = _percept_store(tmp_path, [_percept_rule("pizza", enabled=False)])
        surface = triggers.build(_StubApp(store, _event_store(tmp_path)))
        assert "disarmed" in " ".join(_row_texts(surface)[0])
        assert store.get("pizza").enabled is False

    @pytest.mark.parametrize("enabled", [True, False])
    def test_it_leaves_the_live_rule_objects_alone(self, tmp_path, enabled):
        """`RuleStore.all()` hands back the *same* rule objects the engine fires
        from, so flipping one in a widget would disarm a rule in the running
        engine without ever writing the file - a change with no record and no
        way back. Every field a row displays is read, never assigned."""
        store = _percept_store(tmp_path, [_percept_rule("pizza", enabled=enabled)])
        surface = triggers.build(_StubApp(store, _event_store(tmp_path)))
        assert surface.row_count() == 1
        rule = store.get("pizza")
        assert rule.enabled is enabled
        assert rule.to_dict() == RuleStore(store.path).get("pizza").to_dict()

    def test_the_gate_is_reported_as_unreadable_rather_than_off(self, tmp_path):
        class ExplodingConfig:
            def get_bool(self, key, default=False):
                raise RuntimeError("no schema installed")

        surface = triggers.build(_StubApp(
            _percept_store(tmp_path), _event_store(tmp_path), config=ExplodingConfig()
        ))
        note = " ".join(_texts(surface))
        assert "could not be read" in note, note
        assert "is off:" not in note, note

    def test_the_gate_being_off_says_so(self, tmp_path):
        surface = triggers.build(_StubApp(
            _percept_store(tmp_path), _event_store(tmp_path), config=_StubConfig(False)
        ))
        assert "is off" in " ".join(_texts(surface))


def test_row_count_negative_control(tmp_path):
    """Forcing the row count to zero must break the non-empty-store assertion.

    A count that cannot come out wrong proves nothing, so this patches the one
    function the count comes from, checks the patch actually took effect (the
    rows went to zero *while the store still holds its rules*, so the failure is
    the rendering's, not an emptied store's), and checks the assertion meant to
    catch that fails. Then it restores and checks the count is back.
    """
    store = _percept_store(tmp_path, [_percept_rule("pizza"), _percept_rule("wifi")])
    app = _StubApp(store, _event_store(tmp_path))
    assert triggers.build(app).row_count() == 2

    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(triggers, "_rules_for", lambda _store: [])
        forced = triggers.build(app)
        assert forced.row_count() == 0, "the control did not remove the rows"
        assert store.count() == 2, "the store was emptied, so the control proves nothing"
        with pytest.raises(AssertionError):
            assert forced.row_count() == 2

    assert triggers.build(app).row_count() == 2, "the control was not restored"


def test_the_rules_file_is_the_real_one(tmp_path):
    """The rows come from a real `RuleStore` on disk, not a hand-made list."""
    store = _percept_store(tmp_path, [_percept_rule("pizza")])
    reloaded = RuleStore(store.path)
    surface = triggers.build(_StubApp(reloaded, _event_store(tmp_path)))
    assert surface.row_count() == 1
    assert json.loads(store.path.read_text(encoding="utf-8"))[0]["name"] == "pizza"

def test_a_backing_off_rule_does_not_merely_say_armed():
    """Armed, parked, and *waiting* are three states, and the panel knew two.

    An event rule that has failed is neither firing nor stopped: `retry_at` is
    set, `backoff_delay()` returns the exponential ramp, and it will start again
    by itself with nothing being re-armed. The panel said "armed", so a person
    looking at a rule that had stopped responding had no way to tell a rule
    waiting out a backoff from one that had never failed — and no way to tell
    how long the wait is.

    The delay comes from the rule's own policy rather than being restated here,
    so the number on screen is the number the engine waits.
    """
    import time as _time

    from shani_chronoa.gui.surfaces.triggers import _arm_state
    from shani_chronoa.triggers.event_rules import EventRule

    def rule(**kwargs):
        return EventRule(name="r", event_type="powerstate", source="BAT0",
                         actuator="notify", arguments={}, **kwargs)

    assert _arm_state(rule()) == "armed", "a rule that has never failed"

    failing = rule()
    for _ in range(3):
        failing.consecutive_failures += 1
        failing.attempt += 1
        failing.retry_at = _time.time() + failing.backoff_delay()
    said = _arm_state(failing)
    assert said != "armed", \
        "a rule in backoff reads as armed, which is the state it is not in"
    assert "waiting" in said and f"{failing.backoff_delay():.0f}s" in said, said
    assert "3" in said, f"the count of failures is not reported: {said}"

    parked = rule()
    parked.parked = True
    assert _arm_state(parked).startswith("armed, parked"), _arm_state(parked)

    class _PerceptRule:
        enabled = True

    assert _arm_state(_PerceptRule()) == "armed", \
        "a percept rule has no backoff counters and must not borrow one"
