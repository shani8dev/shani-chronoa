"""The memory surface, as a built widget.

The store is real. Every fact on this page comes from a real `PerceptStore`
built on a tmp directory, with its own `durable_path` and `live_path`, so
`Forget` is checked against what the store actually holds afterwards and
against the bytes actually left in `memory.jsonl` - not against a list the
module stashed on itself. The rows are counted by walking the built tree for
the module's own marker CSS classes, which is the pattern `test_surface_daemon`
uses and the reason it does: a count read back out of the object being counted
cannot fail.

Two behaviours here are the ones a reader cannot get from the diff:

- **`Forget` is a real deletion, matched on identity.** `PerceptStore.forget()`
  rewrites the durable file, so a test asserts the file's line count dropped,
  and it clicks the button on a row that shares its text with another row to
  show that only one record went.
- **A transient percept cannot be forgotten one at a time**, so its button is
  insensitive and says why. That is the store's API (`clear_transient()` is the
  only transient removal), not a policy choice, and a button that worked here
  would have had to do something wider than the user asked for.

`common.adw_ready()` branches the two places where libadwaita genuinely changes
what can be asserted: `Adw.PreferencesRow.use-markup` defaults to True, so the
escaping assertions only hold in the Adw branch, and the plain-GTK group is a
`Gtk.Box` whose rows are appended rather than `add`ed. The invariant asserted in
both branches is the one that matters to a user - the label reads back as the
exact characters of the fact.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import egress, markdown_lite  # noqa: E402
from shani_chronoa.senses import Percept  # noqa: E402
from shani_chronoa.senses.store import PerceptStore  # noqa: E402


def _ensure_gui_package_importable() -> None:
    """Import `shani_chronoa.gui`, past a package-level import that is broken.

    `gui/__init__.py` ends with `from .about import AboutWindow, ShortcutsWindow`,
    and `about.py`'s `AboutWindow` subclasses `Adw.AboutWindow` - a *final*
    GType on the libadwaita this box has - so registering it raises
    `RuntimeError: could not create new GType: ...AboutWindow`. Importing any
    surface runs that package `__init__`, so without this the whole test file
    cannot even be collected (verified: `tests/test_surface_daemon.py` fails to
    collect the same way, before this file existed).

    Neither `gui/__init__.py` nor `gui/about.py` is this change's to touch, so
    the one offending module is replaced by a stub carrying the two names it
    exports. `test_the_broken_package_import_is_reproduced` below re-runs the
    real import in a child process and skips itself once it succeeds, so the
    shim cannot outlive the bug unnoticed.
    """
    try:
        importlib.import_module("shani_chronoa.gui")
        return
    except Exception:  # noqa: BLE001 - any failure here is the shim's reason
        pass
    stub = types.ModuleType("shani_chronoa.gui.about")
    stub.AboutWindow = object
    stub.ShortcutsWindow = object
    sys.modules["shani_chronoa.gui.about"] = stub
    importlib.import_module("shani_chronoa.gui")


_ensure_gui_package_importable()

from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.gui.surfaces import memory as surface  # noqa: E402

REGISTRY_SOURCE = Path(surface.__file__).parent / "__init__.py"


# -- fixtures and helpers ---------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """A real store on this test's own directory.

    Both paths are named explicitly: a `PerceptStore()` with no arguments reads
    `store.DURABLE_FILE`, which the conftest fixtures redirect, and naming them
    here is what makes "the file this page wrote" a question with one answer.
    """
    return PerceptStore(
        durable_path=tmp_path / "percepts" / "memory.jsonl",
        live_path=tmp_path / "percepts" / "live.json",
    )


def _app(store=None):
    """The least an app can be: a percept store, or deliberately not one."""
    return types.SimpleNamespace(percept_store=store)


def _fact(sense, content, *, kind="fact", ttl=None, age=0.0, source="test", **kwargs):
    return Percept(
        sense=sense,
        kind=kind,
        content=content,
        created_at=time.time() - age,
        ttl_seconds=ttl,
        source=source,
        **kwargs,
    )


def _walk(node):
    """Every widget in the tree, `node` included."""
    yield node
    child = node.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _marked(widget, css):
    """Every widget in the tree carrying `css`, in tree order."""
    return [w for w in _walk(widget) if css in w.get_css_classes()]


def _labels(widget):
    return [w.get_text() for w in _walk(widget) if isinstance(w, Gtk.Label) and w.get_text().strip()]


def _row_labels(row):
    return _labels(row)


def _buttons(row):
    return [w for w in _walk(row) if isinstance(w, Gtk.Button)]


def _forget(row):
    found = [b for b in _buttons(row) if b.get_label() == "Forget"]
    assert found, f"no Forget button in {_row_labels(row)!r}"
    return found[0]


def _durable_lines(store):
    path = Path(store.durable_path)
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _privacy(monkeypatch, value):
    """Patch privacy mode where it is read: `egress`, not this module."""
    monkeypatch.setattr(egress, "privacy_mode_enabled", lambda: value)


# -- the module contract ----------------------------------------------------


class TestModuleContract:
    def test_it_exports_the_three_names_the_registry_reads(self):
        assert surface.TITLE == "Memory"
        assert isinstance(surface.ICON, str) and surface.ICON
        assert callable(surface.build)

    def test_it_is_the_module_the_registry_names(self):
        assert sys.modules["shani_chronoa.gui.surfaces.memory"] is surface
        assert surface.__name__ == "shani_chronoa.gui.surfaces.memory"

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic icon nothing ships renders as a blank space-shaped gap in
        the sidebar, which a non-empty string does not catch."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        found = [svg for root in roots if root.is_dir()
                 for svg in root.rglob(f"{surface.ICON}.svg")]
        assert found, f"{surface.ICON} is not an icon this machine has"

    def test_the_registry_already_names_this_surface(self):
        """`SURFACE_IDS` is what the sidebar offers. Read out of the registry's
        source rather than through `all_surfaces()`, which imports every other
        panel and would report their failures as this one's."""
        source = REGISTRY_SOURCE.read_text(encoding="utf-8")
        assert re.search(r'"memory"', source), (
            "gui/surfaces/__init__.py never names a 'memory' surface, so this "
            "module is built by nothing that runs"
        )


# -- build ------------------------------------------------------------------


class TestBuild:
    def test_returns_a_widget_for_a_stub_app(self, store):
        widget = surface.build(_app(store))
        assert isinstance(widget, Gtk.Widget)

    def test_an_app_with_no_percept_store_at_all_still_builds(self):
        """`ChronoaApplication._init_components` assigns `percept_store`, so a
        window built before that has none. It is a failure to read, and it must
        not be drawn as "nothing has been remembered"."""
        widget = surface.build(types.SimpleNamespace())
        assert isinstance(widget, Gtk.Widget)
        shown = " ".join(_labels(widget))
        assert surface.NO_STORE_TITLE in shown, shown
        assert surface.EMPTY_TITLE not in shown, shown
        assert not _marked(widget, surface.FACT_CSS)

    def test_no_store_does_not_substitute_a_store_of_its_own(self, monkeypatch, tmp_path):
        """A `PerceptStore()` built here would read the default durable file and
        answer about a store the assistant is not using."""
        built = []
        real = PerceptStore.__init__

        def _spy(self, *args, **kwargs):
            built.append(kwargs.get("durable_path", args[0] if args else None))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(PerceptStore, "__init__", _spy)
        surface.build(types.SimpleNamespace())
        assert built == [], f"the surface built its own store: {built}"


# -- the facts --------------------------------------------------------------


class TestFacts:
    def test_percepts_from_a_tmp_store_render_one_row_each(self, store):
        store.add(_fact("memory", "the meeting is at 4pm in the annex"))
        store.add(_fact("memory", "prefers dark roast coffee"))
        widget = surface.build(_app(store))
        rows = _marked(widget, surface.FACT_CSS)
        assert len(rows) == 2, _row_labels(widget)
        shown = " ".join(_labels(widget))
        assert "the meeting is at 4pm in the annex" in shown
        assert "prefers dark roast coffee" in shown

    def test_every_row_states_its_age_and_its_kind(self, store):
        store.add(_fact("memory", "a fresh fact", kind="fact"))
        store.add(_fact("memory", "an older fact", kind="fact", age=7200))
        widget = surface.build(_app(store))
        texts = [" ".join(_row_labels(row)) for row in _marked(widget, surface.FACT_CSS)]
        assert any("just now" in text for text in texts), texts
        assert any("2 hours ago" in text for text in texts), texts
        for text in texts:
            assert "fact" in text, text

    def test_facts_are_grouped_by_sense_alphabetically(self, store):
        store.add(_fact("memory", "the meeting is at 4pm"))
        store.add(_fact("hearing", "switch the lights off"))
        store.add(_fact("memory", "prefers dark roast"))
        widget = surface.build(_app(store))
        groups = _marked(widget, surface.GROUP_CSS)
        order = [
            _row_labels(_marked(group, surface.FACT_CSS)[0])[0] for group in groups
        ]
        assert order == ["switch the lights off", "the meeting is at 4pm"], order
        assert len(_marked(widget, surface.FACT_CSS)) == 3

    def test_a_row_says_where_its_answer_came_from(self, store):
        store.add(_fact("memory", "prefers dark roast", source="memory sense"))
        row = _marked(surface.build(_app(store)), surface.FACT_CSS)[0]
        tooltip = row.get_tooltip_text()
        assert "memory sense" in tooltip, tooltip
        assert str(store.durable_path) in tooltip, tooltip

    def test_a_transient_row_says_it_is_live_only(self, store):
        store.add(_fact("hearing", "a passing sound", kind="sound", ttl=300))
        row = _marked(surface.build(_app(store)), surface.FACT_CSS)[0]
        text = " ".join(_row_labels(row))
        assert "live only" in text, text
        assert "5 minutes" in text, text


# -- forget -----------------------------------------------------------------


class TestForget:
    def test_forget_removes_that_row_and_deletes_it_from_disk(self, store):
        store.add(_fact("memory", "the meeting is at 4pm in the annex"))
        store.add(_fact("memory", "prefers dark roast coffee"))
        widget = surface.build(_app(store))
        assert len(_durable_lines(store)) == 2, _durable_lines(store)

        target = [r for r in _marked(widget, surface.FACT_CSS)
                  if "4pm" in " ".join(_row_labels(r))][0]
        _forget(target).emit("clicked")

        assert len(_marked(widget, surface.FACT_CSS)) == 1, _row_labels(widget)
        assert len(store.durable()) == 1
        # The deletion is on disk, not merely hidden from a read: this is what
        # `forget()` rewrites the file for.
        assert len(_durable_lines(store)) == 1, _durable_lines(store)
        remaining = " ".join(_labels(widget))
        assert "prefers dark roast coffee" in remaining
        assert "4pm" not in remaining, remaining

    def test_it_matches_on_identity_not_on_text(self, store):
        """Two records with the same text are two records; a content match would
        take both when the user asked for one."""
        store.add(_fact("memory", "prefers dark roast coffee"))
        store.add(_fact("memory", "prefers dark roast coffee"))
        widget = surface.build(_app(store))
        assert len(_marked(widget, surface.FACT_CSS)) == 2

        _forget(_marked(widget, surface.FACT_CSS)[0]).emit("clicked")

        assert len(_marked(widget, surface.FACT_CSS)) == 1, _row_labels(widget)
        assert len(store.durable()) == 1
        assert len(_durable_lines(store)) == 1

    def test_a_transient_rows_button_is_insensitive_and_says_why(self, store):
        """`PerceptStore` has no per-item transient removal - `clear_transient()`
        drops the whole window - so a working button here would do something the
        user did not ask for."""
        store.add(_fact("hearing", "a passing sound", ttl=300))
        row = _marked(surface.build(_app(store)), surface.FACT_CSS)[0]
        button = _forget(row)
        assert not button.get_sensitive()
        assert "transient" in button.get_tooltip_text(), button.get_tooltip_text()
        button.emit("clicked")
        assert len(_marked(surface.build(_app(store)), surface.FACT_CSS)) == 1

    def test_forgetting_everything_leaves_the_empty_state(self, store):
        store.add(_fact("memory", "the only fact"))
        widget = surface.build(_app(store))
        _forget(_marked(widget, surface.FACT_CSS)[0]).emit("clicked")
        assert not _marked(widget, surface.FACT_CSS)
        assert surface.EMPTY_TITLE in " ".join(_labels(widget))
        assert _durable_lines(store) == []


# -- the honest empty state -------------------------------------------------


class TestEmptyState:
    def test_an_empty_store_says_nothing_has_been_remembered(self, store):
        widget = surface.build(_app(store))
        assert not _marked(widget, surface.FACT_CSS)
        assert not _marked(widget, surface.GROUP_CSS)
        assert len(_marked(widget, surface.EMPTY_CSS)) == 1, _labels(widget)
        shown = " ".join(_labels(widget))
        assert surface.EMPTY_TITLE in shown, shown
        assert "No sense has recorded anything" in shown, shown

    def test_a_store_that_raises_is_not_an_empty_store(self):
        class _Exploding:
            def active(self, now=None):
                raise OSError("the percept file is on a disk that is gone")

        widget = surface.build(_app(_Exploding()))
        assert not _marked(widget, surface.FACT_CSS)
        shown = " ".join(_labels(widget))
        assert "could not be read" in shown, shown
        assert "the percept file is on a disk that is gone" in shown, shown
        assert surface.EMPTY_TITLE not in shown, shown


# -- privacy mode -----------------------------------------------------------


class TestPrivacyBanner:
    def test_privacy_mode_on_shows_the_banner(self, store, monkeypatch):
        _privacy(monkeypatch, True)
        widget = surface.build(_app(store))
        banners = _marked(widget, surface.BANNER_CSS)
        assert len(banners) == 1, _labels(widget)
        shown = " ".join(_labels(banners[0]))
        assert "Privacy mode is on" in shown, shown
        assert "withheld from the model" in shown, shown

    def test_privacy_mode_off_shows_no_banner(self, store, monkeypatch):
        _privacy(monkeypatch, False)
        widget = surface.build(_app(store))
        assert not _marked(widget, surface.BANNER_CSS)
        assert "Privacy mode" not in " ".join(_labels(widget))

    def test_the_banner_survives_the_store_being_unreadable(self, monkeypatch):
        _privacy(monkeypatch, True)
        widget = surface.build(types.SimpleNamespace())
        assert len(_marked(widget, surface.BANNER_CSS)) == 1


# -- markup -----------------------------------------------------------------


class TestMarkup:
    HOSTILE = 'teapot & <b>blue</b> > kettle "x"'

    def test_markup_in_a_fact_is_escaped_not_parsed(self, store):
        """The invariant, in both branches: the row shows the fact exactly as it
        was recorded.

        This is not a weak assertion. Measured on this libadwaita, an
        `Adw.ActionRow` given the same text *unescaped* fails its markup parse
        ("Entity did not end with a semicolon") and renders an **empty** label,
        with a Gtk-WARNING - so equality with the raw text can only happen if
        the entities were there, and the failure mode of getting it wrong is a
        blank row rather than a wrong character.
        """
        store.add(_fact("memory", self.HOSTILE))
        widget = surface.build(_app(store))
        row = _marked(widget, surface.FACT_CSS)[0]
        labels = _row_labels(row)
        assert self.HOSTILE in labels, labels
        assert [text for text in labels if text == self.HOSTILE], labels

    @pytest.mark.skipif(not common.adw_ready(), reason="no libadwaita: no markup is parsed")
    def test_with_adw_the_title_is_escaped_for_the_row(self, store):
        """`Adw.PreferencesRow.use-markup` defaults to True (measured on
        libadwaita 1.5), so the row title has to carry entities. Without this the
        label above would be `A & B`, and with `<b>` a label would be bold."""
        store.add(_fact("memory", self.HOSTILE))
        widget = surface.build(_app(store))
        row = _marked(widget, surface.FACT_CSS)[0]
        assert row.get_title() == markdown_lite.escape(self.HOSTILE), row.get_title()
        assert "&amp;" in row.get_title() and "&lt;b&gt;" in row.get_title()
        assert row.get_property("use-markup") is True, (
            "if use-markup is ever False the escaping is no longer load-bearing "
            "and this test is asserting a difference that is not there"
        )


def test_the_broken_package_import_is_reproduced():
    """Why `_ensure_gui_package_importable()` exists, checked rather than assumed.

    In a child process, because the parent has already stubbed the module and
    GTK cannot be initialised and torn down per-test in one interpreter. If the
    import succeeds, this skips itself: the shim above should then be deleted,
    and a shim that silently outlives its bug is how the next reader stops
    trusting it.
    """
    # The repo's own package directory, spelled the way `tests/conftest.py`
    # spells it rather than read off the imported module: this child must find
    # the *same* package the parent imported, and a path derived from
    # `shani_chronoa.__file__` is one more thing that can be subtly wrong.
    pkg_dir = Path(__file__).resolve().parent.parent / "usr" / "lib" / "shani-chronoa"
    assert (pkg_dir / "shani_chronoa" / "__init__.py").is_file(), pkg_dir
    env = dict(os.environ)
    # The child needs this test's *own* site-packages as well as the package:
    # `_hermetic_env` points HOME at a temp directory, and Python skips
    # `~/.local/lib/python3.*/site-packages` for a home it does not recognise -
    # so without this the child fails at `webtext.py`'s `import httpx`, before
    # reaching the import this test is about.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(pkg_dir)] + [p for p in sys.path if p and "site-packages" in p]
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", "import shani_chronoa.gui"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    if result.returncode == 0:
        pytest.skip("shani_chronoa.gui imports cleanly now - drop the shim in this file")
    assert "could not create new GType" in result.stderr, result.stderr


# -- negative control -------------------------------------------------------


def test_forget_doing_nothing_must_fail_the_row_count(store, monkeypatch):
    """A Forget that deletes nothing has to break the assertion meant to catch it.

    `store.forget` is made a no-op that reports success, which is the exact
    failure a "Forget" button can have: the handler runs, the page rebuilds, and
    the row is still there. The row count is asserted to *fail* while the button
    is patched, the store's own contents are checked so the failure is the
    rendering's and not an emptied fixture's, and then the patch is released and
    the count is asserted again - an unrestored control is a change left behind
    for the next reader.
    """
    store.add(_fact("memory", "the meeting is at 4pm in the annex"))
    store.add(_fact("memory", "prefers dark roast coffee"))
    widget = surface.build(_app(store))

    def _row_count():
        widget.refresh()
        rows = _marked(widget, surface.FACT_CSS)
        assert len(rows) == 1, f"expected one row left, found {len(rows)}: {_labels(widget)}"
        return len(rows)

    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(store, "forget", lambda predicate: 0)
        _forget(_marked(widget, surface.FACT_CSS)[0]).emit("clicked")
        assert len(store.durable()) == 2, "the fixture was emptied, so this proves nothing"
        assert len(_durable_lines(store)) == 2, _durable_lines(store)
        with pytest.raises(AssertionError):
            _row_count()

    _forget(_marked(widget, surface.FACT_CSS)[0]).emit("clicked")
    assert _row_count() == 1
    assert len(_durable_lines(store)) == 1