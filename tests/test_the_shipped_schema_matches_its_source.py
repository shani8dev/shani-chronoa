"""The shipped schema's defaults are the ones the schema says.

**`usr/share/glib-2.0/schemas/gschemas.compiled` is tracked in git** -
it is what a package installs - and it is a *compiled* file, so it can be
stale against the XML beside it without anything complaining. The failure
is silent: every setting then takes its Python default instead of the
one the schema declares, and the only symptom is a switch that is on when
the schema says off.

So this file reads both and compares them.

**And one default is a decision, not an accident.** `phone-remote-enabled`
is **off**: while this computer advertises "Chronoa Remote", the Find Me
alert is registered as a plain write-without-response characteristic with no
encryption flag, so a device nearby that has not paired with it can write
the alert level and make the machine ring. A key that advertises a
keyboard is worth having; a key that lets anything nearby ring the
computer is not something to hand out without being asked.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
Gio = pytest.importorskip("gi.repository.Gio", reason="GIO is not installed")

SCHEMA_DIR = _REPO / "usr" / "share" / "glib-2.0" / "schemas"
SCHEMA_XML = SCHEMA_DIR / "org.shani.chronoa.gschema.xml"
SCHEMA_COMPILED = SCHEMA_DIR / "gschemas.compiled"


def _xml_defaults() -> dict:
    """`key -> the default the XML declares`, as text."""
    out = {}
    root = ET.parse(SCHEMA_XML).getroot()
    for key in root.iter("key"):
        default = key.find("default")
        if default is not None and default.text is not None:
            out[key.get("name")] = default.text.strip()
    return out


def _same(declared: str, shipped: str) -> bool:
    """Whether the two texts are the same value.

    **The two spell the same value differently** and a raw comparison
    reports five disagreements that are not disagreements: the XML writes a
    string with double quotes and the compiled schema with single ones, a
    double is 0.59999999999999998 in binary, and an integer written into a
    double-typed key comes back as 2048.0. So numbers are compared as
    numbers and strings with their quotes off.
    """
    left = declared.strip().strip('"').strip("'")
    right = shipped.strip().strip('"').strip("'")
    try:
        return float(left) == float(right)
    except ValueError:
        return left == right


def _compiled_defaults() -> dict:
    """`key -> the default the SHIPPED compiled schema carries`."""
    source = Gio.SettingsSchemaSource.new_from_directory(str(SCHEMA_DIR))
    schema = source.lookup("org.shani.chronoa", True)
    assert schema is not None, "the shipped schema directory has no schema"
    out = {}
    for key in schema.list_keys():
        value = schema.get_key(key).get_default_value()
        if value is not None:
            # `print_(False)` does not quote a string, so this compares the
            # same text the XML holds.
            out[key] = value.print_(False)
    return out


class TestTheShippedSchemaMatchesItsSource:
    def test_every_default_is_the_one_the_xml_declares(self):
        """**A compiled schema that is stale against its XML fails
        silently** - every setting takes its Python default, and nothing
        says so."""
        xml = _xml_defaults()
        compiled = _compiled_defaults()
        disagree = {}
        for key, declared in xml.items():
            shipped = compiled.get(key)
            if shipped is None:
                disagree[key] = (declared, "missing from the compiled schema")
            elif not _same(declared, shipped):
                disagree[key] = (declared, shipped)
        assert not disagree, (
            "the shipped gschemas.compiled disagrees with the XML on "
            f"{len(disagree)} key(s): {disagree}")

    def test_no_key_is_missing_from_the_compiled_schema(self):
        """**A key in the XML and not in the compiled schema** takes its
        Python default - which is how `calendar-write-enabled` and
        `fm-radio-enabled` shipped as permanent refusals."""
        xml = _xml_defaults()
        compiled = _compiled_defaults()
        missing = sorted(set(xml) - set(compiled))
        assert not missing, f"the shipped schema is missing {missing}"


class TestThePhoneKeyboardIsOptIn:
    """**`phone-remote-enabled` is off by default**, and the reason is
    measured rather than assumed."""

    def test_the_default_is_off(self):
        """**The ring is reachable without pairing** while this computer
        advertises, so the advertising is opt-in."""
        settings = Gio.Settings.new("org.shani.chronoa")
        assert settings.get_boolean("phone-remote-enabled") is False, (
            "the phone keyboard advertises by default, so a device nearby "
            "that has not paired can make this computer ring")

    def test_the_description_does_not_claim_it_is_on(self):
        """**A description that justifies a default the schema no longer
        has is a stale claim** - and it is the sentence a person reads to
        decide whether the switch is on."""
        text = SCHEMA_XML.read_text(encoding="utf-8")
        block = text.split('name="phone-remote-enabled"', 1)[1].split("</key>", 1)[0]
        assert "On by default" not in block, (
            "the schema's description still says the phone keyboard is on by "
            "default, which is no longer true")
        assert "Off by default" in block, (
            "the schema's description should say the phone keyboard is opt-in, "
            "so a person reading it knows what turning it on does")
