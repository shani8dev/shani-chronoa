"""The reply-style preset: one config key, one appended clause every turn.

A device-wide style belongs in Settings behind one key, identically to what
`voice-style` does for the voice engine - and unlike a persona, the line
constrains phrasing only. A brief reply must report the exact same fact that
an ordinary reply would; it just gets there faster. Same reasoning on every
backend, which is why it lives next to SYSTEM_PROMPT's build rather than at
the delivery layer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.assistant import Assistant, SYSTEM_PROMPT, reply_style_clause  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402


class _Config:
    def __init__(self, style: str) -> None:
        self.reply_style = style


class TestReplyStyleClause:
    def test_ordinary_style_adds_no_clause(self):
        assert reply_style_clause(_Config("ordinary")) == ""

    def test_brief_adds_its_clause(self):
        assert "Be brief" in reply_style_clause(_Config("brief"))

    def test_explanatory_adds_its_clause(self):
        assert "one sentence of mechanism" in reply_style_clause(_Config("explanatory"))

    def test_an_xhr_settings_value_is_ignored(self):
        assert reply_style_clause(_Config("pirate")) == ""

    def test_no_config_is_no_line(self):
        assert reply_style_clause(None) == ""


class TestConfig:
    def test_default_is_ordinary(self):
        assert ChronoaConfig().reply_style == "ordinary"

    def test_round_trip(self):
        ChronoaConfig().set("reply-style", "brief")
        assert ChronoaConfig().reply_style == "brief"
        ChronoaConfig().set("reply-style", "ordinary")


class _LLM:
    def is_available(self):
        return False

    async def chat_message(self, messages, **_):
        return {"role": "assistant", "content": "ok"}


class TestAssistant:
    def _assistant(self, style: str) -> Assistant:
        return Assistant(_LLM(), config=_Config(style))

    def test_the_style_line_rides_with_the_system_prompt(self):
        messages = self._assistant("brief").build_messages()
        assert messages[0]["role"] == "system"
        assert SYSTEM_PROMPT in messages[0]["content"]
        assert "Be brief" in messages[0]["content"]

    def test_the_ordinary_style_leaves_the_prompt_untouched(self):
        messages = self._assistant("ordinary").build_messages()
        assert messages[0]["content"] == SYSTEM_PROMPT

    def test_the_stored_history_is_never_rewritten(self):
        assistant = self._assistant("brief")
        before = assistant._history[0]["content"]
        assistant.build_messages()
        assert assistant._history[0]["content"] == before, (
            "a style change must not silently rewrite the conversation transcript"
        )
        assistant._history[0]["content"] = before  # hygiene for other tests

    def test_no_config_behaviour_is_byte_identical(self):
        assistant = Assistant(_LLM())
        messages = assistant.build_messages()
        assert messages[0]["content"] == SYSTEM_PROMPT
