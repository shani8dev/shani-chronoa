"""Every setting the app has is reachable from the window - or is declared not to be.

Two capabilities were terminal-only until recently, and nothing noticed: the
`input-control-enabled` consent gate, which `triggers.py` refuses to act without
so an armed rule silently did nothing, and audio device selection, which the
capture path has always honoured and which therefore looked configurable when it
was not. Both were found by *listing what the app uses* and comparing it with
what the window offers - not by reading the window, and not by any test.

So this is a coverage contract, not a source-text scan. A source scan cannot do
this job: the sense keys are generated from the registry, and most of the
boolean switches bind through a GAction (`toggle-privacy`) whose name never
appears next to the GSettings key it writes. Asserting on file contents would
either miss them or forbid the next person from refactoring.

Adding a key to the schema therefore fails this until someone says where it
belongs - which is the whole point.
"""

import pathlib
import re
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.config import _SENSE_CONSENT_KEYS  # noqa: E402

SCHEMA = pathlib.Path("usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml")

# Keys the window controls through a GAction, an entry row, or a generated
# sense switch. Kept as a declared list because the binding is indirect: a
# source scan would miss every one of these.
CONTROLLED = {
    # switches, reached through a GAction - the action name is what the window
    # calls, so the GSettings key never appears in the file next to it
    "privacy-mode",            # toggle-privacy
    "debug-mode",              # toggle-debug
    "wake-word-enabled",       # toggle-wake-word
    "cloud-fallback-enabled",  # toggle-cloud-fallback
    "barge-in-vad-enabled",    # toggle-barge-in-vad
    "auto-start",              # toggle-auto-start
    "notification-enabled",    # written directly; gates the notify skill
    # the actuator consent gate, written directly
    "input-control-enabled",
    # the destructive-action gates, each with its own row in the actions group
    "file-delete-enabled",
    "process-kill-enabled",
    "window-close-enabled",
    "wifi-connect-enabled",
    "service-control-enabled",
    "bulk-edit-enabled",
    "mount-control-enabled",
    # entry rows
    "model", "vision-model", "whisper-model", "language", "wake-word-model",
    "piper-voice", "ollama-host",
    # the audio device choosers
    "audio-input-device", "audio-output-device",
    # BYOK provider keys
    "anthropic-api-key", "openai-api-key", "google-api-key", "groq-api-key",
    "llm7-api-key", "kilo-api-key", "blockrun-api-key",
}

# Keys deliberately not in the window, each with the reason. A new entry here
# is a decision someone has to write down.
NOT_EXPOSED = {
    "hardware-profile": "shown read-only in 'In effect right now'; it is "
                        "auto-detected, and overriding it by hand is how a "
                        "user ends up with a model too large for the machine",
    "link-sense-enabled": "retired: the `link` sense was merged into "
                          "`network`, which has the row. Its default is false "
                          "so it cannot silently re-grant the network sense and "
                          "make that row's switch do nothing. Still honoured: "
                          "setting it grants `network`",
    "thermal-sense-enabled": "retired: the `thermal` sense was merged into "
                             "`hwmon`, which reports both the hwmon channels "
                             "and the ACPI thermal zones - the kernel exposes "
                             "the same sensors twice and they agree. Still "
                             "honoured: setting it grants `hwmon`",
    "cooling-sense-enabled": "retired: the `cooling` sense was merged into "
                             "`hwmon`. Its fan walk was a second traversal of "
                             "/sys/class/hwmon that hwmon had already done, "
                             "and its liquidctl half moved with it. Still "
                             "honoured: setting it grants `hwmon`",
    "smart-sense-enabled": "retired: the `smart` sense was merged into "
                           "`storage`, which has the row. The key is still "
                           "honoured - setting it grants `storage` - but a "
                           "second row for the same permission would be a "
                           "switch that appears to do nothing when it is "
                           "turned off, because the other one still allows it",
    "camera-sense-enabled": "retired: the `camera` sense was merged into "
                            "`capture`, which has the row. Still honoured - "
                            "setting it grants `capture` - but a second row "
                            "for the same permission would be a switch that "
                            "appears to do nothing when turned off, because "
                            "the other one still allows it",
    "contention-sense-enabled": "retired: the `contention` sense was merged "
                                 "into `capture`, which has the row. Still "
                                 "honoured - setting it grants `capture`",
    "monitors-sense-enabled": "retired: the `monitors` sense was merged into "
                              "`display`, which now has the row. The key is "
                              "still honoured - setting it grants `display` - "
                              "but a second row for the same permission would "
                              "be a switch that appears to do nothing when it "
                              "is turned off, because the other one still "
                              "allows it",
}


def _schema_keys():
    return set(re.findall(r'<key name="([^"]+)"', SCHEMA.read_text()))


def _sense_keys():
    """The 17 consent keys, from the registry rather than by pattern."""
    return set(_SENSE_CONSENT_KEYS.values())


class TestTheWindowCoversTheSchema:
    def test_the_schema_file_is_readable(self):
        assert SCHEMA.exists(), f"no schema at {SCHEMA}"
        assert _schema_keys(), "the schema declares no keys at all"

    def test_every_key_is_accounted_for(self):
        unaccounted = sorted(
            _schema_keys() - _sense_keys() - CONTROLLED - set(NOT_EXPOSED)
        )
        assert not unaccounted, (
            f"these settings exist but the window neither controls them nor "
            f"declares them intentional. Each needs a control or a reason in "
            f"NOT_EXPOSED: {unaccounted}"
        )

    def test_the_declared_keys_all_exist(self):
        """A stale entry is worse than a missing one: it looks like coverage."""
        schema = _schema_keys()
        phantom = sorted((CONTROLLED | set(NOT_EXPOSED)) - schema)
        assert not phantom, (
            f"these are declared as handled but are not in the schema, so the "
            f"declaration has gone stale: {phantom}"
        )

    def test_every_sense_has_a_consent_key_in_the_schema(self):
        missing = sorted(_sense_keys() - _schema_keys())
        assert not missing, (
            f"the registry expects these consent keys and the schema does not "
            f"declare them, so the sense can never be granted: {missing}"
        )


class TestTheWindowActuallyOffersThem:
    """The declaration above is a claim; this builds the window and checks it.

    A declaration that drifts from the code is a lie with a test's name on it,
    which is why the two are in the same file.
    """

    @pytest.fixture(scope="class")
    def built(self):
        import os
        import subprocess

        work = pathlib.Path("/tmp/chronoa-surface-check")
        work.mkdir(exist_ok=True)
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(pathlib.Path("usr/lib/shani-chronoa").resolve())
        env["GSETTINGS_BACKEND"] = "keyfile"
        env["XDG_CONFIG_HOME"] = str(work)
        env["XDG_DATA_HOME"] = str(work)
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        code = (
            "import json, gi\n"
            "gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')\n"
            "from gi.repository import Gtk, Adw, GLib\n"
            "from shani_chronoa.config import ChronoaConfig\n"
            "class A(Gtk.Application):\n"
            "    def __init__(s):\n"
            "        super().__init__(application_id='test.surface.cover')\n"
            "        s.config=ChronoaConfig(); s.window=None; s._wake_word_active=False\n"
            "    def activate_action(s,n,a): pass\n"
            "app=A(); out={}\n"
            "def walk(n,a):\n"
            "    c=n.get_first_child()\n"
            "    while c:\n"
            "        walk(c,a); a.append(c); c=c.get_next_sibling()\n"
            "    return a\n"
            "def on_activate(a):\n"
            "    from shani_chronoa.settings_window import SettingsWindow\n"
            "    w=SettingsWindow(a); nodes=walk(w.get_child(),[])\n"
            "    out['titles']=[r.get_title() for r in nodes if isinstance(r,Adw.PreferencesRow)]\n"
            "    out['sense_rows']=sorted(w._sense_rows)\n"
            "    a.quit()\n"
            "app.connect('activate', on_activate)\n"
            "GLib.timeout_add(25000, lambda:(app.quit(),False)[1])\n"
            "app.run([])\n"
            "print('RESULT'+json.dumps(out))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            timeout=120, env=env,
        )
        import json
        payload = None
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT"):
                payload = json.loads(line[len("RESULT"):])
        assert payload is not None, (
            f"the window did not build:\n{proc.stdout}\n{proc.stderr[-1500:]}"
        )
        return payload

    def test_every_sense_is_present_as_a_row(self, built):
        from shani_chronoa.senses import discover_senses

        missing = sorted(set(discover_senses()) - set(built["sense_rows"]))
        assert not missing, f"senses with no row: {missing}"

    def test_the_actuator_gate_has_a_row(self, built):
        assert any("act on this machine" in t for t in built["titles"]), (
            "input-control-enabled gates every actuator; without a row the only "
            "way to allow one is a terminal"
        )

    def test_the_device_pickers_have_rows(self, built):
        for title in ("Microphone", "Speakers"):
            assert title in built["titles"], f"no row for {title} selection"
