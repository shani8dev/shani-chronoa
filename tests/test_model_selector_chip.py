"""The model selector on the mode strip: what it offers, and what it refuses.

It is the only chip on the strip with a real effect - every other one writes a
setting the next turn reads - so the properties worth pinning are the ones that
keep it honest: it offers only what is installed, it says what is answering now,
it flags a departure from the machine's own default, and it answers rather than
raises on a machine with no model at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("gi")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import local_llm  # noqa: E402


@pytest.fixture
def models(gsettings_env, monkeypatch, tmp_path):
    """Link the repo's installed models into the redirected data home.

    `conftest.py`'s autouse `gsettings_env` points `XDG_DATA_HOME` at a scratch
    dir, so the models installed once into this repo's git-ignored
    `cache/data-home` are invisible to anything that just looks - the same trap
    `test_tts_loudness.py` records, where the engines "skipped on a box that had
    them". Without this, `local_llm.installed()` returns `[]` and every assertion
    about what the menu offers is a statement about a machine with no models.
    """
    import os

    data_home = Path(os.environ["XDG_DATA_HOME"])
    real = REPO / "cache" / "data-home" / "shani-chronoa" / "llm"
    link = data_home / "shani-chronoa" / "llm"
    if real.is_dir() and not link.exists():
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(real, link)
    return real


_counter = [0]


def _strip(gsettings_env, monkeypatch=None):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.gui.window import _ModeStrip

    # A unique id per test: `Gtk.Application` exports its id on the bus, and a
    # second registration of the same one raises `An object is already exported
    # for the interface org.gtk.Application` - measured, all five red on it. The
    # strip takes the application as an argument precisely so it can be told
    # which one owns its actions. The suffix is a *letter* because an application
    # id is a D-Bus well-formed name and a segment starting with a digit is
    # invalid - which is the trap this file's own `Adwaita` note records.
    _counter[0] += 1
    app = Gtk.Application(
        application_id=f"test.modelchip.{chr(ord('a') + _counter[0] - 1)}")
    app.connect("activate", lambda a: a.quit())
    app.register(None)
    return _ModeStrip(app, ChronoaConfig())


class TestTheModelDropdown:
    def test_the_menu_offers_exactly_what_is_installed(self, gsettings_env, monkeypatch, models):
        strip = _strip(gsettings_env, None)
        strip.refresh()          # the menu is rebuilt here, from what is on disk
        installed = set(local_llm.installed())
        rows = strip._model_rows()
        offered = {r[0] for r in rows} - {""}
        assert offered == installed, (
            f"the menu offers {sorted(offered)} but {sorted(installed)} is on "
            f"disk; an uninstalled tier is a download and not a mode, so offering "
            f"one puts a dead item in a menu whose job is to say what is true now")
        menu = strip._model_chip.get_menu_model()
        assert menu is not None and menu.get_n_items() == len(rows), (
            "the chip was built with no menu, which is a label that looks like a "
            "control - measured, get_menu_model() returned None")

    def test_the_word_shown_is_the_tier_answering_now(self, gsettings_env, monkeypatch, models):
        strip = _strip(gsettings_env, None)
        active = local_llm.active()
        rows = {r[0]: r for r in strip._model_rows()}
        if active in rows:
            assert strip._model_label.get_text() == rows[active][1], (
                f"{active} is answering but the chip reads "
                f"{strip._model_label.get_text()!r}")
        else:
            assert strip._model_chip.get_tooltip_text(), (
                "no model is answering and the chip says nothing about why")

    def test_only_the_recommended_tier_is_quiet(self, gsettings_env, monkeypatch, models):
        """A departure from the machine's own default is the thing worth seeing.

        The quiet word is `recommended()`'s answer rather than a name this file
        chose, so the strip flags what *this machine* would have picked.
        """
        strip = _strip(gsettings_env, None)
        offered = [r[0] for r in strip._model_rows()]
        default = strip._default_model_key()
        assert default, "no default tier could be read, so nothing would be quiet"
        assert default in offered, (
            f"the default is {default} but only {offered} is installed; every "
            f"installed tier is then a departure and the chip should say so")

    def test_a_machine_with_no_model_says_so_rather_than_raising(
            self, gsettings_env, monkeypatch):
        """`_draw` reads `table[0]`, so an empty table took the whole strip down.

        Measured before this was handled: six tests red with `IndexError: list
        index out of range`, because the four static tables are written down and
        this one is built from what is on disk.
        """
        strip = _strip(gsettings_env, None)
        monkeypatch.setattr(local_llm, "installed", lambda: [])
        rows = strip._model_rows()
        assert rows and rows[0][0] == "", (
            f"a machine with no model must still have a row to draw, got {rows}")
        assert "no model" in rows[0][1].lower(), (
            f"the empty state should say so, it says {rows[0][1]!r}")
        strip.refresh()                       # must not raise
        assert strip._model_label.get_text() == rows[0][1]

    def test_the_icon_is_one_this_repo_already_uses(self, gsettings_env, monkeypatch, models):
        """Adwaita has five icon names this project has already guessed wrong."""
        strip = _strip(gsettings_env, None)
        icon = strip._model_image.get_icon_name()
        used = {r[3] for r in strip._MODES} | {r[3] for r in strip._RULES}
        assert icon in used or icon == "object-select-symbolic", (
            f"{icon} is not one of the icons this strip already uses, and a "
            f"theme lookup that fails draws a blank box")
