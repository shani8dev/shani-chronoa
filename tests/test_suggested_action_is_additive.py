"""Turning sensing on is consent, so the bulk version of it is held to the same
rule the individual toggles are - and a little more.

Two properties, both of which are invisible in a passing test unless something
checks them:

- **Additive only.** The action must never write `false` to a sense the user
  granted. A "set up Chronoa for me" button that also tidies up is a button
  that can revoke a decision someone made deliberately.
- **Confirmed, and cancellable.** Nothing is written until the dialog is
  answered, and the default response is the refusal.
"""

import os
import subprocess
import sys
import textwrap

import pytest

_HARNESS = textwrap.dedent(
    """
    import json
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    from shani_chronoa.config import ChronoaConfig

    writes = []

    class RecordingConfig(ChronoaConfig):
        def set(self, key, value):
            writes.append((key, value))
            return super().set(key, value)

    class App(Gtk.Application):
        def __init__(s):
            super().__init__(application_id="test.suggest.action")
            s.config = RecordingConfig(); s.window = None; s._wake_word_active = False
        def activate_action(s, name, arg): pass

    app = App()
    out = {}

    def on_activate(a):
        from shani_chronoa.settings_window import SettingsWindow, SUGGESTED
        w = SettingsWindow(a)
        out["has_dialog_response_handler"] = hasattr(w, "_on_suggest_response")

        # Turn one of them on by hand first, so the action has both a missing
        # sense and an already-granted one to get wrong.
        from shani_chronoa.config import _SENSE_CONSENT_KEYS
        a.config.set(_SENSE_CONSENT_KEYS[SUGGESTED[0]], "true")
        writes.clear()

        missing = [n for n in SUGGESTED if not a.config.sense_allowed(n)]
        w._on_suggest_response(None, "apply", missing)

        out["writes"] = writes[:]
        out["all_true"] = all(v == "true" for _k, v in writes)
        out["never_false"] = not any(v == "false" for _k, v in writes)

        # Cancelling must write nothing at all.
        writes.clear()
        w._on_suggest_response(None, "cancel", missing)
        out["cancel_writes"] = writes[:]

        # And a sense the user enabled by hand must still be enabled afterwards.
        out["hand_enabled_survives"] = a.config.sense_allowed(SUGGESTED[0])
        a.quit()

    app.connect("activate", on_activate)
    GLib.timeout_add(25000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def applied(tmp_path_factory, compiled_schema_dir):
    import json
    import pathlib

    work = tmp_path_factory.mktemp("suggest")
    runtime = work / "runtime"
    runtime.mkdir()
    os.chmod(runtime, 0o700)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(pathlib.Path("usr/lib/shani-chronoa").resolve())
    env["XDG_RUNTIME_DIR"] = str(runtime)
    # Same reason as test_missing_settings_surface: the harness builds its own
    # ChronoaConfig in a SUBPROCESS, and without the compiled schema dir it
    # falls back to "using Python defaults" with an empty _valid_keys, so every
    # set()/get_bool() silently no-ops. That made a sense the user had enabled
    # by hand look like the bulk action had switched it back off.
    env["GSETTINGS_SCHEMA_DIR"] = str(compiled_schema_dir)
    # A keyfile backend, for the same reason the sibling harness sets one: there
    # is no session bus on a runner, and dconf writes fail SILENTLY without one.
    # That is what this file was still hitting - the hand-enabled value was
    # written and simply never landed, so the test reported that the bulk action
    # had switched it back off. Reading it locally hid it, because a dev machine
    # has the bus that makes dconf work.
    env["GSETTINGS_BACKEND"] = "keyfile"
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(work),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, (
        f"no result:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    )
    return payload


class TestTheBulkAction:
    def test_it_writes_something(self, applied):
        assert applied["writes"], "the action wrote nothing at all"

    def test_it_only_ever_enables(self, applied):
        assert applied["never_false"], (
            f"the suggested setup wrote a false: {applied['writes']}"
        )
        assert applied["all_true"]

    def test_cancelling_writes_nothing(self, applied):
        assert applied["cancel_writes"] == [], (
            f"cancelling still wrote {applied['cancel_writes']}"
        )

    def test_it_does_not_revoke_what_the_user_already_turned_on(self, applied):
        assert applied["hand_enabled_survives"], (
            "a sense enabled by hand was turned back off"
        )
