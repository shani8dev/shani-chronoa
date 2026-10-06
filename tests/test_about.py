"""The shortcuts and about windows, as built widgets under a real application.

Every accelerator the shortcuts window shows is checked against the ones the
app really registers, read out of the package source with `ast`. That is the
whole point of the file: a shortcut window listing a shortcut the app does not
have is worse than none, because it is a confident wrong answer, and every
other check here (it imports, it constructs, it has three sections) would pass
just as happily if it did.

The windows are constructed in a child process, for the reason
`test_adw_initialisation.py` gives: GTK holds a display connection and
libadwaita holds global state, and neither can be set up and torn down per test
inside one interpreter.
"""

import ast
import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

PKG = pathlib.Path(__file__).resolve().parent.parent / "usr/lib/shani-chronoa/shani_chronoa"
REPO = pathlib.Path(__file__).resolve().parent.parent
#: The Arch manifest that actually ships this package is the one in the sibling
#: `shani-pkgbuilds` repo, which builds from this tree by pinned commit. This
#: repo used to carry a second, drifting copy that nothing built from.
PKGBUILD_PATH = REPO.parent / "shani-pkgbuilds" / "shani-chronoa" / "PKGBUILD"


# --- what the app really registers ------------------------------------------

def _const(node):
    """A string literal, or None for anything that is not one.

    An f-string or a concatenation is deliberately *not* folded: this parser
    exists to prove the table matches the source, and a clever source should
    fail the check rather than be guessed at.
    """
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def registrations():
    """`(accels, actions)` as the app's own source declares them.

    `accels` maps an action name to the accelerators bound to it;
    `actions` is every `Gio.SimpleAction.new(...)` name, without the `app.`
    prefix.
    """
    accels: dict = {}
    actions: set = set()
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr == "set_accels_for_action" and len(node.args) == 2:
                    action = _const(node.args[0])
                    keys = node.args[1]
                    if action is None or not isinstance(keys, (ast.List, ast.Tuple)):
                        continue
                    bound = [k.value for k in keys.elts if isinstance(k, ast.Constant)]
                    if len(bound) == len(keys.elts):
                        accels.setdefault(action, []).extend(bound)
                elif _dotted(func.value) == "Gio.SimpleAction" and node.args:
                    name = _const(node.args[0])
                    if name is not None:
                        actions.add(name)
    return accels, actions


ACCELS, ACTIONS = registrations()
REAL_ACCELS = {a for bound in ACCELS.values() for a in bound}


# --- the windows, built under a real Gtk.Application ------------------------

_HARNESS = textwrap.dedent(
    """
    import json, sys
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib

    if not Gtk.init_check():
        print("RESULT" + json.dumps({"skipped": "no display available to GTK"}))
        sys.exit(0)

    # Spied on before the import, because that is the only place the call can
    # still be caught: `Adw.init()` has to have run by the time the first Adw
    # widget is constructed, and a widget that missed it renders nothing.
    adw_init_calls = []
    real_adw_init = Adw.init

    def spy_adw_init():
        adw_init_calls.append("Adw.init")
        return real_adw_init()

    Adw.init = spy_adw_init
    from shani_chronoa.gui import AboutWindow, ShortcutsWindow
    Adw.init = real_adw_init
    out = {"adw_init_calls_at_import": len(adw_init_calls)}

    def walk(node, out):
        out.append(node)
        child = node.get_first_child()
        while child is not None:
            walk(child, out)
            child = child.get_next_sibling()
        return out

    app = Gtk.Application(application_id="test.chronoa.about.windows")

    def on_activate(a):
        shortcuts = ShortcutsWindow(a)
        shortcuts.present()
        sections = []
        for node in walk(shortcuts, []):
            if not isinstance(node, Adw.PreferencesGroup):
                continue
            rows = []
            for row in walk(node, []):
                if isinstance(row, Adw.ActionRow):
                    # The fallback's shape: a real row with getters for both.
                    get_accel = getattr(row, "get_accelerator", None)
                    accel = get_accel() if callable(get_accel) else ""
                    # The label is the widget's own, so read it where it is.
                    for sub in walk(row, []):
                        getter = getattr(sub, "get_accelerator", None)
                        if callable(getter) and (getter() or ""):
                            accel = getter()
                    rows.append({
                        "title": row.get_title() or "",
                        "subtitle": row.get_subtitle() or "",
                        "accelerator": accel or "",
                    })
                    continue
                # The native dialog's shape, which is what runs on the
                # installed libadwaita 1.9. Its rows are `AdwShortcutRow`,
                # which is **not introspectable by name** on this binding -
                # `Adw.ShortcutRow` raises AttributeError - so it is matched on
                # the class name the tree actually reports. It exposes
                # `get_title()` but **no `get_subtitle()`**, and the subtitle
                # the app set on `Adw.ShortcutsItem` is rendered as the second
                # label in the row, so that is where it is read. Measured: a row
                # for "Settings" carries ['Settings', 'Every setting...', 'Ctrl',
                # ',']; a keyless row carries the title, its explanation, and
                # libadwaita's own "No Shortcut" - so "the row explains itself"
                # holds on this path too.
                if type(row).__name__ != "AdwShortcutRow":
                    continue
                labels = [
                    sub.get_label() for sub in walk(row, [])
                    if type(sub).__name__ == "Label" and (sub.get_label() or "").strip()
                ]
                accel = ""
                for sub in walk(row, []):
                    if type(sub).__name__ != "ShortcutLabel":
                        continue
                    # A keyless row still builds a ShortcutLabel; libadwaita
                    # labels it "No Shortcut". Only a real accelerator counts,
                    # or every row would look bound.
                    candidate = sub.get_accelerator() or ""
                    if candidate and candidate != "No Shortcut":
                        accel = candidate
                        break
                rows.append({
                    "title": row.get_title() or "",
                    "subtitle": labels[1] if len(labels) > 1 else "",
                    "accelerator": accel,
                })
            if rows:
                sections.append({"title": node.get_title() or "", "rows": rows})
        # The native dialog brings its own chrome, and both of these are
        # `Adw.PreferencesGroup` like a real section is. Measured: an untitled
        # empty group (the search field's container) and an untitled group of
        # **all 15** rows - its search-results view, which repeats every
        # shortcut in the window a second time. Counting either would double
        # the rows and add a fourth "section" that the app never declared, so
        # only the declared titles are kept. Filtered by the table rather than
        # by "is the title empty" so a genuinely untitled declared section would
        # still be caught.
        from shani_chronoa.gui.about import SHORTCUT_SECTIONS as _declared

        wanted = {s.title for s in _declared}
        out["sections"] = [s for s in sections if s["title"] in wanted]

        about = AboutWindow(a)
        about.present()
        out["about"] = {
            "is_gtk_window": isinstance(about, Gtk.Window),
            "title": about.get_title() or "",
            "program_name": about.get_program_name() or "",
            "version": about.get_version() or "",
            "licence": int(about.get_license_type()),
            "licence_name": about.get_license_type().value_nick,
            "website": about.get_website() or "",
            "copyright": about.get_copyright() or "",
            # The list of strings that went in, one entry per author. This used
            # to be `"".join(...)` over a comment claiming `get_authors()`
            # returns a list of single characters in this PyGObject; measured,
            # it returns the list unchanged. Joining it would now produce
            # "Shrinivas Vishnu Kumbharthe Shanios authors" - one run-on name -
            # and every assertion below would fail for the wrong reason.
            "authors": list(about.get_authors() or []),
            "icon": about.get_logo_icon_name() or "",
            "comments": about.get_comments() or "",
        }

        def after():
            out["shortcuts_mapped"] = shortcuts.get_mapped()
            out["about_mapped"] = about.get_mapped()
            out["shortcuts_size"] = [shortcuts.get_width(), shortcuts.get_height()]
            out["about_size"] = [about.get_width(), about.get_height()]
            print("RESULT" + json.dumps(out))
            a.quit()
            return False

        GLib.timeout_add(400, after)

    app.connect("activate", on_activate)
    GLib.timeout_add(25000, lambda: (print("RESULT" + json.dumps(out)), app.quit(), False)[2])
    app.run([])
    """
)


@pytest.fixture(scope="module")
def built():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(PKG.parent)
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(REPO),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, (
        f"the real application produced no result:\n{proc.stdout}\n{proc.stderr[-3000:]}"
    )
    if payload.get("skipped"):
        pytest.skip(payload["skipped"])
    return payload


# --- the module ---------------------------------------------------------------

def test_the_module_imports():
    module = pytest.importorskip("shani_chronoa.gui.about")
    assert module.SHORTCUT_SECTIONS


def test_the_gui_package_reexports_both_windows():
    from shani_chronoa import gui

    assert gui.AboutWindow.__module__.endswith("gui.about")
    assert gui.ShortcutsWindow.__module__.endswith("gui.about")


def test_libadwaita_is_initialised_before_any_widget_is_built(built):
    """The silent failure this repo has already paid for: an Adw widget built
    before `Adw.init()` renders nothing, with no error to notice.

    Checked by counting the calls made during the import, not by looking for
    `Adw.StyleManager` - that attribute exists whether or not the library was
    initialised, so an assertion on it cannot fail, which is the mistake
    `test_adw_initialisation.py` documents twice in its own docstring.
    """
    assert built["adw_init_calls_at_import"], (
        "importing shani_chronoa.gui.about did not call Adw.init(), so every "
        "Adw widget in these windows is built before libadwaita is ready"
    )


def test_each_window_constructs_under_a_real_application(built):
    assert built["sections"], "the shortcuts window produced no sections"
    assert built["about"]["version"], "the about window built with no version"


def test_both_windows_really_map(built):
    """Constructing a widget is not the same as it appearing: an
    `Adw.AboutWindow` put inside another window constructs cleanly and then
    never maps at 0x0."""
    assert built["shortcuts_mapped"], f"shortcuts window did not map: {built['shortcuts_size']}"
    assert built["about_mapped"], f"about window did not map: {built['about_size']}"


# --- the shortcuts -----------------------------------------------------------

def test_the_source_parser_finds_the_real_registrations():
    """Guards the guard: an `ast` parser that matched nothing would make the
    membership tests below pass for the wrong reason."""
    assert ACCELS.get("app.quit") == ["<Ctrl>Q"]
    assert ACCELS.get("app.open-settings") == ["<Ctrl>comma"]
    assert ACCELS.get("app.stop-speaking") == ["Escape"]
    assert {"toggle-listening", "stop-speaking", "quick-ask", "setup"} <= ACTIONS


def test_the_three_sections_are_there_and_non_empty(built):
    titles = [section["title"] for section in built["sections"]]
    assert titles == ["General", "Conversation", "This window"]
    for section in built["sections"]:
        assert section["rows"], f"section {section['title']!r} has no rows"


def test_every_accelerator_listed_is_really_registered(built):
    listed = {
        row["accelerator"]
        for section in built["sections"]
        for row in section["rows"]
        if row["accelerator"]
    }
    assert listed, "the window lists no accelerator at all, so this proves nothing"
    unknown = listed - REAL_ACCELS
    assert not unknown, (
        f"the shortcuts window shows {sorted(unknown)}, which "
        f"set_accels_for_action never registers. Real ones: {sorted(REAL_ACCELS)}"
    )


def test_every_real_accelerator_is_listed(built):
    """The other direction: an accelerator the app binds and the window does
    not mention is a shortcut nobody can find."""
    listed = {
        row["accelerator"]
        for section in built["sections"]
        for row in section["rows"]
        if row["accelerator"]
    }
    assert REAL_ACCELS - listed == set()


def test_every_row_names_an_action_that_exists(built):
    from shani_chronoa.gui.about import SHORTCUT_SECTIONS

    named = {s.action for section in SHORTCUT_SECTIONS for s in section.shortcuts}
    assert named
    assert not named - ACTIONS, f"rows name actions the app never registers: {named - ACTIONS}"
    # And the rows on screen are these rows: the table is what got built.
    on_screen = {row["title"] for section in built["sections"] for row in section["rows"]}
    assert on_screen == {s.title for section in SHORTCUT_SECTIONS for s in section.shortcuts}


def test_an_action_with_no_key_says_so(built):
    """Empty is a fact - the app binds no accelerator for it - and the row has
    to state it rather than showing a shortcut that does nothing."""
    rows = [row for section in built["sections"] for row in section["rows"]]
    unbound = [row for row in rows if not row["accelerator"]]
    assert unbound, "every row claims a key; the orb and the conversations list have none"
    assert all(row["subtitle"] for row in unbound)


def test_a_fake_accelerator_would_be_rejected(built):
    """The negative control, with the same predicate the real test uses.

    `<Ctrl><Alt>F9` is bound to nothing anywhere in this repo, so if the check
    above could not fail, this is where it shows.
    """
    listed = {
        row["accelerator"]
        for section in built["sections"]
        for row in section["rows"]
        if row["accelerator"]
    }
    fake = "<Ctrl><Alt>F9"
    assert fake not in REAL_ACCELS, "the control is not a control: it is really registered"
    assert (listed | {fake}) - REAL_ACCELS == {fake}


# --- the about ---------------------------------------------------------------

def test_the_about_window_reports_the_repos_real_licence(built):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    licence = PKGBUILD_PATH.read_text(encoding="utf-8")
    assert "license=('GPL-3.0-only')" in licence
    assert "GNU GENERAL PUBLIC LICENSE" in (REPO / "LICENSE").read_text(encoding="utf-8")
    assert built["about"]["licence"] == int(Gtk.License.GPL_3_0)
    assert built["about"]["licence_name"] == "gpl-3-0"


def test_the_about_window_reports_the_manifests_version_and_url(built):
    from shani_chronoa import __version__

    pkgbuild = PKGBUILD_PATH.read_text(encoding="utf-8")
    assert f"pkgver={__version__}" in pkgbuild
    assert built["about"]["version"] == __version__
    # The About window's website is the **project** repository, NOT the
    # manifest's `url=`. This test used to assert they were the same, which
    # made the About window link to `shani-pkgbuilds/tree/main/shani-chronoa` -
    # the packaging directory - because that is what a package's url= means.
    # A person clicking "website" wants the program's source; a person reading
    # the PKGBUILD wants the packaging tree. Asserting the distinction is the
    # point; asserting equality would just re-encode the confusion.
    manifest_url = [line for line in pkgbuild.splitlines()
                    if line.startswith("url=")][0].split("=", 1)[1].strip('"')
    assert built["about"]["website"] == "https://github.com/shani8dev/shani-chronoa"
    assert built["about"]["website"] != manifest_url, (
        "the About window is linking to the packaging repository again"
    )
    assert built["about"]["program_name"] == "Shani Chronoa"
    assert built["about"]["icon"] == "shani-chronoa"
    assert built["about"]["is_gtk_window"]


def test_the_about_window_names_its_real_developers(built):
    pkgbuild = PKGBUILD_PATH.read_text(encoding="utf-8")
    maintainer = [ln for ln in pkgbuild.splitlines() if ln.startswith("# Maintainer:")][0]
    # Name only: `authors` is a list of names, so the "name\nemail"
    # pair form would interleave with the declared entry.
    name = maintainer.split(": ", 1)[1].split("<", 1)[0].strip()
    assert name in built["about"]["authors"]
    assert "the Shanios authors" in (REPO / "LICENSE").read_text(encoding="utf-8")
    assert "the Shanios authors" in built["about"]["authors"]
    assert built["about"]["copyright"], "an about window with no copyright line"


def test_the_about_window_says_what_this_is(built):
    comments = built["about"]["comments"]
    assert len(comments) > 200, "a one-line about dialog is not a 'what this is' section"
    for expected in ("whisper.cpp", "llama.cpp", "MCP", "not somewhere else"):
        assert expected in comments, f"{expected!r} is missing from the about text"