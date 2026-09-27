"""RED tests: a registered sense must be fully wired, not merely declared.

This module exists because of a class of bug this repo has demonstrable
history with. `ipc.py`'s `PeerValidator`, `tool_tracking.py`'s `ToolCall`,
`gateway_supervisor.py` and `sandbox/profiles.py`'s `AgentProfile` were all
fully built, passed module-level unit tests, and were never imported by
anything that runs - four "features" that were pure dead weight. The stated
project rule (see this repo's `AGENTS.md`) is: *do not treat a `feat:` commit
message or a passing module-level unit test as proof something is live -
grep for real callers first.*

The senses layer has exactly the same shape of hazard, and worse, because a
sense that exists but is never reachable is a **privacy** hazard rather than a
feature gap:

- A sense with no `<name>-sense-enabled` consent key in the gschema is
  **permanently denied**. `ChronoaConfig.sense_allowed()` calls
  `get_bool(f"{name}-sense-enabled", ...)`, and `get_bool` returns the
  supplied default for any key the running schema does not declare. The
  sense is registered, discoverable, importable, and reachable by nothing -
  a dead sense that looks alive in `discover_senses()`.
- A sense that declares `ttl_seconds=None` is **durable**: `PerceptStore.add`
  routes it to the on-disk tier (`~/.local/share/shani-chronoa/percepts/`).
  A screen or filesystem sense declaring `None` does not fail - it silently
  writes a surveillance record to disk that outlives every session, which is
  the one outcome the `senses/__init__.py` docstring says must not happen.
- A schema that `glib-compile-schemas` silently discards makes *every*
  setting revert to a hardcoded Python default while still exiting 0. This
  repo's `AGENTS.md` records exactly that having shipped. A "does the schema
  compile" check proves nothing on its own, so
  `test_compiled_schema_still_declares_every_key_in_the_xml` compares the
  *compiled* schema against the *source* XML key-by-key instead.

Paths, constants and the per-sense predicates live in
`sense_manifest_support.py`; this module holds only the assertions.

allow: SIZE_OK - ~330 pure lines, one responsibility ("every registered sense
is fully wired"), and the bulk is per-assertion failure text. A sense author
who trips one of these should be told the exact key or the exact module to
touch without opening a second file, and that text is the deliverable rather
than commentary. Splitting further would move those messages away from the
assertion they explain.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sense_manifest_support import (
    DURABLE_MODULE,
    EXPECTED_SENSE_KEY_DEFAULTS,
    LOCAL_SURFACE_KINDS,
    MAX_TRANSIENT_TTL_SECONDS,
    PKG_DIR,
    REPO_ROOT,
    SCHEMA_XML,
    SENSE_CLI,
    SENSES_PKG_DIR,
    USER_SITE_PACKAGES,
    _bool_key_defaults_from_xml,
    _consent_key,
    _load_registry,
    _schema_keys_from_xml,
    _schema_source,
    durable_senses,
    durable_senses_outside_memory,
    oversize_ttl_senses,
    public_local_surface_senses,
    registry,
    senses_failing_the_loader_validator,
    senses_without_consent_key,
    undeclared_sensitivity_senses,
)
# --- gschema layer invariants (independent of how many senses exist) ------


class TestGschemaConsentSurface:
    """The gschema must compile into something that still has every key.

    `glib-compile-schemas` discards a malformed schema file *entirely* and
    exits 0, after which every setting silently reverts to a hardcoded
    Python default. That has shipped in this repo before (see `AGENTS.md`),
    so "it compiled" is not the question - "the compiled artifact still
    declares the keys the source declares" is.
    """

    def test_gschema_xml_parses_and_declares_the_five_sense_keys(self):
        # Given: the shipped gschema XML
        # When: it is parsed as XML rather than regex-matched
        keys = _schema_keys_from_xml()
        # Then: parsing yields the consent surface, so a broken file is a
        # hard failure here rather than a silently short list downstream
        missing = sorted(set(EXPECTED_SENSE_KEY_DEFAULTS) - keys)
        assert missing == [], f"schema XML is missing consent keys: {missing}"

    def test_compiled_schema_still_declares_every_key_in_the_xml(
        self, compiled_schema_dir, gsettings_env
    ):
        # Given: a freshly compiled copy of the shipped schema
        source = _schema_source(compiled_schema_dir)
        # When: glib-compile-schemas has had its chance to silently discard it
        assert source is not None, (
            f"{compiled_schema_dir} holds no gschemas.compiled at all - "
            "glib-compile-schemas discarded the whole schema file. It exits 0 "
            "on a malformed schema, so a clean exit proves nothing; every "
            "setting would silently revert to a hardcoded Python default."
        )
        compiled = source.lookup("org.shani.chronoa", True)
        assert compiled is not None, (
            "the compiled schema dir does not contain org.shani.chronoa at all - "
            "glib-compile-schemas discarded the whole file (it exits 0 on a "
            "malformed schema, so this is the failure to watch for)"
        )
        compiled_keys = set(compiled.list_keys())
        # Then: nothing the source declared was lost on the way to the binary
        dropped = sorted(_schema_keys_from_xml() - compiled_keys)
        assert dropped == [], f"compiled schema dropped keys declared in the XML: {dropped}"

    def test_sense_consent_keys_default_as_intended(
        self, compiled_schema_dir, gsettings_env
    ):
        # Given: the compiled schema as the running app would see it
        source = _schema_source(compiled_schema_dir)
        assert source is not None, "no gschemas.compiled - see the test above"
        compiled = source.lookup("org.shani.chronoa", True)
        assert compiled is not None, "compiled schema not found - see the test above"
        xml_defaults = _bool_key_defaults_from_xml()
        # When/Then: each of the five sense keys carries its documented default.
        # Every sense is opt-in; only memory is on by default.
        for key, expected in sorted(EXPECTED_SENSE_KEY_DEFAULTS.items()):
            assert xml_defaults.get(key, f"<{key} is not a boolean or has no default>") is expected, (
                f"{key} must default to {str(expected).lower()} in the gschema XML, "
                "got "
                f"{xml_defaults.get(key, '<missing>')}"
            )
            compiled_default = compiled.get_key(key).get_default_value().get_boolean()
            assert compiled_default is expected, (
                f"{key} must default to {str(expected).lower()} in the COMPILED "
                f"schema (the running app reads this, not the XML); got "
                f"{compiled_default}"
            )


class TestSenseRegistryIsNotVacuous:
    """Anti-vacuity: the per-sense assertions below must have something to chew on."""

    def test_the_senses_layer_registers_at_least_one_sense(self, registry):
        # Given/When: the builtin registry
        # Then: it is non-empty, so the per-sense invariants are not passing
        # for want of subjects
        assert registry, (
            "no builtin sense is registered: every per-sense assertion in this "
            "module would pass vacuously. A sense module under "
            "shani_chronoa/senses/ must declare a SENSES list."
        )


class TestEverySenseHasAConsentKey:
    """A sense with no consent key is a sense that can never be permitted."""

    def test_every_sense_declares_its_own_consent_key(self, registry):
        # Given: the registered senses
        # When: each one's `<name>-sense-enabled` key is looked up in the gschema
        missing = senses_without_consent_key(registry)
        # Then: none may be missing. `ChronoaConfig.sense_allowed()` calls
        # `get_bool(f"{name}-sense-enabled", default)`, and `get_bool` returns
        # `default` for a key the running schema does not declare - so a
        # missing key does not disable the sense, it makes it unreachable,
        # while `discover_senses()` still advertises it as available.
        assert missing == [], (
            "these senses are registered but have no consent key in "
            f"{SCHEMA_XML.name}, so `sense_allowed()` denies them forever and "
            "nothing can ever turn them on:\n"
            + "\n".join(
                f"  - {_consent_key(name)} is missing; add a <key name=\"{_consent_key(name)}\" "
                f'type="b"> to the gschema, or rename the sense to match an existing key'
                for name in missing
            )
        )

    def test_only_senses_whose_schema_default_is_true_are_allowed_out_of_the_box(
        self, chronoa_config, registry, gsettings_env
    ):
        # Given: a fresh install - no user overrides, privacy mode on - and
        # each sense's consent key default as declared in the compiled schema
        assert chronoa_config.privacy_mode is True, (
            "the hermetic keyfile store must start in privacy mode, or this "
            "test cannot tell a default-on sense from an overridden one"
        )
        source = _schema_source(Path(os.environ["GSETTINGS_SCHEMA_DIR"]))
        assert source is not None, "no gschemas.compiled - see TestGschemaConsentSurface"
        compiled = source.lookup("org.shani.chronoa", True)
        # When/Then: the runtime agrees with the schema. Derived from the
        # schema rather than hardcoded, so a new consent key cannot smuggle in
        # a sense that perceives on a default install.
        disagreements: "list[str]" = []
        for name in sorted(registry):
            key = _consent_key(name)
            if not compiled.has_key(key):
                continue
            declared = compiled.get_key(key).get_default_value().get_boolean()
            if chronoa_config.sense_allowed(name) is not declared:
                disagreements.append(
                    f"{name}: {key} defaults to {str(declared).lower()} in the "
                    f"schema but sense_allowed() returned "
                    f"{str(chronoa_config.sense_allowed(name)).lower()}"
                )
        assert disagreements == [], (
            "the running app's consent verdict disagrees with the shipped "
            "schema defaults:\n" + "\n".join(f"  - {d}" for d in disagreements)
        )

    def test_setting_a_senses_consent_key_actually_grants_that_sense(
        self, chronoa_config, registry
    ):
        # Given: privacy mode off (the `web` sense is refused outright while
        # privacy mode is on, per `config._NETWORKED_SENSES`) and a sense's
        # own consent key turned on
        chronoa_config.set("privacy-mode", "false")
        # When: the key is written through the real `ChronoaConfig.set`
        ungranted: "list[str]" = []
        for name in sorted(registry):
            key = _consent_key(name)
            chronoa_config.set(key, "true")
            # Then: a fresh config over the same backend must see it. A
            # missing key makes `set()` a silent no-op, which is the whole
            # failure this catches - stronger than asserting the key string
            # is present, because it proves the setting is actually honoured.
            from shani_chronoa.config import ChronoaConfig

            reread = ChronoaConfig()
            if not reread.sense_allowed(name):
                ungranted.append(name)
        assert ungranted == [], (
            "writing a sense's consent key to true did not grant that sense - "
            "the key almost certainly is not declared in the gschema, so "
            "`ChronoaConfig.set()` hit its documented silent no-op branch:\n"
            + "\n".join(
                f"  - {_consent_key(name)}: add a <key> for it to "
                f"{SCHEMA_XML.name}, or give the sense a name that already has one"
                for name in ungranted
            )
        )


class TestEverySenseHasARealCaller:
    """A registry is not a caller. This is the AGENTS.md rule, made a test."""

    def test_the_senses_layer_has_a_consumer_outside_its_own_package(self):
        # Given: the whole shipped `usr/` tree
        consumers: "list[Path]" = []
        for path in sorted((REPO_ROOT / "usr").rglob("*.py")):
            if "__pycache__" in path.parts or SENSES_PKG_DIR in path.parents:
                continue
            text = path.read_text(errors="replace")
            if "shani_chronoa.senses" in text or "discover_senses" in text:
                consumers.append(path)
        for path in sorted(REPO_ROOT.glob("usr/bin/*")):
            if path.is_file():
                text = path.read_text(errors="replace")
                if "shani_chronoa.senses" in text or "discover_senses" in text:
                    consumers.append(path)
        # When/Then: something that actually runs imports the layer.
        # `ipc.py`, `tool_tracking.py`, `gateway_supervisor.py` and
        # `sandbox/profiles.py` all passed their own unit tests with zero
        # entries here; this is the check that would have caught them.
        assert consumers, (
            "nothing outside shani_chronoa/senses/ imports the senses layer or "
            "calls discover_senses() - the whole layer is dead code of exactly "
            "the kind this repo has shipped four times already (ipc.PeerValidator, "
            "tool_tracking.ToolCall, gateway_supervisor, sandbox/profiles.AgentProfile). "
            "Wire it into a launcher (e.g. usr/bin/shani-chronoa-sense) or delete it."
        )

    def test_every_sense_is_listed_by_the_headless_cli(self, registry, gsettings_env, monkeypatch):
        # Given: the registered senses
        if not SENSE_CLI.is_file():
            pytest.skip(
                f"{SENSE_CLI.relative_to(REPO_ROOT)} does not exist yet, so there is no "
                "consumer to prove reachability through. The registry-external "
                "caller test above still applies."
            )
        # When: the real launcher is driven as a subprocess, hermetically
        monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        monkeypatch.setenv(
            "PYTHONPATH", os.pathsep.join([str(PKG_DIR), USER_SITE_PACKAGES])
        )
        completed = subprocess.run(
            [sys.executable, str(SENSE_CLI), "list", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(os.environ),
        )
        assert completed.returncode == 0, (
            f"shani-chronoa-sense list failed (exit {completed.returncode}):\n"
            f"{completed.stderr}"
        )
        listing = json.loads(completed.stdout)["data"]["senses"]
        by_name = {entry["name"]: entry for entry in listing}
        # Then: every registered sense is reachable *through a real consumer*,
        # not merely present in the dict this test loaded itself
        unreachable = sorted(
            name for name in registry if not by_name.get(name, {}).get("registered")
        )
        assert unreachable == [], (
            "these senses load into discover_senses() but the headless CLI does "
            "not see them, so the loader-to-consumer path is broken for them:\n"
            + "\n".join(f"  - {name}: not present in `shani-chronoa-sense list --json`" for name in unreachable)
        )


class TestEverySenseIsContractValid:
    """Nothing malformed should have reached the registry in the first place."""

    def test_every_registered_sense_passes_the_loaders_own_validator(self, registry):
        # Given/When: the loader's own `_sense_problem` is run over every entry
        problems = senses_failing_the_loader_validator(registry)
        # Then: nothing may be rejected. The loader skips malformed entries
        # with a warning, so a live violation means something bypassed
        # `discover_senses()` (a cached registry, a hand-built entry, a
        # validation rule that was loosened in one place only).
        assert problems == [], "registered senses the loader would refuse:\n" + "\n".join(
            f"  - {problem}" for problem in problems
        )

    def test_every_sense_schema_advertises_its_own_name(self, registry):
        # Given/When: each sense's Ollama-style function schema
        mismatched = sorted(
            name
            for name, sense in registry.items()
            if sense.schema.get("function", {}).get("name") != name
        )
        # Then: the advertised tool name must equal the registry key, or a
        # caller looking the sense up by the name the LLM was shown misses it
        assert mismatched == [], (
            f"these senses advertise a different function name in their schema: {mismatched}"
        )


class TestEverySenseDeclaresSensitivity:
    """Sensitivity is not optional metadata; it decides what leaves the machine."""

    def test_every_sense_declares_a_valid_sensitivity_tier(self, registry):
        # Given/When: each sense's declared tier
        undeclared = undeclared_sensitivity_senses(registry)
        # Then: it must be one the context builder and every cloud translator
        # actually know about - an unrecognised tier is ranked as maximally
        # private by accident, not by decision
        assert undeclared == [], f"these senses declare a sensitivity outside the tier set: {undeclared}"

    def test_local_surface_percepts_are_not_left_public(self, registry):
        # Given/When: senses whose percept content is read from the user's
        # own machine, checked against the `public` default
        offenders = public_local_surface_senses(registry)
        # Then: none may sit at `public`. `ContextBuilder` orders percepts
        # least-private-first for INCLUSION, so a public percept is the last
        # one dropped under budget pressure and the first one offered to a
        # cloud fallback - a local file path or OCR dump has no business there
        assert offenders == [], (
            f"these senses read local data but declare sensitivity='public': {offenders}. "
            "Use SENSITIVITY_PRIVATE (or SENSITIVITY_PERSONAL) explicitly."
        )


class TestEverySenseDeclaresItsLifetime:
    """A sense must choose a lifetime rather than inherit one by accident."""

    def test_every_sense_declares_a_ttl_within_a_sane_window(self, registry):
        # Given/When: each sense's numeric TTL
        oversize = oversize_ttl_senses(registry)
        # Then: a transient TTL beyond the window is an oversight. A screen
        # grab, an OCR dump or a fetched page is not context an hour later,
        # and `ContextBuilder` ranks by freshness so stale percepts lose.
        # (`None` is a *decision* - durable - and is asserted separately.)
        assert oversize == [], (
            "transient TTLs that long read as a forgotten value rather than a choice:\n"
            + "\n".join(f"  - {item}" for item in oversize)
        )

    def test_every_sense_ttl_is_a_positive_number_or_explicitly_durable(self, registry):
        # Given/When: every sense's declared `ttl_seconds`
        bad = sorted(
            f"{name}: ttl_seconds={sense.ttl_seconds!r}"
            for name, sense in registry.items()
            if not (
                sense.ttl_seconds is None
                or (
                    isinstance(sense.ttl_seconds, (int, float))
                    and not isinstance(sense.ttl_seconds, bool)
                    and sense.ttl_seconds > 0
                )
            )
        )
        # Then: a number or None. Nothing else - a zero, a negative, a
        # string, or a bool smuggled in through a NamedTuple is an accident
        # waiting to be read as "never expires" by `Percept.is_expired`
        assert bad == [], "senses with a missing or nonsensical ttl_seconds:\n" + "\n".join(
            f"  - {item}" for item in bad
        )


class TestOnlyMemoryMayBeDurable:
    """The most important assertion in this file.

    `PerceptStore.add()` routes `ttl_seconds is None` to the on-disk tier
    (`~/.local/share/shani-chronoa/percepts/`, appended as JSON lines and
    never re-checked against a TTL). A screen or filesystem sense declaring
    `None` does not raise, does not log, and does not expire - it silently
    accumulates a permanent record of what the user was doing, which is the
    precise outcome the `senses/__init__.py` and `senses/store.py` docstrings
    say must not happen ("a percept of what the screen looked like three
    sessions ago is not context, it is a surveillance record").
    """

    def test_only_the_memory_sense_may_declare_a_durable_ttl(self, registry):
        # Given/When: senses declaring `ttl_seconds is None`
        offenders = durable_senses_outside_memory(registry)
        # Then: none may exist outside the memory module
        assert offenders == [], (
            "these senses declare a durable TTL (ttl_seconds=None), which "
            "PerceptStore writes to the on-disk tier and never expires:\n"
            + "\n".join(
                f"  - {name}: declared in {registry[name].run.__module__}, not "
                f"{DURABLE_MODULE}. Give it a real TTL in seconds; if a durable "
                "percept really is warranted, that is a design decision to make "
                "deliberately in this test, not an accident to keep."
                for name in offenders
            )
        )

    def test_the_memory_senses_are_actually_durable(self, registry):
        # Given/When: senses declared by the memory module
        memory_senses = sorted(
            name for name, sense in registry.items() if sense.run.__module__ == DURABLE_MODULE
        )
        # Then: at least one exists and every one of them is durable. Without
        # this, deleting the memory senses would make the test above pass for
        # the wrong reason - nothing durable at all is also not "a screen
        # sense quietly persisting to disk".
        assert memory_senses, (
            f"no sense is declared by {DURABLE_MODULE}, so "
            "'only memory may be durable' has nothing to protect. The memory "
            "sense is the one capability meant to outlive a session."
        )
        assert set(durable_senses(registry)) == set(memory_senses), (
            "the memory module's senses must be the durable ones; got durable="
            f"{durable_senses(registry)} memory_module={memory_senses}"
        )

    def test_transient_percepts_are_never_written_to_disk(self, registry, tmp_path):
        # Given: a store pointed at a scratch file, and one percept per
        # registered sense built through that sense's own `to_percept`
        from shani_chronoa.senses.store import PerceptStore

        durable_file = tmp_path / "percepts" / "memory.jsonl"
        store = PerceptStore(durable_path=durable_file)
        transient = [
            (name, sense) for name, sense in sorted(registry.items()) if sense.ttl_seconds is not None
        ]
        # When: each is added
        for name, sense in transient:
            store.add(sense.to_percept(f"probe {name}"))
        # Then: the on-disk tier stayed empty - the store honours the TTL
        # split the sense declared, so a future regression that makes a
        # screen sense durable is visible here as a written file too
        assert not durable_file.exists(), (
            f"{durable_file} was written for transient percepts: "
            f"{sorted(name for name, _ in transient)}"
        )
