"""Every setting in the schema is reachable, or nothing would say so.

**Why this file exists, and why it is worded the way it is.** Auditing all 202
keys for "is anything reading this?" produced **six rounds of false alarms before
it produced one true result**: 162 unreachable, then 74, then 23, then 9, then
**0**. Every round was my own scanner missing one more way a key gets read:

1. `config.get_double("speech-rate")` - a read attribute list that stopped at
   `get_bool`, so two live settings looked write-only.
2. `config.sense_allowed(self.consent_key)` - the name is a *field*.
3. `SENSE_NAMES` in the machine panel is 16 senses; the real registry is the
   `SENSES` list in each of 57 modules, **53** of them. Reading the wrong one
   invented 42 dead senses.
4. Trigger *event* types resolve through `_consent_key_for` too.
5. `_CONSENT_KEY = "cleanup-enabled"` then `sense_allowed(_CONSENT_KEY)` - a
   constant, read through a name.
6. Tables: `TIMBRE_KEYS`, `SETTING = "extra-languages"`, a provider-to-key map.

**The hazard is not that any of those keys is dead. It is that a setting's
reachability depends on it appearing in one of roughly eight tables scattered
across modules, and nothing anywhere documents that list.** So the next person who
adds a key to the schema and a switch to the Settings window gets a control that
is silently inert - and every one of my six rounds is evidence that a competent,
careful attempt to check this answers wrongly.

**So this check over-approximates on purpose.** A key counts as reachable if it is
mentioned *anywhere* in the package, or derivable from a registered sense or
event. That cannot produce a false alarm: a key nobody mentions anywhere really is
inert. The cost is that it will not catch a key that is written but never read -
the sharper defect, and the one that needed six attempts to scan for. **A gate
that never cries wolf is worth more here than one that catches the ninth case**,
and `test_setting_dependencies.py` covers the sharp case where it can be stated
soundly.

The control is below and it bites: removing a single mention of a live setting
turns it red.
"""

import ast
import importlib
import pathlib
import sys
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("gi")
sys.path.insert(0, "usr/lib/shani-chronoa")

SCHEMA = pathlib.Path("usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml")
PKG = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa")


def _schema_keys():
    root = ET.parse(SCHEMA).getroot()
    return {k.get("name") for k in root.iter("key")}


def _every_mentioned_key():
    """Every schema key mentioned as a string literal anywhere in the package.

    Deliberately not "in a read position". Six rounds of false alarms came from
    trying to be clever about position; this counts a key as reachable if any
    source file says its name at all, which is a sound over-approximation.
    """
    keys = _schema_keys()
    found = set()
    for path in PKG.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value in keys:
                found.add(node.value)
    return found


def _keys_derived_from_senses_and_events():
    """`<name>-sense-enabled` for every registered sense and every event type.

    These two are resolved by **calling the real resolver** rather than by
    rebuilding its rule. Rebuilding it is how round three invented 42 senses that
    do not exist: `_consent_key_for` has an alias table, and a reconstructed
    `f"{name}-sense-enabled"` knows nothing about it.
    """
    from shani_chronoa import config

    names = set()
    for path in sorted((PKG / "senses").glob("*.py")):
        if path.stem.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"shani_chronoa.senses.{path.stem}")
        except Exception:                                    # noqa: BLE001
            continue
        for entry in getattr(mod, "SENSES", []) or []:
            name = getattr(entry, "name", None) or (
                entry.get("name") if isinstance(entry, dict) else None)
            if name:
                names.add(name)
    for mod_name in ("triggers.common", "triggers.sources"):
        try:
            mod = importlib.import_module(f"shani_chronoa.{mod_name}")
        except Exception:                                    # noqa: BLE001
            continue
        for value in vars(mod).values():
            if isinstance(value, (list, tuple, set, frozenset)):
                names.update(v for v in value if isinstance(v, str))

    out = {config._consent_key_for(n) for n in names}
    for keys in config._SENSE_CONSENT_ALIASES.values():
        out.update(keys)
    return {k for k in out if k}


def test_every_setting_in_the_schema_is_reachable():
    """The gate: no schema key is inert."""
    reachable = _every_mentioned_key() | _keys_derived_from_senses_and_events()
    unreachable = sorted(_schema_keys() - reachable)
    assert not unreachable, (
        f"{len(unreachable)} settings are in the schema and named nowhere in the "
        f"package, so nothing can read them and every switch for them is inert: "
        f"{unreachable}. Either the setting does nothing, or it is read through a "
        f"table this check has not been taught - in which case teach it here "
        f"rather than leaving the setting unreachable.")


def test_the_sense_resolver_is_not_broken():
    """The derived half of the gate still derives something.

    **This first asserted that the resolver finds keys the literal scan misses,
    and that was wrong.** It found none - every derived sense key is also a
    literal somewhere, because `capabilities.GATED` spells them all out. So the
    resolver adds no coverage *today*.

    It still earns its place: the gate unions it so that a sense added without a
    literal cannot become inert, and a resolver that silently returned an empty
    set would look identical to one that is working. This asserts it is working.
    """
    derived = _keys_derived_from_senses_and_events()
    assert len(derived) >= 50, (
        f"the sense resolver derived only {len(derived)} keys; the documented "
        f"figure is 53 senses and 78 sense keys, so it has broken - and a "
        f"resolver returning nothing is indistinguishable from one that works")


def test_this_gate_can_fail(monkeypatch):
    """**The control.** The gate must be able to say no.

    It asserts a set difference is empty, which is exactly the shape that passes
    whatever it is handed. So it is driven against a schema whose keys are
    guaranteed absent from the package: a key nobody has ever written is
    unreachable by definition, and if this returns empty the gate is not looking
    at anything.
    """
    class _Fake:
        def getroot(self):
            key = ET.Element("key", name="a-setting-nobody-has-written",
                             type="b", default="false")
            schema = ET.Element("schema")
            schema.append(key)
            return schema

    monkeypatch.setattr(ET, "parse", lambda _p: _Fake())
    # The schema now holds exactly the one invented key, so the gate's answer is
    # a one-element difference and cannot be confused with the real set.
    assert _schema_keys() == {"a-setting-nobody-has-written"}
    unreachable = _schema_keys() - (_every_mentioned_key()
                                    | _keys_derived_from_senses_and_events())
    assert unreachable == {"a-setting-nobody-has-written"}, (
        f"a setting named nowhere was not reported unreachable: {unreachable}. "
        f"The gate cannot fail, so passing means nothing.")

    # And the real schema still passes with the real code.
    monkeypatch.undo()
    assert _schema_keys() != {"a-setting-nobody-has-written"}
    assert not (_schema_keys() - (_every_mentioned_key()
                                  | _keys_derived_from_senses_and_events())), (
        "with the real schema restored, real settings look unreachable - the "
        "gate and the live code disagree")


def test_the_registry_is_the_real_one_not_the_panel_list():
    """53 senses, not the 16 the machine panel names.

    Written down because round three of the audit read `SENSE_NAMES` - a list of
    what one panel displays - and reported 42 senses that do not exist. The panel
    list is a legitimate thing to read and the wrong thing to read here, and
    nothing says so at either end.
    """
    names = set()
    for path in sorted((PKG / "senses").glob("*.py")):
        if path.stem.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"shani_chronoa.senses.{path.stem}")
        except Exception:                                    # noqa: BLE001
            continue
        for entry in getattr(mod, "SENSES", []) or []:
            name = getattr(entry, "name", None) or (
                entry.get("name") if isinstance(entry, dict) else None)
            if name:
                names.add(name)
    assert len(names) >= 50, (
        f"only {len(names)} senses resolved from the registry; the panel list "
        f"has 16 and the documented figure is 53, so the registry walk has "
        f"broken rather than the senses having been removed")
    assert len(names) > 16, (
        "the resolved count has collapsed onto the panel list, which means "
        "this is walking SENSE_NAMES instead of the SENSES registry")