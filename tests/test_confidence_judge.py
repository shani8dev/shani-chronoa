"""A cheap self-check before an irreversible action, and an honest not-sure path.

Two properties matter more than the score: the judge must not become an
accidental egress event or a second gate that overrides consent, and a broken
check must say "I'm not sure" rather than quietly approving the call.
"""

from __future__ import annotations

import asyncio

import pytest

from shani_chronoa import assistant as assistant_mod
from shani_chronoa import confidence, history_repair


class _Config:
    def __init__(self, thinking="off"):
        self.thinking = thinking

    def get(self, key, default=""):
        return self.thinking if key == confidence.JUDGE_FLAG else default


class _LLM:
    """A local backend whose one judge answer is queued."""

    local = True
    stream_supported = False

    def __init__(self, answer, replies=None):
        self.answer = answer
        self.replies = list(replies or [])
        self.calls = 0

    async def chat_message(self, messages, tools=None, stream=False):
        self.calls += 1
        if self.replies:
            return self.replies.pop(0)
        return {"role": "assistant", "content": self.answer}


class _CloudLLM(_LLM):
    local = False


class TestTheCheckIsCheapAndOptIn:
    def test_off_by_default_never_calls_a_model(self):
        llm = _LLM("UNSURE")
        check = asyncio.run(
            confidence.judge("delete_file", {}, llm=llm, config=_Config())
        )
        assert check.confident and check.source == "off"
        assert llm.calls == 0

    def test_an_unreadable_config_is_off(self):
        class Broken:
            def get(self, *args):
                raise RuntimeError("no schema")

        check = asyncio.run(
            confidence.judge("delete_file", {}, llm=_LLM("UNSURE"), config=Broken())
        )
        assert check.confident and check.source == "off"

    def test_risk_comes_from_the_existing_consent_table(self):
        assert "delete_file" in confidence.risky_tools()
        assert "get_datetime" not in confidence.risky_tools()
        assert not confidence.is_risky("get_datetime")

    def test_the_cheap_check_catches_a_call_with_no_target(self):
        check = confidence.cheap_check("delete_file", {}, schema={})
        assert not check.confident
        assert "target" in check.reason

    def test_an_ordinary_read_is_not_held_up(self):
        check = confidence.cheap_check("get_datetime", {}, schema={})
        assert check.confident

    def test_a_named_target_passes(self):
        check = confidence.cheap_check("delete_file", {"path": "/tmp/x"})
        assert check.confident


class TestTheModelPass:
    def test_a_cloud_backend_is_never_asked(self):
        llm = _CloudLLM("UNSURE")
        check = asyncio.run(
            confidence.judge(
                "delete_file", {"path": "/tmp/x"}, llm=llm, config=_Config("low")
            )
        )
        assert check.confident and check.source == "heuristic"
        assert llm.calls == 0

    def test_local_unsure_is_an_explicit_refusal(self):
        llm = _LLM("UNSURE")
        check = asyncio.run(
            confidence.judge(
                "delete_file", {"path": "/tmp/x"}, llm=llm, config=_Config("low")
            )
        )
        assert not check.confident and check.source == "judge"
        assert llm.calls == 1

    def test_local_sure_clears_the_call(self):
        check = asyncio.run(
            confidence.judge(
                "delete_file",
                {"path": "/tmp/x"},
                llm=_LLM("SURE"),
                config=_Config("high"),
            )
        )
        assert check.confident and check.source == "judge"

    def test_a_timeout_is_not_sure(self):
        class Slow(_LLM):
            async def chat_message(self, messages, tools=None, stream=False):
                await asyncio.sleep(0.5)
                return {"role": "assistant", "content": "SURE"}

        check = asyncio.run(
            confidence.judge(
                "delete_file",
                {"path": "/tmp/x"},
                llm=Slow(""),
                config=_Config("low"),
                timeout=0.01,
            )
        )
        assert not check.confident and check.source == "judge-error"

    def test_an_unusable_answer_is_not_sure(self):
        check = asyncio.run(
            confidence.judge(
                "delete_file",
                {"path": "/tmp/x"},
                llm=_LLM("let me think"),
                config=_Config("low"),
            )
        )
        assert not check.confident

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("SURE", True),
            ("unsure", False),
            ("UNSURE. I cannot tell", False),
            ("I'm not sure", False),
            ("maybe", None),
            (None, None),
        ],
    )
    def test_the_judge_vocabulary_closes_around_unsure(self, text, expected):
        assert confidence.parse_judge(text) is expected


class TestTheAssistantUsesIt:
    def _bot(self, replies):
        llm = _LLM("", replies)
        return assistant_mod.Assistant(llm, config=_Config("low")), llm

    def test_a_skipped_risky_call_leaves_a_truthful_tool_result(self, monkeypatch):
        calls = []

        def _no_run(*args, **kwargs):
            calls.append(args)
            raise AssertionError("the self-check refused this call; it must not run")

        monkeypatch.setattr(assistant_mod, "_run_tool", _no_run)
        bot, llm = self._bot(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "delete_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "assistant", "content": "I'm not sure which file you mean."},
            ]
        )
        answer = asyncio.run(bot.handle("delete it"))
        assert "not sure" in answer
        assert calls == [], "the risky tool ran even though the check was unsure"
        repairs = [
            m
            for m in bot._history
            if m.get("role") == "tool"
            and m.get(history_repair.REASON_KEY) == "uncertain"
        ]
        assert len(repairs) == 1

    def test_the_not_sure_sentence_reaches_the_model(self):
        bot, llm = self._bot([])
        result = history_repair.synthesized_tool_result(
            {"id": "c1", "function": {"name": "delete_file"}}, "uncertain"
        )
        assert confidence.UNSURE_SENTENCE in result["content"]
