"""Does the infrared switch really appear in the built Settings window?

A key present in `test_settings_surface_is_complete.py`'s CONTROLLED list is a
claim about a table; this builds the real `SettingsWindow` and walks its widget
tree, which is the only thing that distinguishes "the row is written" from "a
user can reach the switch". It follows `tests/test_missing_settings_surface.py`'s
subprocess pattern, because building a SettingsWindow needs a real
Gtk.Application and an activate callback.

The defect this guards is the `calendar_edit` one: a key in the schema, a row in
a table, and no switch on any screen - a permission nobody can grant.
"""
import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

LABEL = "Let Chronoa send and receive infrared"

_HARNESS = textwrap.dedent("""
    import json
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib

    out = {"rows": [], "error": ""}

    def walk(node, acc):
        c = node.get_first_child()
        while c:
            walk(c, acc); acc.append(c); c = c.get_next_sibling()
        return acc

    class App(Gtk.Application):
        # The same three attributes `tests/test_missing_settings_surface.py`'s
        # harness gives its stub: `SettingsWindow` reads each of them, and a stub
        # without them raises inside `activate`, so `quit()` is never reached and
        # the whole run hangs until the timeout instead of failing.
        def __init__(s, cid):
            super().__init__(application_id=cid)
            s.config = None; s.window = None; s._wake_word_active = False
        def activate_action(s, name, arg): pass

    def collect(a):
        try:
            from shani_chronoa.config import ChronoaConfig
            a.config = ChronoaConfig()
            from shani_chronoa.settings_window import SettingsWindow
            w = SettingsWindow(a)
            for r in walk(w.get_child(), []):
                if not isinstance(r, Adw.PreferencesRow):
                    continue
                title = r.get_title() or ""
                # Match the *exact* privacy label. A substring match finds the
                # thermalgrid sense's own "Infrared array" row first - which is
                # the name collision this module is called `ir_remote` to avoid -
                # and then asserts on the wrong row's subtitle.
                if title != "__LABEL__":
                    continue
                try:
                    subtitle = r.get_subtitle() or ""
                except Exception:  # noqa: BLE001 - EntryRow has no subtitle
                    subtitle = ""
                out["rows"].append({
                    "title": title,
                    "subtitle": subtitle[:900],
                    "kind": type(r).__name__,
                    "active": (r.get_active()
                               if isinstance(r, Adw.SwitchRow) else None),
                })
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            import traceback
            out["error"] = traceback.format_exc()[-600:]
        finally:
            a.quit()

    app = App("test.ir.settings.row")
    app.connect("activate", collect)
    # A watchdog: whatever happens above, this run ends.
    GLib.timeout_add_seconds(120, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
"""
).replace("__LABEL__", LABEL)


@pytest.fixture(scope="module")
def built(tmp_path_factory, compiled_schema_dir):
    work = tmp_path_factory.mktemp("irsettings")
    (work / "config").mkdir()
    (work / "data").mkdir()
    (work / "state").mkdir()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(pathlib.Path("usr/lib/shani-chronoa").resolve())
    env["XDG_CONFIG_HOME"] = str(work / "config")
    env["XDG_DATA_HOME"] = str(work / "data")
    env["XDG_STATE_HOME"] = str(work / "state")
    env["GSETTINGS_SCHEMA_DIR"] = str(compiled_schema_dir)
    env["GSETTINGS_BACKEND"] = "keyfile"
    proc = subprocess.run([sys.executable, "-c", _HARNESS], env=env,
                          capture_output=True, text=True, timeout=300)
    line = [l for l in proc.stdout.splitlines() if l.startswith("RESULT")]
    assert line, (
        f"the window harness produced no RESULT line:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(line[0][len("RESULT"):])


def test_the_window_built_at_all(built):
    """A harness that cannot build the window cannot test anything in it."""
    assert not built["error"], (
        f"the SettingsWindow could not be built:\n{built['error']}")


def test_the_window_offers_an_infrared_switch(built):
    assert built["rows"], (
        "no row mentioning infrared appears in the built SettingsWindow, so a "
        "user has no way to grant the ir_remote skill - the dead-switch defect")
    row = built["rows"][0]
    assert row["kind"] in ("SwitchRow", "ActionRow"), (
        f"the infrared row is a {row['kind']}, which is not a switch")
    assert row["subtitle"], "the infrared row has no description"


def test_the_row_says_what_it_does(built):
    """The description has to explain the permission, not just name it."""
    row = built["rows"][0]
    subtitle = row["subtitle"]
    for word in ("infrared", "/dev/lirc"):
        assert word in subtitle, (
            f"the infrared row's description does not mention {word!r}, so it "
            "does not tell a user what the switch actually allows")


def test_the_switch_reads_the_stored_value(built):
    """Reading, not writing: toggling tests nothing about the read path.

    **The expected value is read from the schema, not written down here.** It
    used to be a literal `False`, which made this a test of the *default* rather
    than of the read path, and it failed the moment `ir-remote-enabled` was
    correctly turned on - reporting a broken switch where there was none.

    That is the fourth time this repository has encoded a default as an
    assertion instead of a behaviour. A default is a choice; the read path is the
    thing that can have a defect. Comparing against what GSettings says keeps the
    test honest whichever way the default goes.

    The harness matches rows by exact title and the keyfile backend is fresh, so
    the stored value here *is* the schema default.
    """
    row = built["rows"][0]
    if row["kind"] != "SwitchRow":
        pytest.skip("not a switch row")

    from gi.repository import Gio
    from shani_chronoa.config import SCHEMA_ID
    stored = Gio.Settings.new(SCHEMA_ID).get_boolean("ir-remote-enabled")

    assert isinstance(row["active"], bool), (
        f"the switch reported {row['active']!r}, which is not a bool")
    assert row["active"] is stored, (
        "the infrared switch shows %r but the stored value is %r, so the row does "
        "not read what it is bound to" % (row["active"], stored))

