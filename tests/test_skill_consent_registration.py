"""Four skills declared a consent gate on a key the shipped schema never had.

`edit_file`, `undo_last_change`, `todo_list` and `manage_triggers` each declare
a `_CONSENT_KEY` and each `_consent()` fails closed on it, which is correct and
is the whole reason the defect stayed invisible: a gate that is shut looks
exactly like a gate that is working. The keys were simply absent from
`org.shani.chronoa.gschema.xml`, so `ChronoaConfig._valid_keys` - which is
built from `schema.list_keys()` - never contained them, and `get_bool` returns
its `default` argument for any key not in that set. Four skills that rewrite
files, persist a task list and arm unattended rules were ungrantable: no
settings switch, no `gsettings set`, no per-turn grant could ever open them.

`AGENTS.md` already names this exact hazard for the senses ("a rename silently
makes a sense permanently ungrantable") and this file applies the same rule to
skills, so the next rename cannot reproduce it unnoticed.

The load-bearing assertion here is `get_bool(key, True) is False`. `True` is a
deliberate poison value: a key the schema knows resolves to its stored value and
returns the schema default, which is `false`, while a key the schema does *not*
know falls through to the caller-supplied default and returns `True`. Asserting
`get_bool(key, False) is False` would pass on both paths and prove nothing.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402  - require_version must run first

import pytest  # noqa: E402

from shani_chronoa import config as config_mod  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.skills import (  # noqa: E402
    edit_file,
    manage_triggers,
    todo_list,
    undo_last_change,
)

GSCHEMA_XML = (
    Path(__file__).resolve().parent.parent
    / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml"
)

#: The four skills this file exists for: module -> (skill name, consent key).
#: Spelled out rather than derived, so a rename that moves a gate is a visible
#: test failure instead of a silent pass.
GATED = (
    (edit_file, "edit_file", "file-edit-enabled"),
    (undo_last_change, "undo_last_change", "file-edit-enabled"),
    (todo_list, "todo_list", "todo-list-enabled"),
    (manage_triggers, "manage_triggers", "trigger-control-enabled"),
)

#: Every consent key the fix registers. `file-delete-enabled` is the control:
#: it was already in the schema and already worked, so it must stay working.
REGISTERED = ("file-edit-enabled", "todo-list-enabled", "trigger-control-enabled")
CONTROL = "file-delete-enabled"


def _schema_keys() -> dict:
    """`{key name: <default> text}` as parsed from the XML, not the compiled store.

    Read from the source file deliberately. The compiled store is what runs,
    but a key present in one and not the other is a packaging accident rather
    than a defect, and this is a registration defect.
    """
    tree = ET.parse(GSCHEMA_XML)
    node = tree.getroot()
    return {
        key.get("name"): key.findtext("default")
        for key in node.iter("key")
        if key.get("name")
    }


@pytest.fixture
def hermetic(tmp_path, monkeypatch):
    """Point every XDG root at throwaway dirs.

    The conftest's own fixture isolates `XDG_CONFIG_HOME` and `HOME` but leaves
    `XDG_DATA_HOME` and `XDG_STATE_HOME` alone, and a run without them has
    already written into a real user's `~/.local/share` in this repo. Both are
    set here so no `Gio.Settings` backend constructed below can reach the real
    filesystem regardless of which one it picks.
    """
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "HOME"):
        target = tmp_path / var.lower()
        target.mkdir(exist_ok=True)  # conftest's autouse fixture already made some
        monkeypatch.setenv(var, str(target))
    return tmp_path


def _external_settings(tmp_path):
    """A `Gio.Settings` on the same keyfile store `ChronoaConfig` will read.

    Built with the keyfile backend at the path `_new_settings()` uses, rather
    than by calling a `ChronoaConfig` setter. The point is to prove the
    *external* grant path - a settings switch, a `gsettings set`, anything but
    Chronoa asking itself - can open the gate. Setting a value through the same
    object that later reads it would prove only that two calls agree.
    """
    config_home = str(tmp_path / "xdg_config_home")
    keyfile = str(Path(config_home) / "glib-2.0" / "settings" / "keyfile")
    backend = Gio.keyfile_settings_backend_new(
        keyfile, config_mod.SCHEMA_PATH, config_mod.SCHEMA_ID
    )
    return Gio.Settings.new_with_backend(config_mod.SCHEMA_ID, backend)


def test_each_gate_key_is_registered_in_the_schema():
    """The regression itself: the three keys are in the shipped XML."""
    keys = _schema_keys()
    missing = [key for key in REGISTERED if key not in keys]
    assert not missing, f"consent keys absent from the gschema: {missing}"


def test_each_registered_key_defaults_to_false():
    """Fail closed. A write/action capability that ships open is a defect."""
    keys = _schema_keys()
    for key in REGISTERED:
        assert keys.get(key) == "false", f"{key} defaults to {keys.get(key)!r}, not false"


@pytest.mark.parametrize("key", REGISTERED + (CONTROL,))
def test_valid_keys_contains_the_key(gsettings_env, hermetic, key):
    """`_valid_keys` is built from the schema, so this is the live surface."""
    config = ChronoaConfig()
    assert key in config._valid_keys, (
        f"{key} is not in _valid_keys; get_bool will ignore any value set for it"
    )


@pytest.mark.parametrize("key", REGISTERED + (CONTROL,))
def test_get_bool_resolves_from_the_schema_not_the_fallback(gsettings_env, hermetic, key):
    """`get_bool(key, True)` must be `False`: that is what proves resolution.

    An unknown key falls through to the caller's default and returns `True`
    here; a known key resolves to the schema default and returns `False`. The
    control key proves the assertion can distinguish the two.
    """
    config = ChronoaConfig()
    assert config.get_bool(key, True) is False, (
        f"get_bool({key!r}, True) returned the caller's default, so the key is "
        f"not resolving through the schema"
    )


@pytest.mark.parametrize("key", REGISTERED)
def test_unset_key_reads_false(gsettings_env, hermetic, key):
    """Unset means off, and is reported as off rather than as unknown."""
    assert ChronoaConfig().get_bool(key, False) is False


@pytest.mark.parametrize("module, name, key", GATED, ids=[s for _, s, _ in GATED])
def test_a_grant_opens_the_gate(gsettings_env, hermetic, module, name, key):
    """End to end: grant through `Gio.Settings`, then a *fresh* config allows.

    The fresh instance is the load-bearing part. It is what a real grant looks
    like - the value is already persisted when the process starts - and it is
    the case that a same-object write would have papered over.

    The precondition is asserted before the write rather than after, and that
    ordering is not incidental: `Gio.Settings.set_boolean()` on a key the schema
    does not define is a fatal `GLib-GIO-ERROR`, which aborts the whole
    process instead of failing this test. Checked last it would take the suite
    down with it rather than reporting anything.
    """
    precondition = ChronoaConfig()
    assert key in precondition._valid_keys, f"{key} is not a registered key, so it cannot be granted"

    settings = _external_settings(hermetic)
    settings.set_boolean(key, True)
    Gio.Settings.sync()

    # A new instance, never `precondition`: each holds its own in-memory view,
    # so reusing the pre-write one answers from cache and skips the round trip.
    config = ChronoaConfig()
    assert config.get_bool(key, False) is True, f"{key} did not survive a store round trip"

    allowed, reason = module._consent(ChronoaConfig())
    assert allowed, f"{name} refused a granted {key}: {reason}"


@pytest.mark.parametrize("module, name, key", GATED, ids=[s for _, s, _ in GATED])
def test_a_refusal_names_the_key(gsettings_env, hermetic, module, name, key):
    """A shut gate must say which switch to turn on.

    A refusal the user cannot act on reads as a broken assistant, and the
    whole cost of the original defect was that there was no switch to name.
    """
    allowed, reason = module._consent(ChronoaConfig())
    assert not allowed, f"{name} was allowed with {key} unset"
    assert key in reason, f"{name}'s refusal does not name {key}: {reason!r}"


def test_every_skill_consent_key_is_registered(gsettings_env, hermetic):
    """Sweep every skill's declared gate, not only the four found so far.

    This is the assertion that would have caught the defect on the day it was
    written. It reads each `_CONSENT_KEY` from the module itself, so a new
    skill cannot ship a gate on an unregistered key, and a rename that moves a
    key out of the schema is caught rather than inheriting this file's title.
    """
    import importlib
    import pkgutil

    from shani_chronoa import skills as skills_pkg

    declared = {}
    for info in pkgutil.iter_modules(skills_pkg.__path__):
        if not info.name.startswith("_"):
            try:
                mod = importlib.import_module(f"shani_chronoa.skills.{info.name}")
            except Exception:  # a skill's own import problem is not this file's business
                continue
            key = getattr(mod, "_CONSENT_KEY", None)
            if key:
                declared[info.name] = key

    # Guard the sweep itself: an empty sweep would make every assertion below
    # vacuously true, which is the failure this whole repo keeps warning about.
    assert len(declared) >= 20, f"consent sweep found only {len(declared)} gates; it is not reading the modules"

    keys = _schema_keys()
    unregistered = sorted(
        {key for key in declared.values() if key not in keys}
    )
    assert not unregistered, (
        f"skills declare consent keys that no schema key can ever satisfy: {unregistered}"
    )


def test_no_skill_default_declares_the_unreachable_file_write_key():
    """`file-write-enabled` is dead, and this records why that is safe.

    Measured 2026-09-30: the string appears nowhere in the worktree, in any
    tracked file at HEAD, or anywhere in git history (`git log -S`). No skill
    declares it and `capabilities.py` does not map to it, so adding it would
    register a permission nothing reads - a switch in the settings window that
    opens no gate. It is most likely the pre-rename name for what is now
    `file-edit-enabled`. Asserting its absence is what stops a later reader
    from "fixing" it into existence on the assumption that a missing gate is
    always a bug.
    """
    text = GSCHEMA_XML.read_text()
    assert "file-write-enabled" not in text

    import importlib
    import pkgutil

    from shani_chronoa import skills as skills_pkg

    offenders = []
    for info in pkgutil.iter_modules(skills_pkg.__path__):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"shani_chronoa.skills.{info.name}")
        except Exception:
            continue
        if getattr(mod, "_CONSENT_KEY", None) == "file-write-enabled":
            offenders.append(info.name)
    assert not offenders, f"skills now read file-write-enabled: {offenders}"



def test_control_key_still_resolves_after_the_change(gsettings_env, hermetic):
    """The pre-existing key must be untouched by the edit.

    A schema edit is a whole-file operation and `glib-compile-schemas` discards
    an entire file on the first malformed key, so every key is at risk from
    adding any key. This is the check that the addition did not cost the others.
    """
    config = ChronoaConfig()
    assert CONTROL in config._valid_keys
    assert config.get_bool(CONTROL, False) is False

    settings = _external_settings(hermetic)
    settings.set_boolean(CONTROL, True)
    Gio.Settings.sync()
    assert ChronoaConfig().get_bool(CONTROL, False) is True


def test_declared_key_matches_the_registry_mapping():
    """`capabilities.py` and the skill must agree on which key gates it.

    A mismatch is not cosmetic: the settings window builds its switches from
    the registry, so a skill reading a key the registry does not name has no
    switch at all, which is the defect this file is about arriving by a
    different route.
    """
    from shani_chronoa import capabilities

    for module, name, key in GATED:
        assert module._CONSENT_KEY == key, f"{name} declares {module._CONSENT_KEY}, expected {key}"
        mapped = capabilities.GATED.get(name)
        assert mapped == key, f"capabilities maps {name} to {mapped}, skill reads {key}"
        assert key in capabilities.GATE_NAMES, f"no human label for {key}"


def test_registry_maps_every_key_the_schema_knows_a_label_for():
    """A schema key with no label falls back to the bare key name in the menu."""
    from shani_chronoa import capabilities

    for key in REGISTERED:
        assert key in capabilities.GATE_NAMES
        label = capabilities.GATE_NAMES[key]
        assert label.startswith("Let Chronoa"), f"{key} label reads {label!r}"


def test_key_names_are_legal_for_glib():
    """`glib-compile-schemas` rejects `_` in a key name and discards the file.

    Every key silently disappears - not just the offending one - and the exit
    code stays 0. Asserted here so a future key cannot take the whole settings
    surface down the way this defect took four skills down.
    """
    bad = [key for key in REGISTERED if not re.fullmatch(r"[a-z0-9-]+", key)]
    assert not bad, f"keys glib-compile-schemas would reject, discarding the whole file: {bad}"