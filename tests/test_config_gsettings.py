"""RED tests: GSettings string quoting, fresh-instance round trips, empty defaults, API-key redaction.

Each test fails for a named defect in the current code (failing-first / RED phase).
"""

import logging
import subprocess

from shani_chronoa.config import SCHEMA_ID

import pytest


class TestModelDownloadConsent:
    """`model-download-enabled` is the only setting that causes a download."""

    def test_it_defaults_to_false(self, chronoa_config):
        # Given: a fresh config with the schema's default
        # When: the consent gate is read
        # Then: off, because Chronoa must never fetch bytes without being asked
        assert chronoa_config.model_download_enabled is False

    def test_it_is_a_value_and_not_a_method(self, chronoa_config):
        # A method missing @property is truthy forever, so the settings switch
        # would read as permanently on and the gate would never refuse. This
        # caught exactly that: the property was written without the decorator
        # and the settings-surface suite stayed green throughout.
        value = chronoa_config.model_download_enabled
        assert isinstance(value, bool), (
            f"model_download_enabled is {type(value).__name__}, not bool"
        )


class TestStringQuoting:
    """`gsettings get` returns GVariant-quoted strings; ChronoaConfig stores them verbatim."""

    def test_model_default_is_empty_string(self, chronoa_config):
        # Given: a fresh config with no overrides (schema default model='')
        # When: the model property is read
        model = chronoa_config.model
        # Then: it must be the empty string (no override -> hardware auto-selection)
        assert model == ""

    def test_whisper_model_default_is_empty_string(self, chronoa_config):
        # Given: a fresh config with no overrides (schema default whisper-model='')
        # When: the whisper-model property is read
        whisper_model = chronoa_config.whisper_model
        # Then: it must be the empty string (no override -> hardware auto-selection)
        assert whisper_model == ""

    def test_ollama_host_default_is_unquoted(self, chronoa_config):
        # Given: a fresh config (schema default ollama-host='http://localhost:11434')
        # When: the ollama-host property is read
        host = chronoa_config.ollama_host
        # Then: it must be the plain URL, not the GVariant-quoted "'http://localhost:11434'"
        assert host == "http://localhost:11434"

    def test_piper_voice_default_is_unquoted(self, chronoa_config):
        # Given: a fresh config (schema default piper-voice='en_US-lessac-medium')
        # When: the piper-voice property is read
        voice = chronoa_config.piper_voice
        # Then: it must be the plain voice id, not the GVariant-quoted value
        assert voice == "en_US-lessac-medium"

    def test_language_default_is_unquoted(self, chronoa_config):
        # Given: a fresh config (schema default language='en')
        # When: the language property is read
        language = chronoa_config.language
        # Then: it must be the plain locale, not the GVariant-quoted value
        assert language == "en"

    def test_wake_phrase_default_is_unquoted(self, chronoa_config):
        # Given: a fresh config (schema default wake-phrase='hey chronoa')
        # When: the wake-phrase property is read
        wake_phrase = chronoa_config.wake_phrase
        # Then: it must be the plain phrase, not the GVariant-quoted value
        assert wake_phrase == "hey chronoa"

    def test_boolean_settings_parse_correctly(self, chronoa_config):
        # Baseline: booleans already round-trip (gsettings prints them unquoted).
        assert chronoa_config.privacy_mode is True
        assert chronoa_config.auto_start is False
        assert chronoa_config.cloud_fallback_enabled is False
        assert chronoa_config.barge_in_vad_enabled is False


class TestFreshInstanceRoundTrip:
    """A value set through one instance must be read back unquoted by a fresh instance."""

    def test_set_then_fresh_instance_roundtrip(self, chronoa_config, gsettings_env):        # Given: a value set through one config instance
        chronoa_config.set("model", "qwen3:4b")
        # When: a fresh instance reads it back from the keyfile backend
        from shani_chronoa.config import ChronoaConfig
        fresh = ChronoaConfig()
        # Then: the fresh instance must read the same unquoted value
        assert fresh.model == "qwen3:4b"

    def test_set_ollama_host_then_fresh_read(self, chronoa_config, gsettings_env):
        # Given: a host set through one config instance
        chronoa_config.set("ollama-host", "http://localhost:11434")
        # When: a fresh instance reads it back from the keyfile backend
        from shani_chronoa.config import ChronoaConfig
        fresh = ChronoaConfig()
        # Then: the fresh instance must read the plain URL
        assert fresh.ollama_host == "http://localhost:11434"


class TestEmptyDefaultsReachConsumers:
    """Empty string defaults must not defeat hardware auto-selection downstream."""

    def test_init_components_uses_hardware_model_when_unset(self, stubbed_app, monkeypatch):
        # Given: no model override (schema default model='') and no --model= flag
        # When: components are initialized
        from unittest.mock import patch

        with patch("shani_chronoa.app.application.OllamaLLM") as mock_llm_cls:
            mock_llm_cls.return_value.is_available.return_value = False
            stubbed_app._init_components()
            model = mock_llm_cls.call_args.kwargs["model"]
        # Then: the hardware auto-selected model must be used, not a quoted empty string
        assert model == "qwen3:4b"


class TestHardwareProfileOverride:
    """The persisted hardware-profile setting must drive model/whisper/context selection."""

    def test_init_components_applies_persisted_high_profile(self, stubbed_app, monkeypatch):
        # Given: a persisted hardware-profile override of "high" over a detected "low" profile
        stubbed_app.config.set("hardware-profile", "high")
        from unittest.mock import patch
        from shani_chronoa.hardware_profile import HardwareProfile
        hw = HardwareProfile.__new__(HardwareProfile)
        hw.profile = "low"
        stubbed_app.hardware = hw
        # When: components are initialized
        with patch("shani_chronoa.app.application.OllamaLLM") as mock_llm_cls, \
             patch("shani_chronoa.stt.build_stt") as mock_stt_cls:
            mock_llm_cls.return_value.is_available.return_value = False
            stubbed_app._init_components()
            model = mock_llm_cls.call_args.kwargs["model"]
            context = mock_llm_cls.call_args.kwargs["context_window"]
            whisper = mock_stt_cls.call_args.kwargs["model"]
        # Then: the high-tier model, whisper model, and context window are used
        assert model == "qwen3:4b"
        assert whisper == "medium"
        assert context == 8192

    def test_init_components_applies_persisted_low_profile(self, stubbed_app, monkeypatch):
        # Given: a persisted hardware-profile override of "low" over a detected "gpu" profile
        stubbed_app.config.set("hardware-profile", "low")
        from unittest.mock import patch
        from shani_chronoa.hardware_profile import HardwareProfile
        hw = HardwareProfile.__new__(HardwareProfile)
        hw.profile = "gpu"
        stubbed_app.hardware = hw
        # When: components are initialized
        with patch("shani_chronoa.app.application.OllamaLLM") as mock_llm_cls:
            mock_llm_cls.return_value.is_available.return_value = False
            stubbed_app._init_components()
            model = mock_llm_cls.call_args.kwargs["model"]
            context = mock_llm_cls.call_args.kwargs["context_window"]
        # Then: the low-tier model and context window are used
        assert model == "qwen3:1.7b"
        assert context == 2048

    def test_init_components_unknown_profile_falls_back_to_detected(self, stubbed_app, monkeypatch):
        # Given: a persisted hardware-profile value that is not a known tier
        stubbed_app.config.set("hardware-profile", "quantum")
        from unittest.mock import patch
        from shani_chronoa.hardware_profile import HardwareProfile
        hw = HardwareProfile.__new__(HardwareProfile)
        hw.profile = "medium"
        stubbed_app.hardware = hw
        # When: components are initialized
        with patch("shani_chronoa.app.application.OllamaLLM") as mock_llm_cls:
            mock_llm_cls.return_value.is_available.return_value = False
            stubbed_app._init_components()
            model = mock_llm_cls.call_args.kwargs["model"]
            context = mock_llm_cls.call_args.kwargs["context_window"]
        # Then: the detected (medium) tier is used
        assert model == "qwen3:4b"
        assert context == 4096

    def test_init_components_auto_keeps_detected_profile(self, stubbed_app, monkeypatch):
        # Given: the default "auto" hardware profile (schema default)
        assert stubbed_app.config.hardware_profile == "auto"
        from unittest.mock import patch
        from shani_chronoa.hardware_profile import HardwareProfile
        hw = HardwareProfile.__new__(HardwareProfile)
        hw.profile = "low"
        stubbed_app.hardware = hw
        # When: components are initialized
        with patch("shani_chronoa.app.application.OllamaLLM") as mock_llm_cls:
            mock_llm_cls.return_value.is_available.return_value = False
            stubbed_app._init_components()
            model = mock_llm_cls.call_args.kwargs["model"]
        # Then: the detected (low) tier model is used
        assert model == "qwen3:1.7b"


class TestApiKeyHandling:
    """API keys must be empty (not "''"), redacted from logs, and never in argv."""

    def test_api_key_defaults_are_empty_not_quoted(self, chronoa_config):
        # Given: no API keys configured (schema defaults are all '')
        # When: the cloud LLM API keys are read
        keys = chronoa_config.cloud_llm_api_keys()
        # Then: every key must be the empty string, not the quoted "''"
        assert all(v == "" for v in keys.values())

    def test_api_key_redacted_from_logs(self, chronoa_config, caplog):
        # Given: an API key value
        key_value = "sk-secret-12345"
        # When: the key is set through the config
        with caplog.at_level(logging.DEBUG, logger="shani_chronoa.config"):
            chronoa_config.set("openai-api-key", key_value)
        # Then: the key value must never appear in logs
        assert key_value not in caplog.text

    def test_api_key_not_placed_in_subprocess_argv(self, chronoa_config, monkeypatch):
        # Given: an API key value
        key_value = "sk-secret-12345"
        calls: list = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", _fake_run)  # any subprocess, from anywhere
        # When: the key is set through the config
        chronoa_config.set("openai-api-key", key_value)
        # Then: the key value must not appear in any subprocess argv
        assert all(key_value not in " ".join(c) for c in calls)

    def test_quoted_empty_key_does_not_enable_byok_provider(self, chronoa_config):
        # Given: no API keys configured - config must return clean empty
        # strings, never the GVariant-quoted "''" that would look like a
        # real key to CloudLLMChain's `requires_key and not key` skip
        from shani_chronoa.cloud_llm import CloudLLMChain
        # When: the cloud chain is built with the config's keys
        chain = CloudLLMChain(provider_ids=("openai",), api_keys=chronoa_config.cloud_llm_api_keys())
        # Then: the BYOK provider must be skipped (no backend)
        assert not chain.is_available()

    def test_quoted_empty_key_not_sent_as_credential(self, chronoa_config):
        # Given: no API key configured
        # When: the config's key for a BYOK provider is read
        key = chronoa_config.cloud_llm_api_keys()["openai"]
        # Then: it must be a clean empty string, never the quoted "''" that
        # cloud_llm would send as "Authorization: Bearer ''"
        assert key == ""
        assert key != "''"
        assert f"Bearer {key or 'unused'}" != "Bearer ''"


class TestDebugModeWiring:
    """debug-mode must have a real effect: it drives the logging level."""

    def test_parse_args_debug_sets_persisted_setting(self, stubbed_app):
        # Given: debug mode off
        stubbed_app.config.set("debug-mode", "false")
        # When: --debug is parsed
        stubbed_app._parse_args(["--debug"])
        # Then: the persisted setting is enabled (existing --debug behavior kept)
        assert stubbed_app.config.debug_mode is True

    def test_toggle_debug_flips_setting_and_log_level(self, stubbed_app):
        # Given: debug mode off and INFO logging
        import logging
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        for handler in root.handlers:
            handler.setLevel(logging.INFO)
        stubbed_app.config.set("debug-mode", "false")
        try:
            # When: the toggle action runs
            stubbed_app._toggle_debug(None, None)
            # Then: the setting is persisted and the root logger is at DEBUG
            assert stubbed_app.config.debug_mode is True
            assert root.level == logging.DEBUG
            assert all(h.level <= logging.DEBUG for h in root.handlers)
        finally:
            root.setLevel(logging.INFO)
            for handler in root.handlers:
                handler.setLevel(logging.INFO)

    def test_set_log_level_controls_root_and_handlers(self):
        # Given: INFO logging (basicConfig pins both root and handler levels)
        import logging
        from shani_chronoa.app import _set_log_level
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        for handler in root.handlers:
            handler.setLevel(logging.INFO)
        try:
            # When: debug logging is enabled then disabled
            _set_log_level(True)
            # Then: both the root logger and its handlers reach DEBUG
            assert root.level == logging.DEBUG
            assert all(h.level <= logging.DEBUG for h in root.handlers)
            _set_log_level(False)
            # And: disabling returns both to INFO
            assert root.level == logging.INFO
            assert all(h.level == logging.INFO for h in root.handlers)
        finally:
            root.setLevel(logging.INFO)
            for handler in root.handlers:
                handler.setLevel(logging.INFO)

class TestTheConsentKeyTableIsWrittenOnce:
    """Two merge artifacts in one dict literal.

    `network` and `display` were each listed twice. Python keeps the last value
    and raises nothing, so a duplicate is invisible at runtime and invisible to
    any test that imports the dict - only the source says anything. Reading the
    literal with `ast` is the only way to see it, and seeing it matters: a
    duplicated row is where a half-finished rename hides.
    """

    def _literal_entries(self):
        import ast
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / (
            "usr/lib/shani-chronoa/shani_chronoa/config.py")
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Assign) and getattr(
                    node.targets[0], "id", "") == "_SENSE_CONSENT_KEYS":
                return [k.value for k in node.value.keys]
        raise AssertionError("_SENSE_CONSENT_KEYS literal not found")

    def test_no_consent_key_is_listed_twice(self):
        keys = self._literal_entries()
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        assert not dupes, f"listed more than once in _SENSE_CONSENT_KEYS: {dupes}"

    def test_it_is_a_bijection_with_the_live_senses(self):
        from shani_chronoa.senses import discover_senses

        keys = set(self._literal_entries())
        live = set(discover_senses())
        assert live <= keys, f"senses with no consent key: {sorted(live - keys)}"
        assert keys <= live, f"consent keys for no sense: {sorted(keys - live)}"


class TestTheRefusalOnlyNamesASwitchThatExists:
    """A refusal is an instruction, so it may only name a real switch.

    The reason string listed retired aliases alongside the live key, which sent
    a user to enable a setting that has no row in the settings window. The
    aliases are still honoured - that is what keeps a pre-merge grant alive -
    they are simply not things to tell someone to go and do.
    """

    def test_it_names_the_live_key_and_no_retired_one(self, chronoa_config):
        from shani_chronoa.config import _SENSE_CONSENT_ALIASES, _SENSE_CONSENT_KEYS

        for sense in sorted(_SENSE_CONSENT_ALIASES):
            chronoa_config.set("privacy-mode", "false")
            chronoa_config.set(f"{sense}-sense-enabled", "false")
            for alias in _SENSE_CONSENT_ALIASES[sense]:
                chronoa_config.set(alias, "false")
            reason = chronoa_config.sense_allowed_reason(sense)
            assert f"'{_SENSE_CONSENT_KEYS[sense]}'" in reason, (
                f"{sense} refusal does not name its own key: {reason}")
            for alias in _SENSE_CONSENT_ALIASES[sense]:
                assert alias not in reason, (
                    f"{sense} refusal tells the user to enable the retired "
                    f"key '{alias}', which has no row in the settings window")

    def test_a_retired_grant_is_still_honoured(self, chronoa_config):
        """The compatibility mechanism itself, which the message now omits on
        purpose - so it needs its own test or the two get conflated."""
        from shani_chronoa.config import _SENSE_CONSENT_ALIASES

        for sense, aliases in _SENSE_CONSENT_ALIASES.items():
            chronoa_config.set("privacy-mode", "false")
            chronoa_config.set(f"{sense}-sense-enabled", "false")
            for alias in aliases:
                chronoa_config.set(alias, "true")
                assert chronoa_config.sense_allowed(sense) is True, (
                    f"{alias} no longer grants {sense}; a pre-merge install "
                    "would silently lose a capability it had enabled")


class TestTheKeyfileGroupSpelling:
    """`gsettings get` must be able to read what this app writes.

    **The defect, and why it was invisible.** `ChronoaConfig` built its keyfile
    backend with `root_group=SCHEMA_ID`, so the app wrote `[org.shani.chronoa]`
    while the default backend - the `gsettings` CLI and every other GLib process
    on the machine - wrote `[org/shani/chronoa]`, after the schema's PATH. Both
    landed in the same file, so `gsettings get org.shani.chronoa <key>` answered
    `false` for a value the app had written as `true`: **the switch says on,
    every gated skill refuses, and the CLI insists it is off.** The debugging
    path contradicted the thing it exists to check.

    It was invisible because the app reads back through `ChronoaConfig` too, so
    the round-trip test above passed on both spellings. Only a SECOND READER
    can tell them apart, which is what these assert.
    """

    def _keyfile(self, gsettings_env):
        from pathlib import Path
        import os
        return Path(os.environ["XDG_CONFIG_HOME"]) / "glib-2.0" / "settings" / "keyfile"

    def test_the_group_the_app_writes_is_the_group_the_cli_reads(self, chronoa_config, gsettings_env):
        from shani_chronoa.config import _ROOT_GROUP
        chronoa_config.set("i2c-write-enabled", "true")
        text = self._keyfile(gsettings_env).read_text()
        assert f"[{_ROOT_GROUP}]" in text, (
            f"the app did not write the group the gsettings CLI reads; the file is:\n{text}")
        assert f"[{SCHEMA_ID}]" not in text.replace(f"[{_ROOT_GROUP}]", ""), (
            "the old spelling is still being written alongside the right one")

    def test_the_root_group_is_derived_from_the_path_not_a_second_literal(self):
        from shani_chronoa.config import SCHEMA_PATH, _ROOT_GROUP
        assert _ROOT_GROUP == SCHEMA_PATH.strip("/"), (
            "_ROOT_GROUP is a second literal that can drift from SCHEMA_PATH")

    def test_a_legacy_keyfile_is_adopted_rather_than_left_unreadable(self, chronoa_config, gsettings_env):
        """A keyfile written by an older build must not become dead settings.

        Without the adoption step, a user who had already granted a consent key
        would find every gated skill refusing while the switch reads on - the
        dead-switch class, reached by a rename.
        """
        keyfile = self._keyfile(gsettings_env)
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_text(
            f"[{SCHEMA_ID}]\n"
            "i2c-write-enabled=true\n"
            "driver-build-enabled=true\n"
        )
        from shani_chronoa.config import ChronoaConfig, _ROOT_GROUP
        fresh = ChronoaConfig()
        assert fresh.get_bool("i2c-write-enabled", False) is True, (
            "a grant recorded under the legacy group was lost")
        assert fresh.get_bool("driver-build-enabled", False) is True
        text = keyfile.read_text()
        assert f"[{_ROOT_GROUP}]" in text and f"[{SCHEMA_ID}]" not in text, (
            f"the migration did not converge to one group:\n{text}")

    def test_the_newer_group_wins_over_a_stale_legacy_copy(self, chronoa_config, gsettings_env):
        from shani_chronoa.config import ChronoaConfig, _ROOT_GROUP
        keyfile = self._keyfile(gsettings_env)
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_text(
            f"[{SCHEMA_ID}]\nmodel=STALE\n\n[{_ROOT_GROUP}]\nmodel=FRESH\n"
        )
        assert ChronoaConfig().get("model") == "FRESH", (
            "an old copy overwrote a newer value")

    def test_the_migration_converges_and_leaves_no_group_behind(self, chronoa_config, gsettings_env):
        """Run it repeatedly: the group must go, not just stop growing.

        The first version removed the legacy group only when something was
        adopted, so a keyfile whose keys were all already correct kept the stale
        spelling forever - two groups after any number of runs. **The plain case
        could not catch that**: with only the legacy group present the first run
        adopts its keys, `adopted` is non-zero, and the group goes anyway. The
        control for it is `test_a_legacy_group_whose_keys_are_all_migrated_goes_too`
        below - without that one, the mutation survived a green suite.
        """
        keyfile = self._keyfile(gsettings_env)
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_text(f"[{SCHEMA_ID}]\ni2c-write-enabled=true\n")
        from shani_chronoa.config import ChronoaConfig, _ROOT_GROUP
        for _ in range(3):
            ChronoaConfig()
        groups = [ln for ln in keyfile.read_text().splitlines() if ln.startswith("[")]
        assert groups == [f"[{_ROOT_GROUP}]"], (
            f"expected exactly one group after three runs, got {groups}")

    def test_a_legacy_group_whose_keys_are_all_migrated_goes_too(self, chronoa_config, gsettings_env):
        """The case the migration leaves behind when it only removes on adoption.

        Both spellings hold the same key, so nothing is adopted. A migration
        guarded by `if adopted:` never removes the old group - measured as two
        groups after any number of runs - and the stale copy stays readable by a
        reader of the old spelling, which is exactly the ambiguity being closed.
        """
        from shani_chronoa.config import ChronoaConfig, _ROOT_GROUP
        keyfile = self._keyfile(gsettings_env)
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_text(
            f"[{SCHEMA_ID}]\ni2c-write-enabled=true\n\n"
            f"[{_ROOT_GROUP}]\ni2c-write-enabled=true\n"
        )
        ChronoaConfig()
        groups = [ln for ln in keyfile.read_text().splitlines() if ln.startswith("[")]
        assert groups == [f"[{_ROOT_GROUP}]"], (
            f"the legacy group survived a run that adopted nothing: {groups}")

    def test_a_second_process_reads_what_the_first_wrote(self, chronoa_config, gsettings_env):
        """The whole point: a reader that is not this process.

        Asserted with `gsettings` itself when it is available, which is the
        external reader the bug was about - a second `ChronoaConfig` would have
        passed on the broken code.
        """
        chronoa_config.set("i2c-write-enabled", "true")
        try:
            out = subprocess.run(
                ["gsettings", "get", "org.shani.chronoa", "i2c-write-enabled"],
                capture_output=True, text=True, timeout=30, check=False)
        except OSError:
            pytest.skip("gsettings is not installed, so the CLI cannot be the witness")
        assert out.stdout.strip() == "true", (
            f"gsettings reads {out.stdout.strip()!r} for a value the app wrote "
            f"as true - the two readers still disagree:\n{out.stderr}")
