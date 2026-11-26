"""RED tests: GSettings string quoting, fresh-instance round trips, empty defaults, API-key redaction.

Each test fails for a named defect in the current code (failing-first / RED phase).
"""

import logging
import subprocess

from shani_chronoa.capabilities import gate_label
from shani_chronoa.config import SCHEMA_ID

import pytest


class TestModelDownloadConsent:
    """`model-download-enabled` is the only setting that causes a download."""

    def test_it_defaults_to_true(self, chronoa_config):
        # Given: a fresh config with the schema's default
        # When: the consent gate is read
        # Then: on, because a fresh install that cannot download a speech model
        # cannot hear anything, and nine provisioning steps each began by
        # refusing with "Enable 'Model download' in Settings".
        assert chronoa_config.model_download_enabled is True

    def test_shutting_it_refuses_the_download(self, chronoa_config):
        """**The fail-closed half, which the default can no longer supply.**

        The default is deliberately `true`, so "it is off" is no longer
        something a fresh config demonstrates. What still has to hold is that a
        person who does not want downloads can shut the gate and be refused -
        which is the invariant the old `test_it_defaults_to_false` was standing
        in for, and it is asserted here against an explicitly shut key.
        """
        from shani_chronoa.stt_provision import _consent

        chronoa_config.set("model-download-enabled", "false")
        allowed, reason = _consent(chronoa_config)

        assert allowed is False, (
            "the gate was shut and the download was allowed anyway")
        assert "turned off" in reason, reason

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
        """`gsettings get` answers `true`/`false` as *quoted* GVariants, and a
        config that kept the quotes would report `"'false'"` - truthy - to every
        caller.

        **This asserts the type, not the value.** It used to assert four specific
        defaults, inside a class named `TestStringQuoting`, where the values are not
        the subject: the subject is that a boolean arrives as a bool. Two of the four
        were later turned on for good reasons, and the test reported working code as
        broken - the fifth time this repository has encoded a default as an
        assertion instead of a behaviour.
        """
        for name in ("privacy_mode", "auto_start", "cloud_fallback_enabled",
                     "barge_in_vad_enabled", "model_download_enabled"):
            value = getattr(chronoa_config, name)
            assert isinstance(value, bool), (
                f"{name} came back as {type(value).__name__} ({value!r}), not a bool - "
                "a quoted GVariant would read as truthy")
            assert not (isinstance(value, str) and value.strip("'\"")), (
                f"{name} is still quoted: {value!r}")

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
            assert gate_label(_SENSE_CONSENT_KEYS[sense]) in reason, (
                f"{sense} refusal does not name the Settings row it means: "
                f"{reason}")
            assert _SENSE_CONSENT_KEYS[sense] not in reason, (
                f"{sense} refusal names the raw key "
                f"'{_SENSE_CONSENT_KEYS[sense]}', whose spelling no settings "
                f"window shows: {reason}")
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


class TestTheSettingsFileIsTheSingleReader:
    """The old keyfile spelling is gone: one JSON file, read by every caller.

    The invariant these tests used to defend - a second reader must see what
    the app wrote - is unchanged. The witness is now the settings file itself,
    because GSettings is read only during the one-time migration.
    """

    def _path(self):
        from shani_chronoa import settings_file

        return settings_file.user_path()

    def test_the_app_writes_the_persons_file(self, chronoa_config):
        chronoa_config.set("i2c-write-enabled", "true")
        text = self._path().read_text()
        assert '"i2c-write-enabled": true' in text, text

    def test_a_second_instance_reads_the_same_value(self, chronoa_config):
        from shani_chronoa.config import ChronoaConfig

        chronoa_config.set("i2c-write-enabled", "true")
        assert ChronoaConfig().get_bool("i2c-write-enabled") is True

    def test_a_file_edit_is_visible_without_restarting(self, chronoa_config):
        import json

        from shani_chronoa import settings_file

        path = self._path()
        data = json.loads(path.read_text()) if path.exists() else {}
        data["i2c-write-enabled"] = True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        settings_file.invalidate()
        assert chronoa_config.get_bool("i2c-write-enabled") is True

    def test_an_old_gsettings_value_is_migrated_once(self, monkeypatch):
        import json

        from shani_chronoa import settings_file
        from shani_chronoa.config import ChronoaConfig

        monkeypatch.setattr(settings_file, "_gsettings_values",
                            lambda: {"i2c-write-enabled": True})
        monkeypatch.setattr(settings_file, "retired_values", lambda: {})
        ChronoaConfig()
        data = json.loads(self._path().read_text())
        assert data["i2c-write-enabled"] is True
        assert data[settings_file.MIGRATION_KEY] is True

        monkeypatch.setattr(settings_file, "_gsettings_values",
                            lambda: pytest.fail("read GSettings after migration"))
        ChronoaConfig()
