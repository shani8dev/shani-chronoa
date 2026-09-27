"""Support code for `test_sense_manifest.py`: paths, constants, and predicates.

Split out so the test module holds only assertions. Everything here is
derived from the shipped code at call time - the gschema XML is parsed, the
registry is introspected, the loader's own validator is called - never from a
hardcoded list of known sense names, so adding a sense without doing its
wiring is a red test rather than a silent omission.

The per-sense invariants are expressed as pure predicates that return the
list of senses *violating* them, rather than asserting inline. That is what
lets a negative control feed a hand-built registry straight in and see the
identical verdict the real test computes, without duplicating a single
assertion.

Hermeticity: the `registry` fixture points `senses._USER_SENSES_DIR` at
`os.devnull` so the registry under test is exactly the builtin senses -
user drop-ins are resolved at import time from `$HOME`, so they must be
redirected rather than merely ignored. Everything else the tests touch is
isolated by `tests/conftest.py` (per-test `XDG_CONFIG_HOME` and `HOME`,
`GSETTINGS_BACKEND=keyfile`, a temp `GSETTINGS_SCHEMA_DIR`, and
`SHANI_CHRONOA_KEYRING=0`), so nothing here reads or writes the real user's
GSettings, dconf store, or Secret Service keyring.
"""

import os
import site
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "usr/lib/shani-chronoa"
SENSES_PKG_DIR = PKG_DIR / "shani_chronoa/senses"
SCHEMA_XML = REPO_ROOT / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml"
SENSE_CLI = REPO_ROOT / "usr/bin/shani-chronoa-sense"

# `ChronoaConfig.sense_allowed()` builds its key with exactly this suffix.
CONSENT_SUFFIX = "-sense-enabled"

# The five sense consent keys this repo ships, with the default each is
# documented to have: every sense is opt-in except memory, which is on
# because "remembering is what makes an assistant useful over time"
# (the key's own gschema description). Checked against the *compiled*
# schema, not just the XML, so a silently discarded schema cannot fake it.
EXPECTED_SENSE_KEY_DEFAULTS = {
    "vision-sense-enabled": False,
    "ocr-sense-enabled": False,
    "filesystem-sense-enabled": False,
    "web-sense-enabled": False,
    "memory-sense-enabled": True,
}

# Only the memory module may declare durable percepts. `PerceptStore.add`
# routes `ttl_seconds is None` to the on-disk tier, so this is a data-
# retention boundary, not a style rule: a durable screen or filesystem
# sense writes a permanent record of the user's activity to disk.
#
# Expressed as the declaring *module* rather than a list of sense names,
# so it keeps holding when the memory module grows a fourth sense, and so
# it cannot be satisfied by registering a durable sense under a name that
# happens to look memory-ish. A user drop-in is deliberately rejected too:
# durability is a repo-level safety invariant, and a dropped-in file is
# exactly the thing that should not be able to grant itself a disk tier.
DURABLE_MODULE = "shani_chronoa.senses.memory"

# A numeric TTL this long is an oversight rather than a decision: a screen
# grab, an OCR dump or a fetched page is not context an hour later, and
# `ContextBuilder` ranks by freshness precisely so stale percepts lose.
MAX_TRANSIENT_TTL_SECONDS = 3600.0

# `kind` values that describe something read out of the user's own machine.
# `ContextBuilder` ranks lowest-privacy-first for *inclusion* order, so a
# percept left at `SENSITIVITY_PUBLIC` is the one that survives budget
# pressure and is first in line to be sent to a cloud fallback. A local
# text/image/fact percept has no business sitting there.
LOCAL_SURFACE_KINDS = frozenset({"text-file", "image_text", "fact"})

# conftest's per-test HOME override is what isolates GSettings, dconf and the
# keyring, but it also strips Python's user site-packages from `sys.path` in a
# fresh child process - so `senses/web.py` (needs httpx) would silently drop out
# of the registry there for environmental, not wiring, reasons. Resolved at
# import time (collection precedes every fixture, so HOME is still real) and
# re-exported to the child; only importability crosses, never settings.
USER_SITE_PACKAGES = site.getusersitepackages()


# --- loading the registry ---------------------------------------------------
#
# `_load_registry` is the single indirection the whole module reads through,
# so a negative control can substitute a hand-built registry and exercise
# these exact assertions without touching `usr/lib/`.

def _load_registry() -> "dict[str, object]":
    """Every builtin sense, with the user drop-in directory held empty.

    Excluding user drop-ins is what makes this a test of *this repo's*
    wiring. `senses._USER_SENSES_DIR` is resolved at import time from
    `$HOME`, so it must be redirected rather than merely ignored.
    """
    import shani_chronoa.senses as senses_mod

    original = senses_mod._USER_SENSES_DIR
    try:
        senses_mod._USER_SENSES_DIR = Path(os.devnull)
        return senses_mod.discover_senses()
    finally:
        senses_mod._USER_SENSES_DIR = original


@pytest.fixture
def registry():
    """The builtin sense registry, isolated from `~/.config` drop-ins."""
    return _load_registry()


# --- the consent surface, parsed from the shipped schema -------------------

def _schema_source(schema_dir: Path):
    """A `GSettingsSchemaSource` rooted at `schema_dir`, or None.

    `Gio.SettingsSchemaSource.get_default()` is a process-wide singleton that
    caches its first lookup, so building it explicitly is what makes "did the
    compiled schema survive" answerable per-test.

    Returns None rather than propagating when the directory holds no
    `gschemas.compiled`. That is precisely the state glib-compile-schemas
    leaves behind after discarding a malformed schema - and it raises a bare
    GError, which would surface as a test ERROR rather than as the actionable
    message the caller is written to produce.
    """
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    if not (schema_dir / "gschemas.compiled").is_file():
        return None
    try:
        return Gio.SettingsSchemaSource.new_from_directory(
            str(schema_dir), Gio.SettingsSchemaSource.get_default(), False
        )
    except GLib.Error:
        return None


def _schema_keys_from_xml() -> "set[str]":
    """Every `<key name=...>` declared in the shipped gschema, parsed."""
    root = ElementTree.parse(SCHEMA_XML).getroot()
    return {key.get("name") for key in root.iter("key") if key.get("name")}


def _bool_key_defaults_from_xml() -> "dict[str, bool]":
    """`{key: default}` for every `type="b"` key in the shipped gschema.

    A `<default>` is a GVariant literal, so a boolean is `true`/`false`
    with no quotes. A key with no `<default>` child inherits nothing and
    is reported as absent rather than guessed at.
    """
    root = ElementTree.parse(SCHEMA_XML).getroot()
    defaults: "dict[str, bool]" = {}
    for key in root.iter("key"):
        name, key_type = key.get("name"), key.get("type")
        if name is None or key_type != "b":
            continue
        child = key.find("default")
        if child is not None and child.text is not None:
            defaults[name] = child.text.strip() == "true"
    return defaults


def _consent_key(name: str) -> str:
    return f"{name}{CONSENT_SUFFIX}"


# --- per-sense invariants, expressed as pure predicates --------------------
#
# Each returns the list of senses that VIOLATE the invariant, so a negative
# control can feed a hand-built registry straight in and see the same
# verdict the real test computes.


def senses_without_consent_key(registry: "dict") -> "list[str]":
    """Senses with no `<name>-sense-enabled` key in the shipped schema."""
    keys = _schema_keys_from_xml()
    return sorted(name for name in registry if _consent_key(name) not in keys)


def durable_senses(registry: "dict") -> "list[str]":
    """Senses declaring `ttl_seconds is None` (routed to the on-disk tier)."""
    return sorted(
        name for name, sense in registry.items() if sense.ttl_seconds is None
    )


def durable_senses_outside_memory(registry: "dict") -> "list[str]":
    """Durable senses NOT declared by the memory module - always a bug."""
    return sorted(
        name
        for name, sense in registry.items()
        if sense.ttl_seconds is None and sense.run.__module__ != DURABLE_MODULE
    )


def senses_failing_the_loader_validator(registry: "dict") -> "list[str]":
    """Senses the loader would have refused had it not been bypassed."""
    from shani_chronoa.senses import _sense_problem

    problems: "list[str]" = []
    for name, sense in sorted(registry.items()):
        problem = _sense_problem(sense)
        if problem:
            problems.append(f"{name}: {problem}")
    return problems


def public_local_surface_senses(registry: "dict") -> "list[str]":
    """Senses reading local user data yet left at the `public` default."""
    from shani_chronoa.senses import SENSITIVITY_PUBLIC

    return sorted(
        name
        for name, sense in registry.items()
        if sense.kind in LOCAL_SURFACE_KINDS and sense.sensitivity == SENSITIVITY_PUBLIC
    )


def undeclared_sensitivity_senses(registry: "dict") -> "list[str]":
    """Senses whose `sensitivity` is not an explicitly declared tier."""
    from shani_chronoa.senses import _VALID_SENSITIVITY

    return sorted(
        name
        for name, sense in registry.items()
        if sense.sensitivity not in _VALID_SENSITIVITY
    )


def oversize_ttl_senses(registry: "dict") -> "list[str]":
    """Senses whose numeric TTL is long enough to be an oversight."""
    offenders: "list[str]" = []
    for name, sense in sorted(registry.items()):
        ttl = sense.ttl_seconds
        if ttl is None:
            continue
        if ttl > MAX_TRANSIENT_TTL_SECONDS:
            offenders.append(f"{name}: ttl_seconds={ttl!r} exceeds {MAX_TRANSIENT_TTL_SECONDS:g}s")
    return offenders




__all__ = [
    "CONSENT_SUFFIX",
    "DURABLE_MODULE",
    "EXPECTED_SENSE_KEY_DEFAULTS",
    "LOCAL_SURFACE_KINDS",
    "MAX_TRANSIENT_TTL_SECONDS",
    "REPO_ROOT",
    "SCHEMA_XML",
    "SENSE_CLI",
    "SENSES_PKG_DIR",
    "USER_SITE_PACKAGES",
    "_bool_key_defaults_from_xml",
    "_consent_key",
    "_load_registry",
    "_schema_keys_from_xml",
    "_schema_source",
    "durable_senses",
    "durable_senses_outside_memory",
    "oversize_ttl_senses",
    "public_local_surface_senses",
    "registry",
    "senses_failing_the_loader_validator",
    "senses_without_consent_key",
    "undeclared_sensitivity_senses",
]
