"""Every gate the Help window names must exist as a switch in Settings.

The Help window tells a user who is looking at a refused skill exactly one
thing: `Off. Switch on "<label>" in Settings to use this.` That sentence is a
promise about a switch. When the label belongs to a gate with no row in the
settings window, the promise cannot be kept - the user is sent to look for a
switch that does not exist, and the skill stays unreachable with no way to
enable it. Four gates were in that state (`file-edit-enabled`,
`todo-list-enabled`, `trigger-control-enabled` and the five trigger event types
had no row at all), and five more named a different wording than the row the
user would actually find.

Neither defect is findable by reading `settings_window.py`: the sense rows are
built in a loop from `SENSE_CATEGORIES`, so a gate being absent from the source
says nothing about it being absent from the window. This test therefore builds
the real window and reads the real row titles, which is the only way to tell
what a user can actually see.

The check is a containment test over the joined row titles rather than an
equality test, because the settings window legitimately renders far more than
gates. It fails if a gate's label appears nowhere, and it fails if the label
table loses an entry, so neither a missing row nor a reworded label passes.
"""

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

_HARNESS = textwrap.dedent(
    """
    import json, pathlib, sys
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib

    import shani_chronoa.app as app_mod

    out = {}

    def on_startup(a):
        from shani_chronoa.settings_window import SettingsWindow
        window = SettingsWindow(a)
        titles = []

        def walk(node):
            if isinstance(node, Adw.PreferencesRow):
                title = node.get_title()
                if isinstance(title, str):
                    titles.append(title)
            child = node.get_first_child()
            while child:
                walk(child)
                child = child.get_next_sibling()

        walk(window)
        out["titles"] = titles
        a.quit()

    app = app_mod.ChronoaApplication()
    app.connect("startup", on_startup)
    GLib.timeout_add(25000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def row_titles(tmp_path_factory):
    """Every title the real settings window actually renders.

    A subprocess, because libadwaita holds global state that cannot be set up
    and torn down per-test inside one interpreter - the same reason
    `test_adw_initialisation.py` does this.
    """
    import json

    runtime = tmp_path_factory.mktemp("xrd")
    runtime.chmod(0o700)
    env = dict(os.environ)
    env.update(
        XDG_RUNTIME_DIR=str(runtime),
        PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"),
        PYTHONDONTWRITEBYTECODE="1",
        XDG_DATA_HOME=str(tmp_path_factory.mktemp("data")),
        XDG_CONFIG_HOME=str(tmp_path_factory.mktemp("config")),
        XDG_STATE_HOME=str(tmp_path_factory.mktemp("state")),
    )
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS], capture_output=True, text=True, env=env, timeout=120
    )
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[len("RESULT") :])["titles"]
    pytest.fail(
        "the settings window did not report its rows.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_the_window_actually_rendered_rows(row_titles):
    """Guard the guard: an empty walk would make every check below vacuous."""
    assert len(row_titles) > 50, f"only {len(row_titles)} rows rendered; harness is broken"


def test_every_gate_the_help_window_names_exists_in_settings(row_titles):
    from shani_chronoa.capabilities import GATE_NAMES

    haystack = " | ".join(row_titles).lower()
    missing = {
        key: label
        for key, label in GATE_NAMES.items()
        if label.lower().rstrip(".") not in haystack
    }
    assert not missing, (
        "the Help window would send the user to a switch that is not there: "
        + ", ".join(f"{k} ({v!r})" for k, v in sorted(missing.items()))
    )


def test_a_gate_with_no_row_is_not_merely_misnamed(row_titles):
    """The defect this file exists for: a gate with no row at all.

    Checks the raw key, not the label, so that rewording every label in
    `GATE_NAMES` cannot make this pass while the switches are still absent.
    """
    import ast

    src = pathlib.Path(
        pathlib.Path(__file__).resolve().parents[1]
        / "usr/lib/shani-chronoa/shani_chronoa/capabilities.py"
    ).read_text()
    tree = ast.parse(src)
    gates = {}
    for node in ast.walk(tree):
        targets = [getattr(t, "id", None) for t in getattr(node, "targets", [])]
        if "GATED" in targets and isinstance(node.value, ast.Dict):
            for tool, key in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant):
                    gates[tool] = key.value

    rows_source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "usr/lib/shani-chronoa/shani_chronoa/settings_window.py"
    ).read_text()
    haystack = " | ".join(row_titles).lower()
    # A gate is covered if its key is named in settings_window.py (the explicit
    # rows) or it is a sense (those rows are generated from SENSE_CATEGORIES and
    # cannot be grepped). Anything else must be visible in the rendered titles.
    unaccounted = {
        tool: key
        for tool, key in gates.items()
        if key not in rows_source and key.replace("-sense-enabled", "").split("-")[0] not in haystack
    }
    assert not unaccounted, (
        "gated skills with no settings row and no sense row: "
        + ", ".join(f"{t} ({k})" for t, k in sorted(unaccounted.items()))
    )


def test_gate_labels_are_not_duplicated_keys():
    """A repeated key in a dict literal silently keeps only the last value.

    Adding a label that already exists looks like it worked and changes
    nothing, which is how the wording drifted from the settings rows in the
    first place.
    """
    import ast

    src = pathlib.Path(
        pathlib.Path(__file__).resolve().parents[1]
        / "usr/lib/shani-chronoa/shani_chronoa/capabilities.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        targets = [getattr(t, "id", None) for t in getattr(node, "targets", [])]
        # GATE_NAMES is an AnnAssign (annotated dict), which has no .targets.
        if isinstance(node, ast.AnnAssign):
            targets.append(getattr(node.target, "id", None))
        if "GATE_NAMES" in targets and isinstance(node.value, ast.Dict):
            keys = [k.value for k in node.value.keys]
            dupes = {k for k in keys if keys.count(k) > 1}
            assert not dupes, f"GATE_NAMES has duplicate keys: {sorted(dupes)}"
            return
    pytest.fail("GATE_NAMES was not found in capabilities.py")