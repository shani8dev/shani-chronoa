"""Every page has an id, and any of them can be named from outside the window.

These pages were reachable only by holding the mouse: the settings sections by
typing into a search box, the wizard by pressing Next, the main window by
whatever happened to be open. So a notification could not say "open Settings on
Privacy", a keybinding could not, and a test could not - it had to guess at
selectors, which is how one run produced four screenshots of four "different"
sections that were byte-identical while reporting every step green.

The shape is shani-cassini's (`notebook.py`'s `PAGES`/`page_ids()`/`select`,
`--section=`, and a `show-section` action), generalised to three windows.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import pages  # noqa: E402


class _Window:
    """A window that records what it was asked to show, and can refuse."""

    def __init__(self, refuses=()):
        self.shown = []
        self.refuses = set(refuses)
        self.presented = 0

    def show_page(self, page_id):
        if page_id in self.refuses:
            return False
        self.shown.append(page_id)
        return True

    def present(self):
        self.presented += 1


@pytest.fixture(autouse=True)
def clean_registry():
    """The registry is module state; a test that leaks into it breaks the next."""
    saved_pages, saved_aliases = dict(pages._PAGES), dict(pages._ALIASES)
    saved_windows, saved_factories = dict(pages._WINDOWS), dict(pages._FACTORIES)
    pages._WINDOWS.clear()
    pages._FACTORIES.clear()
    yield
    pages._PAGES.clear(); pages._PAGES.update(saved_pages)
    pages._ALIASES.clear(); pages._ALIASES.update(saved_aliases)
    pages._WINDOWS.clear(); pages._WINDOWS.update(saved_windows)
    pages._FACTORIES.clear(); pages._FACTORIES.update(saved_factories)


def test_a_slug_is_derived_from_the_title():
    assert pages.slug("Tool activity") == "tool-activity"
    assert pages.slug("Who said what") == "who-said-what"
    assert pages.slug("") == ""


def test_an_explicit_id_wins_over_the_title():
    pages.register("settings", [("tool-activity", "Activity log")])
    assert pages.page_ids("settings") == ["tool-activity"]
    assert pages.page_titles("settings") == ["Activity log"]


def test_a_retired_id_still_opens_what_absorbed_it():
    """A stored id that opens nothing is worse than one that opens somewhere
    honest - the app looks broken in a way nobody can diagnose from the id."""
    pages.register("settings", [("models", "Models")], aliases={"model": "models"})
    assert pages.resolve("settings", "model") == "models"
    assert pages.resolve("settings", "models") == "models"


def test_aliases_are_per_window_so_ids_cannot_collide():
    pages.register("settings", [("privacy", "Privacy")], aliases={"model": "privacy"})
    pages.register("setup", [("models", "Model")])
    assert pages.resolve("settings", "model") == "privacy"
    assert pages.resolve("setup", "model") is None


def test_an_alias_cycle_is_refused_rather_than_hanging():
    """A click that loops forever is the worst outcome for a rename gone wrong.

    Neither id here is a live page, so the walk genuinely goes round: an earlier
    version of this test registered `a` as a page *and* aliased it to `b`, which
    is an ordinary alias and resolves correctly - a test that passed for the
    wrong reason.
    """
    pages.register("x", [("c", "C")], aliases={"a": "b", "b": "a"})
    assert pages.resolve("x", "a") is None
    assert pages.resolve("x", "c") == "c"


def test_an_unknown_page_opens_nothing():
    pages.register("settings", [("privacy", "Privacy")])
    window = _Window()
    pages.note_window("settings", window)
    assert pages.show("settings:nosuchpage") is False
    assert window.shown == [], "an unknown id opened something anyway"
    assert window.presented == 0, "and presented the window regardless"


def test_an_unknown_window_is_refused():
    assert pages.show("nosuchwindow:privacy") is False


def test_a_bare_id_resolves_only_when_it_is_unambiguous():
    pages.register("settings", [("privacy", "Privacy")])
    pages.register("setup", [("privacy", "Privacy")])
    assert pages.show("privacy") is False, "an ambiguous bare id must not guess"
    pages._PAGES.pop("setup")
    window = _Window()
    pages.note_window("settings", window)
    assert pages.show("privacy") is True
    assert window.shown == ["privacy"]


def test_a_window_that_refuses_is_reported_not_hidden():
    """The registry must not report success for a page the window would not show."""
    pages.register("settings", [("privacy", "Privacy")])
    pages.note_window("settings", _Window(refuses={"privacy"}))
    assert pages.show("settings:privacy") is False


def test_a_closed_window_is_built_through_its_factory():
    """`--show-page=settings:privacy` on a cold start has to open the window."""
    built = []

    def factory(application, config=None):
        built.append((application, config))
        window = _Window()
        pages.note_window("settings", window)
        return window

    pages.register("settings", [("privacy", "Privacy")], factory=factory)
    assert pages.show("settings:privacy", application="APP", config="CFG")
    assert built == [("APP", "CFG")]
    assert pages.show("settings:privacy") is True, "and the built window is remembered"


def test_describe_names_every_window_and_its_aliases():
    pages.register("settings", [("privacy", "Privacy")], aliases={"model": "privacy"})
    text = pages.describe()
    assert "settings: privacy" in text
    assert "model -> privacy" in text


# ---------------------------------------------------------------------------
# the three real windows register themselves
# ---------------------------------------------------------------------------


def _source(module_name: str) -> str:
    from _source import package_source
    return package_source(module_name)


def test_the_settings_window_registers_its_sections():
    """The ids a caller may use are a promise, so they are declared rather than
    scraped from whatever widgets happened to be built."""
    source = _source("settings_window")
    assert "register(" in source and '"settings"' in source
    for section in ("senses", "privacy", "approvals", "tool-activity",
                    "voice", "models", "system"):
        assert f'"{section}"' in source, section


def test_the_wizard_registers_its_pages():
    source = _source("setup_wizard")
    assert '"setup"' in source
    for page in ("welcome", "mode", "cloud-keys", "brain", "model-picker",
                 "ears", "voice", "review", "extras", "done"):
        assert f'"{page}"' in source, page


def test_all_three_windows_answer_to_show_page():
    """One name, three windows - so a caller does not learn three spellings.

    Asserted through the real registry rather than by grepping for `def
    show_page`. The source check passed while **two of the three windows could
    not be addressed at all**: `show_page` existed on the settings window, and
    the wizard's existed as a closure that was never attached to anything, so
    `pages.show("setup:review")` logged "cannot show a page". A method that
    exists in a scope nobody can reach is the dead-control class this repository
    keeps meeting, in its most literal form.

    Building a real wizard and a real settings window is what the registry
    consults, so this fails whenever an id stops resolving - which is how
    `settings:senses` and `settings:privacy` were caught matching no group at
    all despite being the example in `pages.py`'s own docstring.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    Adw.init()
    from shani_chronoa import pages
    from shani_chronoa.gui.window import ChronoaWindow
    from shani_chronoa.settings_window import SettingsWindow

    assert hasattr(ChronoaWindow, "show_page"), "the main window cannot be addressed"
    assert hasattr(SettingsWindow, "show_page"), "settings cannot be addressed"

    # The wizard's `show_page` is attached to the window object rather than
    # declared on a class, because the wizard is a factory and not a type. It is
    # checked where it is made instead - by `test_the_wizard_window_answers_to
    # show_page` below, on a real wizard inside a real main loop.
    for target in ("main:conversation", "settings:senses",
                   "settings:privacy", "setup:review"):
        window_id, page_id = target.split(":", 1)
        assert pages.resolve(window_id, page_id) == page_id, (
            f"{target!r} does not resolve.\n{pages.describe()}\n"
            "A documented destination that opens nothing is worse than one "
            "that was never promised.")


def test_every_declared_settings_id_shows_something():
    """Each id the registry promises must actually reveal groups on the window.

    `resolve()` alone is not enough, and the gap is not hypothetical: it reported
    `settings:senses` and `settings:privacy` as valid while the built window had
    **no group under either id** - the ids were families and the groups were
    titled "Getting started", "Privacy and network" and the rest. So a caller
    naming the example from `pages.py`'s own docstring got `False` from a window
    with fourteen sense rows sitting in it.

    Built for real, so it fails the moment a builder stops claiming its family.
    Mutation run: removing `self._section_family = "senses"` from
    `_build_senses` fails this test.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk
    Adw.init()
    from shani_chronoa import pages
    from shani_chronoa.settings_window import SettingsWindow

    from shani_chronoa.app import ChronoaApplication
    from shani_chronoa.config import ChronoaConfig

    # The real application class, not a stand-in. The window reads `app.window`,
    # `app.config` and `app._wake_word_active`, and a bare `Gtk.Application` with
    # attributes patched on raises `AttributeError` partway through the build -
    # which reads as a broken window when it is a broken probe. Measured: two of
    # those three attributes discovered that way.
    app = ChronoaApplication()
    # Its own bus name would collide with a running Chronoa and with every other
    # test that builds one, so this run claims a private name. Measured without
    # it: registration times out and the window is never activated.
    app.set_property("application-id", "test.chronoa.sections.live")
    app.register()
    app.hold()
    app.window = None
    app.config = ChronoaConfig()
    app._wake_word_active = lambda _a: False
    box = {}

    def step():
        try:
            sw = SettingsWindow(app)
            total = len(sw._searchable)
            rows = {}
            for pid in pages.page_ids("settings"):
                ok = sw.show_page(pid)
                rows[pid] = (ok, sum(1 for g, _h in sw._searchable
                                     if g.get_visible()))
            # An empty needle must put everything back, or a person who
            # searched and then cleared the box is left with a fraction of the
            # window and no way to tell that is not the window.
            sw._search.set_text("camera")
            sw._search.set_text("")
            restored = sum(1 for g, _h in sw._searchable if g.get_visible())
            box["result"] = (total, rows, restored)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        app.release()
        app.quit()
        return False

    GLib.timeout_add(50, step)
    app.run([])
    if "error" in box:
        raise box["error"]
    total, rows, restored = box["result"]
    assert total > 1, f"the window built only {total} group(s); this test proves nothing"
    empty = {pid: n for pid, (ok, n) in rows.items() if not ok or n == 0}
    assert not empty, f"declared ids that show nothing: {empty}\n{pages.describe()}"
    too_many = {pid: n for pid, (ok, n) in rows.items() if n == total}
    assert not too_many, (
        f"ids that hid nothing: {too_many} - a section id that reveals the whole "
        "window is the same as not addressing a section")
    assert restored == total, (
        f"clearing the search box restored {restored} of {total} groups - the "
        "filter was a one-way door")


def test_the_wizard_window_answers_to_show_page():
    """The wizard's `show_page` reaches the window object, not a dead scope.

    Built for real and read back off the returned window. `pages.show()` finds
    a window addressable by `getattr(window, "show_page", None)`, so a closure
    held only by the local scope that built it is invisible to it - which is
    what happened: `setup:review` logged "cannot show a page" on a wizard that
    had the Review page sitting there, fully built.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk
    Adw.init()
    from shani_chronoa import setup_wizard

    app = Gtk.Application(application_id="test.chronoa.wizard.addr",
                           flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register()
    # `hold()` or `run()` returns immediately: a `Gtk.Application` with no window
    # of its own has no reason to stay alive, so the timeout below never fired and
    # the box stayed empty - which reads as "the wizard has no show_page".
    # Measured: with the hold, `has=True` and `show_page('review')` is True.
    app.hold()
    box = {}

    def step():
        try:
            win = setup_wizard.build_window(app)
            box["ok"] = callable(getattr(win, "show_page", None))
            box["review"] = box["ok"] and win.show_page("review")
            box["unknown"] = box["ok"] and win.show_page("no-such-page")
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        app.release()
        app.quit()
        return False

    GLib.timeout_add(50, step)
    app.run([])
    if "error" in box:
        raise box["error"]
    assert box.get("ok"), "the wizard window has no show_page the registry can find"
    assert box.get("review") is True, "show_page('review') refused a real page"
    assert box.get("unknown") is False, \
        "show_page accepted an id that does not exist - a typo in a notification " \
        "must not land on a page nobody named"


def test_the_application_exposes_the_doorway():
    source = _source("app.application")
    assert '"show-page"' in source, "no action"
    assert "--show-page=" in source, "no flag"

def test_starting_a_new_conversation_clears_the_composer():
    """Ctrl+N and `/new` clear the transcript, and they clear the composer too.

    Measured before the fix, on the real window: with a half-typed question in
    the entry, `reset-conversation` emptied the transcript and **left the text
    in the composer**. The next Enter would have sent a question about the
    previous conversation into the new one, with nothing on screen saying so.

    `ChronoaWindow.clear_input()` had existed for this and had no caller, which
    is the unwired-helper class this repository records repeatedly - and here the
    helper was right and the wiring was missing, which is the harder half to see.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib
    Adw.init()
    from shani_chronoa.app import ChronoaApplication

    app = ChronoaApplication()
    app.set_property("application-id", "test.chronoa.reset.composer")
    app.register()
    app.hold()
    box = {}

    def step():
        try:
            window = app.window
            window._input_entry.set_text("half-typed question I never sent")
            before = window.get_input_text()
            app.activate_action("reset-conversation", None)
            box["result"] = (before, window.get_input_text())
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        app.release()
        app.quit()
        return False

    def start():
        app.activate()
        GLib.timeout_add(600, step)
        return False

    GLib.timeout_add(100, start)
    app.run([])
    if "error" in box:
        raise box["error"]
    before, after = box["result"]
    assert before, "the probe did not put text in the composer, so it proves nothing"
    assert after == "", f"a new conversation kept {after!r} in the composer"


def test_slash_help_takes_its_argument_instead_of_discarding_it():
    """`/help what can you do?` fills the composer; bare `/help` opens the list.

    The argument used to be accepted and dropped: `/help what can you do?` opened
    the same window as `/help` and threw away the question the person had
    already typed. `capabilities.help_prompt()` existed to name that prompt and
    had no caller, so the string lived in the capabilities module and nowhere
    else that could reach it.

    Driven through the real command dispatcher, because the dispatcher is what
    splits `/help` from its argument - a test calling `_help` directly cannot see
    whether the two are separated at all.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib
    Adw.init()
    from shani_chronoa import capabilities as caps
    from shani_chronoa.app import ChronoaApplication
    from shani_chronoa.gui import commands

    app = ChronoaApplication()
    app.set_property("application-id", "test.chronoa.help.argument")
    # `NON_UNIQUE` so this application claims no bus name at all. It shares the
    # process with the other tests here, and two GApplications in one process
    # derived from the same id collide on the D-Bus object path as well as the
    # name - measured as `UnknownMethod: No such interface "org.gtk.Actions"` on
    # the *other* test's path, which reads as a broken command rather than as the
    # collision it is. A name would be the wrong fix: this window is not on the
    # bus, so there is nothing to own.
    app.set_property("flags", int(Gio.ApplicationFlags.NON_UNIQUE))
    app.register()
    app.hold()
    box = {}

    def step():
        try:
            window = app.window
            seen = {}
            for line in ("/help what can you do?", "/help make me a sandwich"):
                window.clear_input()
                command, argument = commands.lookup(line)
                assert command is not None, f"{line!r} was not recognised at all"
                seen[line] = command.run(window, argument)
                seen[line + " composer"] = window.get_input_text()
            box["result"] = (seen, caps.help_prompt())
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        app.release()
        app.quit()
        return False

    def start():
        app.activate()
        GLib.timeout_add(600, step)
        return False

    GLib.timeout_add(100, start)
    app.run([])
    if "error" in box:
        raise box["error"]
    seen, prompt = box["result"]
    assert seen["/help what can you do? composer"] == prompt, (
        f"the help prompt was not used: {seen}")
    assert seen["/help make me a sandwich composer"] == "make me a sandwich", (
        f"an explicit argument was rewritten: {seen}")


def test_the_approvals_page_says_when_no_question_will_be_asked():
    """The row reports the mode as well as the presence of a listener.

    Two different questions, and the panel answered only one of them: whether
    somebody is *there* (`can_ask()`), not whether asking is a thing that may
    happen in the mode in force (`allows_prompting()`). Measured on a real
    window, four states:

    | mode | presenter | row said |
    |---|---|---|
    | DEFAULT | installed | "Chronoa will ask..." |
    | DEFAULT | none | "Nobody is listening..." |
    | **DONT_ASK** | **installed** | **"Chronoa will ask..."** |
    | EXPLORE | installed | "Chronoa will ask..." |

    The third row is the defect: `DONT_ASK` answers the question no on the spot
    before any presenter is consulted, so the panel described a policy the app
    does not enforce - on the one page whose whole subject is what the app
    enforces.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib
    Adw.init()
    from shani_chronoa import ask_bridge, permissions
    from shani_chronoa.app import ChronoaApplication
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.settings_window import SettingsWindow

    app = ChronoaApplication()
    app.set_property("application-id", "test.chronoa.approvals.who")
    app.set_property("flags", int(Gio.ApplicationFlags.NON_UNIQUE))
    app.register()
    app.hold()
    app.window = None
    app.config = ChronoaConfig()
    app._wake_word_active = lambda _a: False
    box = {}

    def who_can_answer() -> str:
        # A fresh window per state: the row is built once, from the state at
        # build time, so reusing one would report the first state every time.
        window = SettingsWindow(app)
        for group, _haystack in window._searchable:
            if group.get_title() != "Approvals":
                continue
            for row, _text in group._needle_extra:
                if row.get_title() == "Who can answer":
                    return row.get_subtitle() or ""
        return ""

    def step():
        try:
            said = {}
            for label, mode, listener in (
                    ("default-listener", permissions.Mode.DEFAULT, True),
                    ("default-silent", permissions.Mode.DEFAULT, False),
                    ("dont_ask-listener", permissions.Mode.DONT_ASK, True)):
                permissions.set_mode(mode)
                ask_bridge.set_presenter(
                    (lambda q: "yes") if listener else None)
                said[label] = who_can_answer()
            permissions.set_mode(permissions.Mode.DEFAULT)
            ask_bridge.set_presenter(None)
            box["said"] = said
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        app.release()
        app.quit()
        return False

    GLib.timeout_add(50, step)
    app.run([])
    if "error" in box:
        raise box["error"]
    said = box["said"]
    assert said["dont_ask-listener"], "the row was not found at all"
    assert "will ask" not in said["dont_ask-listener"], (
        "DONT_ASK with a listener still says Chronoa will ask:\n"
        f"  {said['dont_ask-listener']!r}")
    assert "No questions are asked" in said["dont_ask-listener"], (
        f"the DONT_ASK row does not name the mode:\n  {said['dont_ask-listener']!r}")
    assert "will ask" in said["default-listener"], (
        f"the ordinary case lost its wording:\n  {said['default-listener']!r}")
    assert "Nobody is listening" in said["default-silent"], (
        f"the no-listener case lost its wording:\n  {said['default-silent']!r}")


def test_the_diagnostics_panel_reads_sandbox_policy_files(tmp_path, monkeypatch):
    """A policy file nobody reads is a policy file nobody can trust.

    `sandbox/policy.py` documents itself as loadable from a *user-edited* file
    under `~/.config/shani-chronoa/sandbox/`, with `deny_unknown_fields` so a
    misspelled `"tiemout"` cannot silently mean "the default timeout" — and
    `load_policy()` had no caller, so that directory was never read by anything.
    A strict schema with no loader is the same defect as a loadable one nobody
    calls, from the other direction.

    Two files: one that loads, one with the exact typo the schema exists to
    catch. The row must name the key it did not understand, because a policy
    nobody can read is the one case where naming the key *is* the answer.

    The row also has to say what it is not: nothing applies these files, the
    executor is configured from the Python profiles. An assertion on that phrase
    is the point — a panel that implied the policy was in force would be making
    the confident-wrong claim this repository keeps finding.
    """
    import json
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    Adw.init()
    from shani_chronoa.files import config_home
    from shani_chronoa.gui.surfaces import diagnostics

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = config_home() / "shani-chronoa" / "sandbox"

    status, detail = diagnostics._sandbox_policy_files()
    assert status == diagnostics.STATUS_UNKNOWN
    assert str(directory) in detail and "applied by nothing" in detail, \
        "an absent policy directory must say these files change nothing"

    directory.mkdir(parents=True)
    (directory / "tight.json").write_text(json.dumps(
        {"name": "tight", "level": "LEVEL_3_HOST_USER", "timeout_seconds": 10,
         "blocked_binaries": ["curl", "wget"]}))
    (directory / "typo.json").write_text(json.dumps(
        {"level": "LEVEL_3_HOST_USER", "tiemout_seconds": 5}))

    status, detail = diagnostics._sandbox_policy_files()
    assert "tight.json" in detail, detail
    assert "LEVEL_3_HOST_USER" in detail and "10s timeout" in detail, detail
    assert "curl, wget" in detail, f"the blocked binaries are not reported: {detail}"
    assert "typo.json" in detail and "tiemout_seconds" in detail, (
        f"a refused file must name the key it did not understand: {detail}")
    assert status in (diagnostics.STATUS_WORKING, diagnostics.STATUS_UNKNOWN), \
        f"the row used a status word outside its own vocabulary: {status!r}"
