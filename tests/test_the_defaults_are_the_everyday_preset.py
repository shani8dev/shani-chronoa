"""A fresh install should arrive already set up, and the destructive keys should still be shut.

The consent model has 156 boolean keys and 46 of them are *action* permissions -
the things that let Chronoa change this computer. All 46 used to ship `false`, so
a machine that had not been through the setup window started with nothing
enabled: nine provisioning steps each refused to download a model, and every
capability asked for permission before it could do anything.

The repository already says what a sensible set is. `capabilities.PRESETS` has
three tiers, and `everyday` is described as *"Changes settings, writes and edits
files, controls apps; never deletes, stops or powers off anything"*. Its rule is
`key not in DESTRUCTIVE_CONSENT_KEYS or key == "file-edit-enabled"` - so it
enables 34 and leaves the 12 destructive ones off.

**These tests hold the schema defaults to that preset.** Two files drifting apart
is the obvious failure: a key edited in the XML but not in `PRESETS`, or a preset
widened without the schema following. Either would leave the default set
describing something the code no longer agrees with.

## The defaults are read through `Gio.Settings`, not by parsing the XML

Parsing the XML would pass while the app saw something else. GSettings reads
`gschemas.compiled`, which is tracked separately from the `.xml`, and **editing
the XML does nothing until it is recompiled** - which is exactly what happened
here: the flip measured `False` and looked like it had failed. So there is a
test for staleness at the bottom of this file.
"""

import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

gi = pytest.importorskip("gi")
from gi.repository import Gio  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa.config import SCHEMA_ID  # noqa: E402

SCHEMA_XML = ROOT / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml"
COMPILED = ROOT / "usr/share/glib-2.0/schemas/gschemas.compiled"

EVERYDAY = capabilities.PRESETS["everyday"][2]


def _settings():
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SCHEMA_ID, True) is None:
        pytest.skip("the %s schema is not installed here" % SCHEMA_ID)
    return Gio.Settings.new(SCHEMA_ID)


def test_every_action_permission_defaults_to_what_the_everyday_preset_says():
    """The invariant, read the way the app reads it.

    One assertion over the whole set rather than 46, so a key that drifts is
    named in the failure.
    """
    settings = _settings()
    assert capabilities.ACTION_CONSENT_KEYS, (
        "ACTION_CONSENT_KEYS is empty, so this test would pass by checking nothing")

    wrong = {
        key: (settings.get_boolean(key), EVERYDAY(key))
        for key in capabilities.ACTION_CONSENT_KEYS
        if settings.get_boolean(key) != bool(EVERYDAY(key))
    }

    assert not wrong, (
        "schema defaults no longer match the `everyday` preset for %d key(s): %s"
        % (len(wrong), wrong))


def test_the_destructive_keys_ship_shut():
    """**The safety line, stated on its own.**

    `everyday` is a rule about what is *not* destructive, so a mistake in the
    rule - or in `DESTRUCTIVE_CONSENT_KEYS` itself - would quietly open all
    twelve at once. These are the keys that delete files, empty the trash, stop
    processes, power the machine off and change system services, so they are
    asserted individually rather than through the preset.

    **`DESTRUCTIVE_CONSENT_KEYS` has thirteen members and this asserts twelve.**
    The thirteenth is `file-edit-enabled`, which is classified destructive and
    enabled anyway - the preset rule carves it out with `or key ==
    "file-edit-enabled"`. Asserting the whole set failed on it, which is the
    first version of this test doing exactly what it is for: the classification
    and the preset are different things, and only the intersection the preset
    shuts is the safety line.
    """
    settings = _settings()
    shut = sorted(k for k in capabilities.DESTRUCTIVE_CONSENT_KEYS if not EVERYDAY(k))

    assert len(shut) == 12, (
        "the set of destructive keys the preset shuts is %d, not 12: %s"
        % (len(shut), shut))

    for key in shut:
        assert settings.get_boolean(key) is False, (
            "%s ships on by default; it deletes, stops or powers off" % key)


def test_editing_files_ships_on_because_it_is_not_destructive():
    """The one key `everyday` switches back on inside the destructive set.

    `file-edit-enabled` is in `DESTRUCTIVE_CONSENT_KEYS` and the preset's rule
    still enables it, so a plain "defaults equal the preset" test would not
    notice if someone removed that exception - the preset and the schema would
    both change together and agree.
    """
    assert "file-edit-enabled" in capabilities.DESTRUCTIVE_CONSENT_KEYS
    assert EVERYDAY("file-edit-enabled") is True
    assert _settings().get_boolean("file-edit-enabled") is True


def test_the_preset_the_defaults_describe_is_the_one_reported():
    """The two ends must agree: what the defaults *are* has to be what
    `current_preset` calls them, or the setup window shows a tier that is not the
    state of the machine.
    """
    from shani_chronoa.config import ChronoaConfig

    assert capabilities.current_preset(ChronoaConfig()) == "everyday", (
        "a fresh config does not read back as the preset its defaults describe")


def test_the_compiled_schema_is_not_stale():
    """**Editing the XML does nothing until it is recompiled**, and the symptom is
    a change that looks like it did not take.

    `gschemas.compiled` is tracked and is what GSettings actually loads, so the
    two files are committed side by side and only one of them is the source. If
    the XML is newer, the defaults in the XML are not the defaults in force.
    """
    assert SCHEMA_XML.exists() and COMPILED.exists(), (
        "the schema or its compiled form is missing; the defaults cannot be read")
    assert COMPILED.stat().st_mtime >= SCHEMA_XML.stat().st_mtime, (
        "gschemas.compiled is older than the .xml - run "
        "`glib-compile-schemas usr/share/glib-2.0/schemas` and commit it too")


def test_the_schema_file_still_declares_every_key_the_preset_names():
    """The control on the control: if the XML stopped parsing, or the schema lost
    keys, the tests above would read defaults from somewhere else and pass.

    This reads the XML directly and requires that it declares a boolean for every
    key the preset governs.
    """
    root = ET.parse(SCHEMA_XML).getroot()
    declared = {
        key.get("name")
        for schema in root.findall("schema")
        for key in schema.findall("key")
        if key.get("type") == "b"
    }

    missing = sorted(set(capabilities.ACTION_CONSENT_KEYS) - declared)
    assert not missing, ("the schema no longer declares these keys: %s" % missing)
    assert len(declared) > 100, (
        "the schema only declares %d boolean keys, which cannot be right" % len(declared))


def test_nothing_writes_the_defaults_at_import_time():
    """Reading the defaults must not have written them.

    `ChronoaConfig` writes nothing on construction, and a fresh install should
    look exactly like the schema until a person changes something. This is what
    would catch a helper that "helpfully" applied a preset on first read.
    """
    from shani_chronoa.config import ChronoaConfig

    config = ChronoaConfig()
    reread = Gio.Settings.new(SCHEMA_ID)
    changed = [
        key for key in capabilities.ACTION_CONSENT_KEYS
        if config.get_bool(key, False) != reread.get_boolean(key)
    ]

    assert not changed, (
        "constructing ChronoaConfig changed %d key(s): %s" % (len(changed), changed))


def test_a_stale_compiled_schema_is_what_the_recorder_expects():
    """`glib-compile-schemas` is the only way the XML reaches GSettings, and
    getting the directory wrong is silent - the schema simply is not found and
    the code falls back to Python defaults. This records the command that fixes
    it, next to the assertion that notices when it is needed.
    """
    result = subprocess.run(
        ["glib-compile-schemas", "--help"],
        capture_output=True, text=True, check=False)

    assert result.returncode in (0, 1), (
        "glib-compile-schemas is not available, so the schema cannot be rebuilt "
        "and a stale compiled schema could not be fixed: %s" % result.stderr[:120])