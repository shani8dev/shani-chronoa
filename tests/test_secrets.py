"""Tests for secrets_manager sanitization and vault functionality."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr/lib/shani-chronoa"))

from shani_chronoa.secrets_manager import SecretsManager


class TestSanitizeTextForLLM:
    """Tests for sanitize_text_for_llm()."""

    @pytest.fixture(autouse=True)
    def _fresh_manager(self, tmp_path):
        """Create a fresh SecretsManager with a temp vault for each test."""
        self.sm = SecretsManager(secrets_file=tmp_path / "vault.json")

    def test_no_secrets_returns_text_unchanged(self):
        """No stored secrets means text passes through untouched."""
        text = "hello world"
        assert self.sm.sanitize_text_for_llm(text) == "hello world"

    def test_empty_text_returns_empty(self):
        """Empty/None text passes through."""
        assert self.sm.sanitize_text_for_llm("") == ""
        assert self.sm.sanitize_text_for_llm(None) is None

    def test_secret_value_is_redacted(self):
        """A stored secret's value is replaced with $SECRET:<key>."""
        self.sm.set_secret("API_KEY", "sk-secret123", "Test API key")
        text = "My key is sk-secret123 and it's great"
        result = self.sm.sanitize_text_for_llm(text)
        assert "$SECRET:API_KEY" in result
        assert "sk-secret123" not in result

    def test_multiple_secrets_redacted(self):
        """Multiple secret values are all redacted."""
        self.sm.set_secret("OPENAI_KEY", "openai-abc", "OpenAI key")
        self.sm.set_secret("ANTHROPIC_KEY", "anthropic-xyz", "Anthropic key")
        text = "openai-abc and anthropic-xyz both work"
        result = self.sm.sanitize_text_for_llm(text)
        assert "$SECRET:OPENAI_KEY" in result
        assert "$SECRET:ANTHROPIC_KEY" in result
        assert "openai-abc" not in result
        assert "anthropic-xyz" not in result

    def test_partial_secret_match_not_redacted(self):
        """A partial match that is not the full secret is not redacted."""
        self.sm.set_secret("API_KEY", "secret-full-value", "Test")
        text = "secre"  # partial, not the full secret
        result = self.sm.sanitize_text_for_llm(text)
        assert result == "secre"  # unchanged

    def test_secret_with_special_chars(self):
        """Secrets with special characters are properly redacted."""
        self.sm.set_secret("TOKEN", "abc!@#$%", "Test token")
        text = "Token is abc!@#$% here"
        result = self.sm.sanitize_text_for_llm(text)
        assert "$SECRET:TOKEN" in result
        assert "abc!@#$%" not in result

    def test_no_false_positives_on_substrings(self):
        """A string that contains part of a secret but isn't the secret itself."""
        self.sm.set_secret("API_KEY", "supersecretkey123", "Test")
        text = "super is a common prefix"
        result = self.sm.sanitize_text_for_llm(text)
        assert result == "super is a common prefix"  # unchanged

    def test_sanitized_output_contains_secret_placeholders(self):
        """The sanitized output contains placeholders, not raw secret values."""
        self.sm.set_secret("API_KEY", "real-key-value", "Test API key")
        text = "Use real-key-value to authenticate"
        result = self.sm.sanitize_text_for_llm(text)
        assert "$SECRET:API_KEY" in result
        assert "real-key-value" not in result


class TestSecretsManagerBasic:
    """Tests for basic SecretsManager operations."""

    @pytest.fixture(autouse=True)
    def _fresh_manager(self, tmp_path):
        self.sm = SecretsManager(secrets_file=tmp_path / "vault.json")

    def test_set_and_get_secret(self):
        """set_secret stores and get_secret retrieves correctly."""
        self.sm.set_secret("TEST_KEY", "test_value", "Description")
        assert self.sm.get_secret("TEST_KEY") == "test_value"

    def test_get_missing_secret(self):
        """get_secret returns None for missing keys."""
        assert self.sm.get_secret("NONEXISTENT") is None

    def test_delete_secret(self):
        """delete_secret removes a secret."""
        self.sm.set_secret("TEST_KEY", "val", "Test")
        assert self.sm.delete_secret("TEST_KEY") is True
        assert self.sm.get_secret("TEST_KEY") is None

    def test_list_secrets_masked(self):
        """list_secrets returns masked previews, not raw values."""
        self.sm.set_secret("API_KEY", "1234567890abcdef", "Test")
        secrets = self.sm.list_secrets()
        assert len(secrets) == 1
        masked = secrets[0]["masked"]
        assert "1234567890abcdef" not in masked
        assert masked != "***"  # Should have masked preview

    def test_vault_file_permissions(self, tmp_path):
        """vault.json is created with 0o600 permissions."""
        self.sm.set_secret("TEST", "val", "Test")
        vault_path = tmp_path / "vault.json"
        assert vault_path.exists()
        import stat
        mode = vault_path.stat().st_mode
        assert mode & 0o777 == 0o600

    def test_sanitize_with_no_vault_file(self, tmp_path):
        """sanitize_text_for_llm works when vault file doesn't exist yet."""
        sm = SecretsManager(secrets_file=tmp_path / "nonexistent.json")
        result = sm.sanitize_text_for_llm("hello world")
        assert result == "hello world"
