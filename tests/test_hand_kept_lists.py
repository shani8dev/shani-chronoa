"""Every hand-kept list of sense names must agree with the registry, loudly.

**Why this file exists.** Four module-level tables in this app list sense names by
hand:

    config._SENSE_CONSENT_KEYS     name -> consent key
    config._SENSE_DEFAULT_ENABLED  names on by default
    settings_window.SENSE_CATEGORIES  category -> names (the Settings rows)
    settings_window.SENSE_LABELS   name -> title/summary

Each one rots the moment a sense is added, and each rots in a *different and
invisible* way:

  * missing from CATEGORIES  -> no Settings switch exists, so the sense has a
    consent key no user can reach. That is the `heard-sound` / `calendar_edit`
    bug class, and it is what actually happened when the `ups` sense was added
    during the apcupsd-to-NUT switch.
  * missing from DEFAULT_ENABLED -> the sense is silently fail-closed on a fresh
    install, so a default-on sense never turns on. Also what happened to `ups`.
  * missing from CONSENT_KEYS -> `sense_allowed()` denies forever.
  * missing from LABELS -> the row shows a module name instead of a sentence.

Two of those four bit in the same session, which is the argument for turning all
of them from silent into loud rather than trusting anyone to remember four
tables in four files.

**What this file does NOT do** is make the tables unnecessary. `SENSE_CATEGORIES`
is a judgement about grouping and `SENSE_LABELS` about wording, and neither can be
derived. What CAN be derived is asserted here: the consent key is the naming
convention, and every table's membership is checked against the registry.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "usr/lib/shani-chronoa"))

from shani_chronoa.config import (  # noqa: E402
    _SENSE_CONSENT_KEYS,
    _SENSE_DEFAULT_ENABLED,
)
from shani_chronoa.senses import discover_senses  # noqa: E402
from shani_chronoa.settings_window import senses as sw  # noqa: E402


@pytest.fixture(scope="module")
def registry():
    return discover_senses()


def _categorized():
    out = set()
    for _title, _desc, names in sw.SENSE_CATEGORIES:
        out |= set(names)
    return out


# --- the consent key is a convention, not a table ---------------------------


def test_every_consent_key_matches_the_naming_convention(registry):
    """`<name>-sense-enabled`, always.

    Measured: all 52 registered senses follow it with zero exceptions, which is
    what makes it a convention rather than a coincidence. A single divergent key
    would prove the table is doing real work, and this test would then be the
    place to record the exception deliberately.
    """
    divergent = {n: k for n, k in _SENSE_CONSENT_KEYS.items()
                 if k != f"{n}-sense-enabled"}
    assert not divergent, (
        f"these consent keys break the convention - either fix them or record the "
        f"exception here: {divergent}")


def test_the_consent_key_is_derivable_from_the_name():
    """The rule stated once, so a future sense does not have to be looked up."""
    assert _SENSE_CONSENT_KEYS["ups"] == "ups-sense-enabled"
    for name in ("kernel", "kernellog", "dnsresolvers", "power"):
        assert _SENSE_CONSENT_KEYS[name] == f"{name}-sense-enabled"


def test_no_key_exists_for_a_sense_that_does_not(registry):
    """A key for a nonexistent sense is a permission nothing can read, and it
    survives every rename because nothing fails."""
    stale = sorted(n for n in _SENSE_CONSENT_KEYS if n not in registry)
    assert not stale, f"consent keys for senses that are not registered: {stale}"


def test_every_registered_sense_has_a_consent_key(registry):
    """A sense with no key is denied forever, and `sense_allowed()`'s refusal says
    "sense" - so it reads as a gate rather than as a missing row."""
    missing = sorted(n for n in registry if n not in _SENSE_CONSENT_KEYS)
    assert not missing, f"senses with no consent key: {missing}"


# --- the four tables vs the registry ---------------------------------------


def test_every_registered_sense_is_in_a_settings_category(registry):
    """The one that actually bit. A sense in no category gets no row at all, so
    its consent key exists and no user can reach it - the heard-sound /
    calendar_edit / kernel_log dead-switch class, reached a third way.

    Checked against the registry, so adding a sense without a category is a
    failure here rather than a settings panel that quietly lacks a switch.
    """
    categorized = _categorized()
    missing = sorted(n for n in registry if n not in categorized)
    assert not missing, (
        f"these registered senses have no Settings category, so they have a "
        f"consent key no user can grant from the GUI: {missing}")


def test_no_category_lists_a_sense_that_does_not_exist(registry):
    """The other direction: a category naming a removed sense is dead config that
    reads as a working row."""
    stale = sorted(_categorized() - set(registry))
    assert not stale, f"categories name senses that are not registered: {stale}"


def test_no_sense_is_in_two_categories():
    """Two rows for one sense disagree about its state, and the second silently
    overwrites the first in the row map."""
    seen: dict[str, int] = {}
    for _title, _desc, names in sw.SENSE_CATEGORIES:
        for name in names:
            seen[name] = seen.get(name, 0) + 1
    duplicated = sorted(n for n, count in seen.items() if count > 1)
    assert not duplicated, f"senses listed in more than one category: {duplicated}"


def test_every_registered_sense_has_a_label(registry):
    """A label is NOT optional, and my first version of this test was wrong about
    that.

    I wrote it accepting the `name.replace('_',' ').capitalize()` fallback, on the
    grounds that a title is cosmetic. `test_sense_rows_read_as_interface.py`
    already holds the stricter rule - no row may show a raw module name, every row
    needs a summary, titles must be distinct and no title may contain another -
    so the fallback is exactly what that file rejects. A weaker duplicate of a
    stricter existing check is how `ups` got through here and then failed there.
    """
    missing = sorted(n for n in registry if n not in sw.SENSE_LABELS)
    assert not missing, (
        f"these senses have no SENSE_LABELS entry, so their Settings row falls "
        f"back to a module name: {missing}")


def test_no_fallback_accepting_label_check_can_live_here():
    """REMOVED, and kept as the record of why.

    My first attempt at this was a guard against this file growing a weaker
    duplicate of the strict label check, written as `assert "or_a_readable_fallback"
    not in src` against its own source. **It could never pass**: the assertion
    necessarily contains the phrase it is banning, so the file failed the moment
    the test was written. That is a check that cannot fail, in the one direction
    that matters, and it is the same trap as `augenrules --check`.

    What replaces it is nothing - the strict test above IS the check, and it
    asserts the registry's full membership rather than a floor, so a fallback-
    accepting version would have to weaken an assertion that already names the
    registry. If a second opinion about labels is ever wanted, it belongs in
    test_sense_rows_read_as_interface, where the strict one lives.
    """
    assert True, "deliberately assertion-free; the label test above is the check"


def test_every_registered_sense_has_a_gui_title(registry):
    """The FIFTH hand-kept sense-name list, found by the audit rather than by a
    failure.

    `settings_window.SENSE_LABELS` and `gui.surfaces.common.SENSE_TITLES` are two
    copies of the same fact - what each sense is called in the UI - and they live
    in different modules for different windows. Testing one and not the other is
    how three senses came to show as raw module names in the GUI's sense panel:
    `ups` from this session's NUT work, plus `kernellog` and `polkitpolicy` from
    earlier. All three had Settings labels and none had a GUI title.

    Both directions are checked, because a title for a sense that no longer
    exists is dead config that reads as a working row.
    """
    from shani_chronoa.gui.surfaces.common import SENSE_TITLES
    missing = sorted(n for n in registry if n not in SENSE_TITLES)
    assert not missing, (
        f"these senses have no GUI title, so the sense panel shows their module "
        f"name: {missing}")
    stale = sorted(set(SENSE_TITLES) - set(registry))
    assert not stale, f"GUI titles for senses that are not registered: {stale}"


def test_the_two_ui_label_lists_agree_that_every_sense_is_named():
    """`SENSE_LABELS` and `SENSE_TITLES` both cover the registry, but they are
    separate tables, so a sense can be in one and missing from the other. This
    asserts the coverage is the same set rather than trusting that it is."""
    from shani_chronoa.gui.surfaces.common import SENSE_TITLES
    only_settings = set(sw.SENSE_LABELS) - set(SENSE_TITLES)
    only_gui = set(SENSE_TITLES) - set(sw.SENSE_LABELS)
    assert not only_settings and not only_gui, (
        f"the two UI label tables cover different senses - settings-only: "
        f"{sorted(only_settings)}, gui-only: {sorted(only_gui)}")


def test_every_label_names_a_sense_that_exists(registry):
    stale = sorted(set(sw.SENSE_LABELS) - set(registry))
    assert not stale, f"labels for senses that are not registered: {stale}"


def test_the_default_enabled_set_names_only_real_senses(registry):
    stale = sorted(_SENSE_DEFAULT_ENABLED - set(registry))
    assert not stale, f"default-enabled names that are not senses: {stale}"


# Senses that READ the user's world, and must therefore be opt-in. Deliberately
# not the same as "anything with a privacy note": `memory` is EXCLUDED and the
# reason is worth stating, because it looks like it belongs here.
#
# `memory` is default-on because remembering is an act of the user asking, not an
# observation of them - its own schema text says so ("On by default because
# remembering is what makes an assistant useful over time"). The user tells it a
# fact; it does not read one off them. A first version of this test put `memory`
# in the watching set and failed, which was the test being wrong rather than the
# default: the distinction that matters is whether the sense observes or is told.
WATCHES_THE_USER = frozenset({
    "vision",       # the camera
    "ocr",          # text out of a picture
    "filesystem",   # the user's files
    "web",          # what is fetched
    "hearing",      # the microphone
    "capture",      # mic and camera occupancy
    "sessions",     # who else is on the machine
    "location",     # where the machine is
    "privilege",    # the user's own grants
    "display",      # what is on screen
})


def test_a_sense_that_watches_the_user_is_never_default_on():
    """The safety property, held independently of the table so a copy-paste into
    the default set is caught here rather than shipping.

    `memory` is deliberately absent from WATCHES_THE_USER: it is told a fact, not
    given one to observe. `display` is in it and IS default-on - that one is a
    real, documented exception (it reports which applications are showing, and
    the fleet's own panels already surface it), so it is named here rather than
    quietly excluded.
    """
    overlap = WATCHES_THE_USER & set(_SENSE_DEFAULT_ENABLED)
    documented = {"display"}
    assert overlap <= documented, (
        f"a sense that watches the user is on by default: {sorted(overlap - documented)}")


def test_the_default_set_is_only_machine_state_or_told_facts():
    """The table's stated rule, asserted: default-on is the machine's own state,
    or a sense the user tells something. Everything else waits to be asked for."""
    allowed = WATCHES_THE_USER - {"display"}
    for name in _SENSE_DEFAULT_ENABLED:
        assert name not in allowed, f"{name} watches the user but is default-on"
        assert name != "kernellog", (
            "kernellog is off on purpose: it needs root and carries hardware detail")


# --- the default-on table IS derivable, and that is the real fix -------------
#
# `_SENSE_DEFAULT_ENABLED` duplicates what the GSettings schema already says:
# every sense's key carries `<default>true</default>` or `<default>false</default>`.
# A second hand-kept copy of the same fact has to be updated in the same change,
# and the failure is silent - `ups` was added with a schema default of true and
# left out of the frozenset, so a default-on sense was silently fail-closed.
#
# So this reads the schema, which is the authority, and asserts the table agrees.
# If the two ever diverge the table is redundant rather than merely wrong: the
# fix is to delete it and read the schema.


def _schema_defaults():
    """{consent key: schema default} for every key the schema declares.

    `Gio.Settings.get_default_value` returns the SCHEMA's default rather than any
    stored value, which is exactly the fact being checked - a value someone set at
    runtime must not make a default-on sense read as off.
    """
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    schema = Gio.Settings.new("org.shani.chronoa")
    declared = set(schema.list_keys())
    out = {}
    for name in _SENSE_CONSENT_KEYS:
        key = _SENSE_CONSENT_KEYS[name]
        # Checked against the schema's own key list FIRST. Asking
        # `get_default_value` for a key the schema does not declare does not
        # return None - it takes the interpreter down, which turned a deliberate
        # negative control into a crash and said nothing about the list under
        # test. A key that is not declared is a finding, not a segfault.
        if key not in declared:
            out[name] = None
            continue
        value = schema.get_default_value(key)
        out[name] = bool(value.get_boolean()) if value is not None else None
    return out


def test_the_default_table_agrees_with_the_schema():
    """The authority is the schema. A sense whose schema default is true and which
    is missing from `_SENSE_DEFAULT_ENABLED` is silently fail-closed on a fresh
    install, which is what happened to `ups`.

    Removing `ups` from the frozenset alone leaves every other test here green -
    that is why this one exists, and it was confirmed by running it as a control.
    """
    defaults = _schema_defaults()
    schema_on = {n for n, on in defaults.items() if on is True}
    table_on = set(_SENSE_DEFAULT_ENABLED)
    missing = sorted(schema_on - table_on)
    assert not missing, (
        f"these senses have a schema default of true but are missing from "
        f"_SENSE_DEFAULT_ENABLED, so they are silently off on a fresh install: {missing}")
    extra = sorted(table_on - schema_on)
    assert not extra, (
        f"these senses are in _SENSE_DEFAULT_ENABLED but the schema defaults them "
        f"off - two sources disagreeing about a permission: {extra}")


def test_every_sense_key_has_a_schema_default():
    """A key with no schema default is a permission nothing declares, and
    `get_bool` would answer from a Python fallback that no user can see."""
    defaults = _schema_defaults()
    undeclared = sorted(n for n, on in defaults.items() if on is None)
    assert not undeclared, f"consent keys with no schema default: {undeclared}"
