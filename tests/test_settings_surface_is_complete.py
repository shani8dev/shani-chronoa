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
    "speech-rate", "end-of-speech-pause",  # spin rows in the Voice group
    "follow-up-enabled", "sound-cues-enabled",  # switches in the Voice group
    "voice-style",             # a ComboRow in the Voice group ("Voice"): the named
                              # character, applied by SoX in the same pass as
                              # tts-pitch/tempo/rate, which stay as their own rows
    "organ-audible-enabled",   # "Say what it is doing", same group; the tone the
                              # organ strip plays, which is why it sits with the
                              # other sound cues rather than in Privacy
    "sound-sense-enabled",    # a switch in the Privacy group (privacy.py): the
                              # sound sense, which has to be there and not here
    # the Voice output group: the neural voice's opt-in switch and SoX's three
    # timbre controls, all built by `_build_voice_output`
    "kokoro-tts-enabled", "tts-pitch", "tts-tempo", "tts-rate",
    "model-download-enabled",   # toggle-model-download
    # switches, reached through a GAction - the action name is what the window
    # calls, so the GSettings key never appears in the file next to it
    "privacy-mode",            # toggle-privacy
    "debug-mode",              # toggle-debug
    "wake-word-enabled",       # toggle-wake-word
    "cloud-fallback-enabled",  # toggle-cloud-fallback
    # The two cloud-voice gates, added 2026-10-06 with `cloud_voice.py`. Rows in
    # the Privacy group, next to the fallback switch rather than in Voice,
    # because they are privacy controls in the same sense it is - and because
    # they are deliberately *not* `cloud-fallback-enabled`: that one says prompts
    # may leave as text, and a person who agreed to that has not agreed to
    # upload a recording. Two keys because recording and speaking are two
    # separate disclosures and a person may well allow one and not the other.
    "cloud-stt-enabled", "cloud-tts-enabled",
    # The inbound channel, added 2026-10-06. An EntryRow and a live status row in
    # the Privacy group. It had no row at all until then, which is the whole
    # reason `gateway.py` had never been reachable: `_export_gateways()` ran at
    # startup, found an always-empty registry and returned.
    "gateways",
    "barge-in-vad-enabled",    # toggle-barge-in-vad
    "auto-start",              # toggle-auto-start
    "start-hidden-at-login",   # toggle-start-hidden
    "reply-style",             # the ComboRow in the System group
    "notification-enabled",    # written directly; gates the notify skill
    # the actuator consent gate, written directly
    "input-control-enabled",
    "document-search-enabled", "calendar-read-enabled", "calendar-sense-enabled",
    # The write half of the calendar, beside the read half above. It has its own
    # row in privacy.py; before that row landed the key existed in the schema and
    # nowhere else, so `calendar_edit` refused every call with an instruction to
    # enable a switch the window did not have.
    "calendar-write-enabled",
    "phone-control-enabled", "phone-sense-enabled", "global-shortcut-enabled", "radio-control-enabled",
    # The two halves of talking to a phone, each its own row in privacy.py.
    # They were named in `phone`'s own description and checked by the skill
    # before this existed in the schema, so reading and sending refused every
    # call with an instruction to enable a switch that was not there.
    "phone-messages-read-enabled", "phone-messages-send-enabled",
    # Reading a device's own attributes - a watch battery, a heart-rate reading.
    # Added to the schema, this row and the skill in one go, after two other
    # skills in the same batch shipped naming switches that did not exist.
    "bluetooth-gatt-enabled",
    # The two pushes the watch companion makes on its own initiative, each its
    # own row beside `watch-companion-enabled` in privacy.py. They were
    # unconditional before, and they are the only traffic the companion sends
    # that the watch did not ask for - now-playing being two writes every five
    # seconds for as long as the watch is held.
    "watch-nowplaying-enabled", "watch-weather-enabled",
    "nfc-enabled", "fm-radio-enabled", "watch-companion-enabled", "home-place", "phone-remote-enabled", "bluetooth-media-remote-enabled",
    "bluetooth-call-enabled",
    "background-mode-enabled",
    "global-shortcut",  # the trigger string; changed in the desktop's own keyboard settings
    # the six desktop/device trigger gates, each a row in the automatic-rules group
    "screenlock-sense-enabled", "powerstate-sense-enabled", "netstate-sense-enabled",
    "usbplug-sense-enabled", "btconnect-sense-enabled", "schedule-sense-enabled",
    "sleepwake-sense-enabled", "audiodevice-sense-enabled", "journalmatch-sense-enabled", "dbusprop-sense-enabled",
    # the destructive-action gates, each with its own row in the actions group
    "file-delete-enabled",
    "process-kill-enabled",
    "window-close-enabled",
    "power-control-enabled",
    "app-install-enabled",
    "wifi-connect-enabled",
    "network-provision-enabled",
    "packet-capture-enabled",  # row in the actions group, worded so it does
                            # not read as more of the network sense
    # `interface_counters` is NOT listed here: it is ungated by design -, beside WiFi: reads
                                  # like WiFi, but it builds new interfaces and
                                  # needs the machine password, so it is its own
                                  # agreement rather than a wider WiFi switch
    "service-control-enabled",
    "bulk-edit-enabled",
    "mount-control-enabled",
    "bluetooth-control-enabled",
    "mic-control-enabled",
    "screen-lock-enabled",
    "trash-empty-enabled",
    "appearance-control-enabled",
    "timezone-control-enabled",
    "default-apps-enabled",
    "print-control-enabled",
    "hostname-control-enabled",
    "locale-control-enabled",
    "speed-test-enabled",
    "idle-timeout-enabled",
    "sleep-inhibit-enabled",
    # `file-edit-enabled` backs two rows (edit_file, undo_last_change), so it
    # is one key listed once - not a duplicate to prune.
    "file-edit-enabled",
    "todo-list-enabled",
    "goals-enabled",           # Privacy switch beside the task list (manage_goals)
    "trigger-control-enabled",
    # entry rows
    "model", "vision-model", "whisper-model", "language", "wake-phrase",
    "piper-voice", "ollama-host",
    # Read by `stt.build_stt`, not written by a row. Listed here rather than in
    # NOT_EXPOSED because it is *meant* to be user-settable - the whole point
    # of the Parakeet backend - and the window row for it belongs to the
    # settings-surface work that owns the voice-input panel, not to the commit
    # that added the backend. Until that row lands it is still grantable by
    # key: `gsettings set org.shani.chronoa stt-backend parakeet`.
    "stt-backend",
    # the audio device choosers
    "audio-input-device", "audio-output-device",
    # BYOK provider keys
    "anthropic-api-key", "openai-api-key", "google-api-key", "groq-api-key", "opencode-zen-api-key", "openrouter-api-key",
    "llm7-api-key", "kilo-api-key", "blockrun-api-key",
    # custom OpenAI-compatible endpoint rows in the Privacy page
    "custom-llm-base-url", "custom-llm-model", "custom-llm-api-key",
}

# Keys deliberately not in the window, each with the reason. A new entry here
# is a decision someone has to write down.
NOT_EXPOSED = {
    "kokoro-voice": "chosen on the setup window's Voice page, in the same list as the Piper voices, "
                    "where choosing one also downloads Kokoro; Settings has the Kokoro on/off switch",
    "extra-languages": "chosen on the setup window's More page, which installs each language's "
                       "reading data and voice at the same time; a text row could name a "
                       "language with nothing installed for it",
    "setup-mode": "records which branch the setup window offered first - local "
                  "or cloud - and is written by the window's own Mode page. It "
                  "is not a switch: setting it by hand here would change what a "
                  "*future* setup offers without changing anything about how "
                  "this machine answers, which is the exact confusion the two "
                  "real gates (privacy-mode, cloud-fallback-enabled) exist to "
                  "avoid. Settings has \"Open setup\" instead.",
    "setup-dismissed": "set when the setup window is closed unfinished; Settings has \"Open setup\" instead",
    "setup-complete": "set by the setup window's own finish button; Settings offers "
                      "\"Open setup\" instead of a switch, because ticking it by hand "
                      "would only hide a setup that has not been done",
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
                              "but a second row for the same permission would be "
                              "a switch that appears to do nothing when turned "
                              "off, because the other one still allows it",
    # The five trigger event-type gates, added 2026-09-30 alongside
    # `config._EVENT_CONSENT_KEYS`. Listed here as *unaccounted for*, not as
    # decided-against: they have no row in the window yet, because
    # `settings_window.py` was out of scope for the change that registered
    # them. They are the one loose end that change left - each needs a row in
    # the automatic-rules group next to `trigger-control-enabled`.
    #
    # Until then they are still reachable and still named: every refusal from
    # `triggers.py` quotes the key, and `gsettings set org.shani.chronoa
    # fswatch-sense-enabled true` opens it. What is missing is the switch, so
    # a user who is told to enable one has to open a terminal to do it.
    "fswatch-sense-enabled": "no row yet: the per-event-type trigger gates were "
                             "registered in the same change that made the five "
                             "inert event types armable, and `settings_window.py` "
                             "was not in scope for it. Grantable by key, named "
                             "in every refusal; needs a switch in the "
                             "automatic-rules group",
    "failure-sense-enabled": "no row yet: see `fswatch-sense-enabled`",
    "expiry-sense-enabled": "no row yet: see `fswatch-sense-enabled`",
    "containerrun-sense-enabled": "no row yet: see `fswatch-sense-enabled`",
    "unithealth-sense-enabled": "no row yet: see `fswatch-sense-enabled`",
    "sandbox-seccomp-enabled": "no row yet: the seccomp syscall filter "
                               "(`sandbox/seccomp.py`) is on by default and "
                               "off-switchable by key. It has no row because "
                               "the change that added it was scoped to the "
                               "sandbox layer and `settings_window.py` was out "
                               "of scope, exactly as with the five trigger "
                               "gates above - and for the same reason it is "
                               "listed rather than quietly added: a switch "
                               "whose side effect is that a machine starts "
                               "refusing every skill call on a kernel that "
                               "cannot filter is a decision a user should make "
                               "deliberately. Grantable by key and named in "
                               "every refusal (`gsettings set "
                               "org.shani.chronoa sandbox-seccomp-enabled "
                               "true`); needs a row in the security group",
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
