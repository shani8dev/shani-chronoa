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
        # Keyed by sense name, so it is the thing to assert reachability
        # against. Row titles are human-readable now ("Hardware sensors", not
        # "hwmon") and are presentation; this is the binding.
        result["sense_rows"] = sorted(w._sense_rows)
        result["sense_active"] = sorted(
            n for n, r in w._sense_rows.items() if r.get_active()
        )
        result["active"] = [r.get_title() for r in nodes
                            if isinstance(r, Adw.SwitchRow) and r.get_active()]
        result["switch_count"] = sum(1 for n in nodes if isinstance(n, Adw.SwitchRow))
        result["entry_count"] = sum(1 for n in nodes if isinstance(n, Adw.EntryRow))
        result["search_placeholder"] = w._search.get_placeholder_text()
        result["default_size"] = (w.get_default_size()[0], w.get_default_size()[1])

        groups = [g for g in nodes if isinstance(g, Adw.PreferencesGroup)]
        w._search.insert_text("hwmon", 0)

        def check():
            result["visible_groups"] = [g.get_title() for g in groups if g.get_visible()]
            # The haystack the filter itself matches against, read from the
            # window rather than rebuilt here: a list of group titles and
            # descriptions would go stale the moment a group is reworded,
            # and a stale list makes "did the search really match it" a
            # question about the copy rather than about the code.
            result["group_needles"] = [
                # Exactly what `_on_search` matches against: the group's own
                # haystack plus every row needle inside it, so "this group
                # mentions it" is read off the filter's own inputs rather
                # than rebuilt from the widgets.
                (g.get_title(), " ".join(
                    [h for _g, h in w._searchable if _g is g]
                    + [text for _row, text in g._needle_extra]).lower())
                for g in groups
            ]
            # By sense name: "hwmon" is titled "Temperatures, fans and power"
            # is titled "Microphone and camera in use".
            result["needle_visible"] = w._sense_rows["hwmon"].get_visible()
            result["capture_visible"] = w._sense_rows["capture"].get_visible()
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
def built(tmp_path_factory, compiled_schema_dir):
    import os
    import json
    import pathlib

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
    # The harness imports shani_chronoa, so it needs the source tree on its
    # path; the other two harnesses in this suite already set it and this one
    # did not, which is why its eight tests died at
    # "ModuleNotFoundError: No module named 'shani_chronoa'" on a clean
    # checkout and nowhere else.
    env["PYTHONPATH"] = str(pathlib.Path("usr/lib/shani-chronoa").resolve())
    # And the same two gsettings inputs the sibling harnesses need. Without a
    # compiled schema the child's ChronoaConfig falls back to Python defaults
    # with an empty _valid_keys, and set()/get_bool() then return early on every
    # key - writes dropped with no exception, reads answering False. Without a
    # keyfile backend it uses dconf, which fails silently on a runner with no
    # session bus. Either one shows up as "the window does not offer the sense"
    # rather than as a configuration problem.
    env["GSETTINGS_SCHEMA_DIR"] = str(compiled_schema_dir)
    env["GSETTINGS_BACKEND"] = "keyfile"
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
        terminal. Asserted against the sense-keyed row map rather than against
        row titles, because the titles are human-readable by design and are not
        the property: the property is that a switch is bound to each key.
        """
        missing = [n for n in sorted(_SENSE_CONSENT_KEYS)
                   if n not in built["sense_rows"]]
        assert missing == [], (
            f"these senses have a consent key but no switch in the settings "
            f"window, so they cannot be granted from the GUI: {missing}"
        )

    def test_only_the_default_on_senses_start_enabled(self, built):
        """Everything not deliberately enabled is fail-closed on a fresh
        install, and the window must not make a denied sense look available.

        The expected set is read from `_SENSE_DEFAULT_ENABLED` rather than
        hardcoded, so a sense added to that set on purpose is expected here
        rather than fighting this assertion. What is still checked - and what
        this test is really for - is that nothing enables itself: a sense in the
        registry but absent from that table must be off.
        """
        from shani_chronoa.config import _SENSE_DEFAULT_ENABLED
        assert set(built["sense_active"]) == set(_SENSE_DEFAULT_ENABLED), (
            f"active={built['sense_active']}, "
            f"expected={sorted(_SENSE_DEFAULT_ENABLED)}"
        )
        # The senses that observe the room or the machine are opt-in.
        assert not {"vision", "ocr", "filesystem", "web", "camera", "hearing"} & set(
            built["sense_active"]
        ), "a sense that watches the user is enabled by default"

    def test_rows_are_real_adwaita_widgets(self, built):
        """The previous version hand-rolled Gtk.Box rows: no group semantics,
        no expected keyboard navigation, no accessibility roles."""
        assert built["switch_count"] >= len(_SENSE_CONSENT_KEYS)
        assert built["entry_count"] > 0

    def test_it_is_organised_into_groups(self, built):
        """By purpose, not by module name.

        The senses used to be one flat group called "Senses" - the registry
        printed out, which answers nothing about which one to turn on. They are
        now grouped by what someone wants the assistant to be able to do, and
        the non-sense sections are unchanged.
        """
        for expected in (
            "Getting started", "Talking to Chronoa", "Looking at things",
            "Getting work done", "The machine itself", "Security and privacy",
            "Privacy and network", "Voice", "Models", "System",
        ):
            assert expected in built["groups"], f"missing group: {expected}"

    def test_there_is_a_search_entry(self, built):
        assert built["search_placeholder"]


class TestSearchActuallyFilters:
    def test_typing_narrows_the_window(self, built):
        """Verified through a real Gtk.SearchEntry with a real typed string.

        `set_text()` does not emit `search-changed`, so an earlier check of
        this appeared to show search was simply broken. It is not - it needs
        the signal the widget emits while a person types.

        Expected group is derived from the category table rather than written
        out, so regrouping does not make this lie the way a hardcoded name did.

        **Not exactly one group any more, and the second one is honest.** The
        `temperatures` skill shares the `hwmon` sense's consent key - the
        rule `sense_reading.py` states - so the Approvals group lists
        `hwmon-sense-enabled` and genuinely mentions the needle. Asserting
        exclusivity here would have been a test of a coincidence: it passed
        while no other group contained the string, and would have failed for
        a *correct* reason. So the assertion is that the owner group survives,
        that the window narrowed a long way, and that every extra survivor
        really does contain the needle (see
        `test_every_surviving_group_really_contains_the_needle`).
        """
        from shani_chronoa.settings_window import SENSE_CATEGORIES

        owner = next(
            title for title, _d, names in SENSE_CATEGORIES if "hwmon" in names
        )
        assert owner in built["visible_groups"], (
            f"searching 'hwmon' hid the group holding that sense ({owner!r}); "
            f"got {built['visible_groups']}"
        )
        assert len(built["visible_groups"]) < 3, (
            f"'hwmon' narrowed the window to {built['visible_groups']} - the "
            f"search is not filtering"
        )

    def test_every_surviving_group_really_contains_the_needle(self, built):
        """The other half: a survivor that does not mention it would be a
        group the search failed to hide, which reads as a match."""
        for title, haystack in built["group_needles"]:
            if "hwmon" in haystack:
                assert title in built["visible_groups"], (
                    f"{title!r} mentions 'hwmon' and was hidden"
                )
            elif title in built["visible_groups"]:
                raise AssertionError(
                    f"{title!r} survived a 'hwmon' search without containing it"
                )

    def test_a_matching_row_inside_a_group_survives(self, built):
        """The group title does not contain the needle, but a row inside it
        does - hiding the group would bury the only match."""
        assert built["needle_visible"] is True
        assert built["capture_visible"] is False


class TestNoHardcodedTheme:
    def test_the_window_uses_the_desktop_theme(self):
        """The old window forced `background-color: #1a1a2e`, so a user with a
        light theme got a dark dialog that fought Adwaita."""
        import pathlib

        from _source import package_source
        source = package_source("settings_window")
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
