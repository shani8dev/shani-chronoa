"""RED tests: GSettings string quoting, fresh-instance round trips, empty defaults, API-key redaction.

Each test fails for a named defect in the current code (failing-first / RED phase).
"""

import logging
import subprocess

import pytest


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

    def test_wake_word_model_default_is_unquoted(self, chronoa_config):
        # Given: a fresh config (schema default wake-word-model='hey_jarvis')
        # When: the wake-word-model property is read
        wake_word_model = chronoa_config.wake_word_model
        # Then: it must be the plain model id, not the GVariant-quoted value
        assert wake_word_model == "hey_jarvis"

    def test_boolean_settings_parse_correctly(self, chronoa_config):
        # Baseline: booleans already round-trip (gsettings prints them unquoted).
        assert chronoa_config.privacy_mode is True
        assert chronoa_config.auto_start is False
        assert chronoa_config.cloud_fallback_enabled is False
        assert chronoa_config.barge_in_vad_enabled is False


class TestFreshInstanceRoundTrip:
    """A value set through one instance must be read back unquoted by a fresh instance."""

    def test_set_then_fresh_instance_roundtrip(self, chronoa_config, gsettings_env):
        # Given: a value set through one config instance
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

        with patch("shani_chronoa.app.OllamaLLM") as mock_llm_cls:
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
        from shani_chronoa.config import HardwareProfile
        hw = HardwareProfile.__new__(HardwareProfile)
        hw.profile = "low"
        stubbed_app.hardware = hw
        # When: components are initialized
        with patch("shani_chronoa.app.OllamaLLM") as mock_llm_cls, \
             patch("shani_chronoa.app.WhisperSTT") as mock_stt_cls:
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
        from shani_chronoa.config import HardwareProfile
        hw = HardwareProfile.__new__(HardwareProfile)
        hw.profile = "gpu"
        stubbed_app.hardware = hw
        # When: components are initialized
        with patch("shani_chronoa.app.OllamaLLM") as mock_llm_cls:
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
        from shani_chronoa.config import HardwareProfile
        hw = HardwareProfile.__new__(HardwareProfile)
        hw.profile = "medium"
        stubbed_app.hardware = hw
        # When: components are initialized
        with patch("shani_chronoa.app.OllamaLLM") as mock_llm_cls:
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
        from shani_chronoa.config import HardwareProfile
        hw = HardwareProfile.__new__(HardwareProfile)
        hw.profile = "low"
        stubbed_app.hardware = hw
        # When: components are initialized
        with patch("shani_chronoa.app.OllamaLLM") as mock_llm_cls:
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

        monkeypatch.setattr("shani_chronoa.config.subprocess.run", _fake_run)
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