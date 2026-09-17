"""RED tests: local-only privacy must reject non-localhost Ollama hosts.

Each test fails for a named defect in the current code (failing-first / RED phase).
"""

from unittest.mock import patch

import pytest


class TestRemoteOllamaPolicy:
    """In local-only mode the effective Ollama host must never be a remote address."""

    def test_local_only_rejects_remote_ollama_host(self, stubbed_app):
        # Given: local-only privacy mode with a remote ollama-host configured
        stubbed_app.config.set("privacy-mode", "true")
        stubbed_app.config.set("ollama-host", "http://192.168.1.50:11434")
        from shani_chronoa.config import PrivacyManager
        stubbed_app.privacy = PrivacyManager(stubbed_app.config)
        assert stubbed_app.privacy.is_local_only
        # When: components are initialized (the host is chosen for the LLM)
        with patch("shani_chronoa.app.OllamaLLM") as mock_llm_cls:
            mock_llm_cls.return_value.is_available.return_value = False
            stubbed_app._init_components()
            host = mock_llm_cls.call_args.kwargs["host"]
        # Then: the effective host must be localhost, not the remote host
        assert host == "http://localhost:11434"

    def test_remote_ollama_host_used_when_privacy_off(self, stubbed_app):
        # Baseline: with privacy mode off, the configured remote host is used.
        stubbed_app.config.set("privacy-mode", "false")
        stubbed_app.config.set("ollama-host", "http://192.168.1.50:11434")
        from shani_chronoa.config import PrivacyManager
        stubbed_app.privacy = PrivacyManager(stubbed_app.config)
        assert not stubbed_app.privacy.is_local_only
        with patch("shani_chronoa.app.OllamaLLM") as mock_llm_cls:
            mock_llm_cls.return_value.is_available.return_value = False
            stubbed_app._init_components()
            host = mock_llm_cls.call_args.kwargs["host"]
        assert host == "http://192.168.1.50:11434"