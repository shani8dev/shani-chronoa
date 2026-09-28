"""The settings window has to be buildable and complete, verified by building it.

A GTK bug that only appears at construction is invisible to a unit test that
reads the file, and this repo has shipped exactly that class: a `Gtk.Button`
never wired to anything, an API removed in GTK4, a handler that silently never
ran. So every assertion here constructs a real `SettingsWindow` against a real
`Gtk.Application` and walks the constructed tree.
"""

import subprocess
import sys
import textwrap

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.config import _SENSE_CONSENT_KEYS  # noqa: E402

# Run in a child process: building a Gtk.Application in-process leaves a
# main loop and a display connection behind that upset every later test.
_HARNESS = textwrap.dedent(
    """
    import json, pathlib, sys
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    sys.path.insert(0, '/home/shrinivaskumbhar/Documents/shani/shani-chronoa/usr/lib/shani-chronoa')
    from shani_chronoa.config import ChronoaConfig

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="test.chronoa.settingswindow")
            self.config = ChronoaConfig()
            self.window = None
            self._wake_word_active = False
        def activate_action(self, name, arg):
            pass

    def walk(node, out):
        child = node.get_first_child()
        while child:
            walk(child, out)
            out.append(child)
            child = child.get_next_sibling()
        return out

    app = App()
    result = {}

    def on_activate(a):
        from shani_chronoa.settings_window import SettingsWindow
        w = SettingsWindow(a)
        nodes = walk(w.get_child(), [])
        result["groups"] = [g.get_title() for g in nodes if isinstance(g, Adw.PreferencesGroup)]
        result["switch_titles"] = [r.get_title() for r in nodes if isinstance(r, Adw.SwitchRow)]
        result["active"] = [r.get_title() for r in nodes
                            if isinstance(r, Adw.SwitchRow) and r.get_active()]
        result["switch_count"] = sum(1 for n in nodes if isinstance(n, Adw.SwitchRow))
        result["entry_count"] = sum(1 for n in nodes if isinstance(n, Adw.EntryRow))
        result["search_placeholder"] = w._search.get_placeholder_text()
        result["default_size"] = (w.get_default_size()[0], w.get_default_size()[1])

        groups = [g for g in nodes if isinstance(g, Adw.PreferencesGroup)]
        rows = {r.get_title(): r for r in nodes if isinstance(r, Adw.SwitchRow)}
        w._search.insert_text("thermal", 0)

        def check():
            result["visible_groups"] = [g.get_title() for g in groups if g.get_visible()]
            result["thermal_visible"] = rows["thermal"].get_visible()
            result["contention_visible"] = rows["contention"].get_visible()
            a.quit()
            return False

        GLib.timeout_add(1200, check)

    app.connect("activate", on_activate)
    GLib.timeout_add(20000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(result))
    """
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import os
    import json

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Its own config/data home. Without this the harness inherits whatever the
    # developer's real dconf has, and the "only memory starts enabled" claim
    # fails for anyone who has ever enabled a sense - which is exactly the
    # state a person reading this test would be in.
    work = tmp_path_factory.mktemp("sw")
    env["XDG_CONFIG_HOME"] = str(work / "config")
    env["XDG_DATA_HOME"] = str(work / "data")
    (work / "config").mkdir()
    (work / "data").mkdir()
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(work),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, f"harness produced no result:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    payload["_stderr"] = proc.stderr
    return payload


class TestSettingsWindowIsComplete:
    def test_every_sense_is_grantable_from_the_gui(self, built):
        """The gap this replaced: 17 consent keys, 0 switches.

        A user who is told a sense is off had no way to turn it on except a
        terminal. Generated from the registry, so a sense added tomorrow shows
        up without anyone editing the window.
        """
        missing = [n for n in sorted(_SENSE_CONSENT_KEYS)
                   if n not in built["switch_titles"]]
        assert missing == [], (
            f"these senses have a consent key but no switch in the settings "
            f"window, so they cannot be granted from the GUI: {missing}"
        )

    def test_only_memory_starts_enabled_among_the_senses(self, built):
        """Everything else is fail-closed on a fresh install, and the window
        must not make a denied sense look available."""
        senses = set(_SENSE_CONSENT_KEYS)
        on = [t for t in built["active"] if t in senses]
        assert on == ["memory"], f"unexpectedly-enabled senses: {on}"

    def test_rows_are_real_adwaita_widgets(self, built):
        """The previous version hand-rolled Gtk.Box rows: no group semantics,
        no expected keyboard navigation, no accessibility roles."""
        assert built["switch_count"] >= len(_SENSE_CONSENT_KEYS)
        assert built["entry_count"] > 0

    def test_it_is_organised_into_groups(self, built):
        for expected in ("Senses", "Privacy and network", "Voice", "Models", "System"):
            assert expected in built["groups"], f"missing group: {expected}"

    def test_there_is_a_search_entry(self, built):
        assert built["search_placeholder"]


class TestSearchActuallyFilters:
    def test_typing_narrows_the_window(self, built):
        """Verified through a real Gtk.SearchEntry with a real typed string.

        `set_text()` does not emit `search-changed`, so an earlier check of
        this appeared to show search was simply broken. It is not - it needs
        the signal the widget emits while a person types.
        """
        assert built["visible_groups"] == ["Senses"], (
            "searching 'thermal' should leave only the group containing a "
            f"matching row, got {built['visible_groups']}"
        )

    def test_a_matching_row_inside_a_group_survives(self, built):
        """The group title does not contain the needle, but a row inside it
        does - hiding the group would bury the only match."""
        assert built["thermal_visible"] is True
        assert built["contention_visible"] is False


class TestNoHardcodedTheme:
    def test_the_window_uses_the_desktop_theme(self):
        """The old window forced `background-color: #1a1a2e`, so a user with a
        light theme got a dark dialog that fought Adwaita."""
        import pathlib

        source = pathlib.Path(
            "usr/lib/shani-chronoa/shani_chronoa/settings_window.py"
        ).read_text()
        # The literal survives in the docstring, describing the bug. What must
        # not survive is an executable stylesheet.
        assert "CssProvider" not in source
        assert "load_from_data" not in source
        assert "add_provider_for_display" not in source

    def test_no_widget_is_given_two_parents(self, built):
        """A HeaderBar set as the window titlebar *and* appended to the content
        box is a second parent, and GTK rejects it at construction with
        `gtk_widget_get_parent (child) == NULL`."""
        assert "gtk_box_append" not in built["_stderr"], (
            "the window produced a GTK parenting assertion:\n"
            + built["_stderr"][-1500:]
        )
