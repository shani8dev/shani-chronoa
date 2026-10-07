"""The reply-style row: Settings must actually carry it into config.

The feature was built as key -> clause -> assistant propagation (verified by
test_reply_style.py), and the row itself could have been a label that looked
right while never writing to the same key the assistant reads. This test builds
the real SettingsWindow, walks the real widget tree to the row, and changes the
selection - the only proof a GTK ComboRow is wired rather than decorative.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

_CODE = textwrap.dedent(
    """
    import json, sys
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    Adw.init()
    sys.path.insert(0, {repo!r})
    from shani_chronoa.config import ChronoaConfig

    rows = {{}}
    combo = {{'found': None, 'model': []}}
    def walk(n):
        c = n.get_first_child()
        while c:
            if isinstance(c, Adw.ComboRow):
                if c.get_title() == "Reply style":
                    items = []
                    for i in range(c.get_model().get_n_items()):
                        items.append(c.get_model().get_item(i).get_string())
                    combo['found'] = c; combo['model'] = items
            walk(c); c = c.get_next_sibling()

    class App(Gtk.Application):
        def __init__(s):
            super().__init__(application_id='test.chronoa.reply.style')
            s.config = ChronoaConfig(); s.window = None; s._wake_word_active = False
        def activate_action(s, n, a): pass

    app = App()
    out = {{}}
    def on_activate(a):
        from shani_chronoa.settings_window import SettingsWindow
        w = SettingsWindow(a)
        walk(w.get_child())
        out['row_found'] = combo['found'] is not None
        out['options'] = combo['model']
        if combo['found'] is not None:
            combo['found'].set_selected(1)   # "brief"
            out['config_after'] = a.config.reply_style
            combo['found'].set_selected(2)   # "explanatory"
            out['config_after_2'] = a.config.reply_style
        a.quit()
    app.connect('activate', on_activate)
    GLib.timeout_add(30000, lambda: (app.quit(), False)[1])
    app.run([])
    print('RESULT' + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def row_result(tmp_path_factory):
    pkg = _REPO / "usr" / "lib" / "shani-chronoa"
    schemas = tmp_path_factory.mktemp("schemas")
    import shutil
    import subprocess as sp

    for xml in (_REPO / "usr" / "share" / "glib-2.0" / "schemas").glob("*.xml"):
        shutil.copy(xml, schemas)
    sp.run(["glib-compile-schemas", str(schemas)], check=True)

    work = tmp_path_factory.mktemp("work")
    (work / "config").mkdir()
    (work / "data").mkdir()
    runtime = work / "runtime"
    runtime.mkdir()
    os.chmod(runtime, 0o700)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(pkg)
    env["GSETTINGS_SCHEMA_DIR"] = str(schemas)
    env["GSETTINGS_BACKEND"] = "keyfile"
    env["XDG_CONFIG_HOME"] = str(work / "config")
    env["XDG_DATA_HOME"] = str(work / "data")
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _CODE.format(repo=str(pkg))],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(work),
    )
    out = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            out = json.loads(line[len("RESULT"):])
    assert out is not None, f"harness produced no result:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    return out


class TestReplyStyleRow:
    def test_the_row_exists_with_the_three_options(self, row_result):
        assert row_result["row_found"], "no 'Reply style' ComboRow in the Settings tree"
        assert row_result["options"] == ["ordinary", "brief", "explanatory"]

    def test_selecting_an_option_writes_the_live_config(self, row_result):
        assert row_result["config_after"] == "brief"
        assert row_result["config_after_2"] == "explanatory"
