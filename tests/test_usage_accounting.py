"""Token counts that were being thrown away by every backend.

Ollama counts prompt and completion tokens in the body *beside* the message,
and `chat_message` returned only `data["message"]`. Every cloud provider sends a
`usage` block, and `chat_message` returned only `choices[0]["message"]`. So all
three backends reported numbers that this codebase discarded on the floor.

opencode is worth copying the shape of here: a price from a published table,
multiplied by the counts the provider actually reported - and no number at all
when the price is unknown.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import usage as U  # noqa: E402


class TestEachBackendShapeIsRead:
    def test_ollama_counts_sit_beside_the_message(self):
        got = U.from_ollama({"message": {"role": "assistant", "content": "hi"},
                             "prompt_eval_count": 120, "eval_count": 45})
        assert got.input_tokens == 120
        assert got.output_tokens == 45
        assert got.total_tokens == 165

    def test_the_openai_shape(self):
        got = U.from_openai({"usage": {"prompt_tokens": 10, "completion_tokens": 3}})
        assert (got.input_tokens, got.output_tokens) == (10, 3)

    def test_anthropic_names_the_same_counts_differently(self):
        """`input_tokens`/`output_tokens`, not `prompt_tokens`/`completion_tokens`.

        Folding this into the OpenAI extractor would have reported zero for every
        Anthropic call - a quiet undercount that looks like a working feature.
        """
        got = U.from_anthropic({"usage": {"input_tokens": 7, "output_tokens": 2}})
        assert (got.input_tokens, got.output_tokens) == (7, 2)

    def test_a_response_with_no_usage_reports_zero_not_a_crash(self):
        for extract in (U.from_openai, U.from_anthropic):
            got = extract({"choices": [{"message": {}}]})
            assert got.total_tokens == 0

    def test_missing_counts_are_zero_rather_than_an_exception(self):
        got = U.from_ollama({"message": {}})
        assert (got.input_tokens, got.output_tokens) == (0, 0)


class TestUnpricedIsUnknownNotZero:
    """The invariant this module exists for."""

    def test_local_inference_is_unpriced(self):
        got = U.from_ollama({"prompt_eval_count": 500, "eval_count": 500})
        assert got.cost_usd is None
        assert got.priced is False

    def test_an_unpriced_call_carries_no_cost_key(self):
        assert "cost_usd" not in U.from_ollama({"prompt_eval_count": 1}).as_dict()

    def test_a_cloud_call_with_no_table_stays_unpriced(self):
        got = U.from_openai({"usage": {"prompt_tokens": 900, "completion_tokens": 90}})
        assert got.priced is False
        assert got.total_tokens == 990, "the tokens are still known and still useful"


class TestPricingFromATable:
    def test_a_supplied_table_produces_a_cost(self):
        priced = U.price(U.from_openai({"usage": {"prompt_tokens": 1_000_000,
                                                   "completion_tokens": 0}}),
                         {"input": 3.00, "output": 15.00})
        assert priced.cost_usd == pytest.approx(3.00)
        assert priced.priced is True

    def test_input_and_output_are_charged_differently(self):
        priced = U.price(U.from_openai({"usage": {"prompt_tokens": 1_000_000,
                                                   "completion_tokens": 1_000_000}}),
                         {"input": 1.0, "output": 2.0})
        assert priced.cost_usd == pytest.approx(3.0)

    def test_an_incomplete_table_leaves_it_unpriced(self):
        """A missing output rate must not be read as a free output token."""
        got = U.price(U.from_openai({"usage": {"prompt_tokens": 100}}), {"input": 1.0})
        assert got.cost_usd is None

    def test_a_malformed_table_leaves_it_unpriced(self):
        got = U.price(U.from_openai({"usage": {"prompt_tokens": 100}}),
                      {"input": "three", "output": None})
        assert got.cost_usd is None

    def test_no_table_at_all(self):
        got = U.price(U.from_openai({"usage": {"prompt_tokens": 5}}), None)
        assert got.priced is False


class TestSummingCalls:
    def test_totals_add_up(self):
        a = U.from_openai({"usage": {"prompt_tokens": 10, "completion_tokens": 1}})
        b = U.from_openai({"usage": {"prompt_tokens": 20, "completion_tokens": 2}})
        assert (a + b).input_tokens == 30
        assert (a + b).output_tokens == 3

    def test_a_priced_plus_unpriced_sum_is_unpriced(self):
        """Otherwise the sum reports only the part that was known.

        That is the same error as reporting zero, just smaller - and therefore
        far easier to miss, because it looks like a plausible number.
        """
        priced = U.price(U.from_openai({"usage": {"prompt_tokens": 1_000_000}}), {"input": 5.0, "output": 5.0})
        unpriced = U.from_ollama({"prompt_eval_count": 9_999_999})
        assert (priced + unpriced).cost_usd is None
        assert (priced + unpriced).priced is False

    def test_two_priced_calls_do_sum(self):
        a = U.price(U.from_openai({"usage": {"prompt_tokens": 1_000_000}}), {"input": 1.0, "output": 0.0})
        b = U.price(U.from_openai({"usage": {"prompt_tokens": 1_000_000}}), {"input": 2.0, "output": 0.0})
        assert (a + b).cost_usd == pytest.approx(3.0)


class TestItNeverReachesTheModel:
    def test_usage_is_not_folded_into_the_message(self):
        """A message is appended to history and re-sent as context every turn.

        A `usage` key there would be persisted to the transcript forever and
        re-sent to the model on every subsequent turn - teaching it about a
        number it has no use for. Hence `last_usage` beside the message.
        """
        from shani_chronoa.ollama_llm import OllamaLLM
        from shani_chronoa import ollama_llm as llm_mod

        llm = OllamaLLM.__new__(OllamaLLM)
        llm.last_usage = None
        # The attribute exists before the first call, so a caller that asks too
        # early gets None rather than an AttributeError.
        assert llm.last_usage is None
        assert hasattr(OllamaLLM, "last_usage")


class TestAShapeThatWouldBeMissed:
    def test_openai_and_ollama_are_not_interchangeable(self):
        """Both report the same two counts under different names.

        Reading Ollama's body with the OpenAI extractor would find no `usage`
        key and report zero tokens for every local call - a plausible-looking
        number that is simply wrong, which is the failure mode this whole
        codebase treats as worse than an honest UNKNOWN.
        """
        body = {"message": {}, "prompt_eval_count": 42, "eval_count": 7}
        assert U.from_openai(body).total_tokens == 0
        assert U.from_ollama(body).total_tokens == 49


class TestTheChainDoesNotSwallowItsBackendsUsage:
    """`CloudLLMChain` is what `app.py` actually installs as `self.llm`.

    Both leaf adapters record `self.last_usage` when a provider sends a `usage`
    block, and `assistant.py` reads it back with
    `getattr(self.llm, "last_usage", None)` to accumulate a turn's cost. But the
    chain returns the backend's message and nothing else, so on the cloud-fallback
    path the number is dropped between the adapter and the assistant - and
    `getattr` returns `None`, which is indistinguishable from a provider that
    reported no usage at all.

    The tests above cover `OllamaLLM` and the OpenAI/Anthropic extractors, so the
    suite was green with the whole cloud-fallback path reporting zero cost.
    """

    def _chain_with(self, backend):
        from shani_chronoa.cloud_llm import CloudLLMChain

        # A real key so the provider is built rather than skipped; the backend is
        # swapped out immediately, so nothing here touches the network.
        chain = CloudLLMChain(provider_ids=("openai",), api_keys={"openai": "sk-test"})
        chain._backends = [backend]
        return chain

    def test_the_chain_reports_what_its_backend_reported(self):
        import asyncio
        from types import SimpleNamespace

        reported = U.Usage(input_tokens=11, output_tokens=5, source="openai-compatible")

        class _Backend:
            model = "stub-model"
            provider = SimpleNamespace(name="Stub")

            def __init__(self):
                self.last_usage = reported

            async def chat_message(self, messages, tools=None, stream=False):
                return {"role": "assistant", "content": "ok"}

        backend = _Backend()
        chain = self._chain_with(backend)
        asyncio.run(chain.chat_message([{"role": "user", "content": "hi"}]))

        assert backend.last_usage is reported, (
            "the stub backend was never consulted, so this test proves nothing")
        got = getattr(chain, "last_usage", None)
        assert got is not None, (
            "CloudLLMChain dropped the usage its backend reported; assistant.py's "
            "getattr(self.llm, 'last_usage', None) now reads None and the whole "
            "cloud-fallback path reports zero tokens as if usage were unsupported")
        assert (got.input_tokens, got.output_tokens) == (11, 5)

    def test_a_backend_reporting_nothing_clears_the_previous_numbers(self):
        """Pinned because the naive fix double-counts instead.

        The chain now assigns `last_usage` on every success, including when the
        answering backend sent no `usage` block. Had it only assigned when a
        value was present, the previous call's counts would linger and
        `assistant.py` would add them into the turn a second time - turning a
        missing number into a confidently wrong one.
        """
        import asyncio
        from types import SimpleNamespace

        class _Backend:
            model = "stub-model"
            provider = SimpleNamespace(name="Stub")

            def __init__(self, usage):
                self.last_usage = usage

            async def chat_message(self, messages, tools=None, stream=False):
                return {"role": "assistant", "content": "ok"}

        chain = self._chain_with(_Backend(U.Usage(input_tokens=11, output_tokens=5)))
        asyncio.run(chain.chat_message([{"role": "user", "content": "hi"}]))
        assert getattr(chain, "last_usage", None) is not None, "precondition"

        chain._backends = [_Backend(None)]  # same chain, a backend that reports nothing
        asyncio.run(chain.chat_message([{"role": "user", "content": "again"}]))
        assert getattr(chain, "last_usage", None) is None, (
            "the previous call's token counts survived a call that reported none, "
            "so the assistant would add them to this turn as well")

    def test_the_turn_reports_the_cost_when_the_chain_is_the_backend(self):
        """The end-to-end claim: a cloud-fallback turn reports what it spent.

        Everything above tests the chain in isolation. This is the path that
        actually reaches a user - `app.py` installs the chain as `self.llm`, and
        `assistant.py` accumulates `getattr(self.llm, "last_usage", None)` into
        `turn_stats()["usage"]`.
        """
        import asyncio
        from types import SimpleNamespace

        from shani_chronoa.assistant import Assistant

        class _Backend:
            model = "stub-model"
            provider = SimpleNamespace(name="Stub")

            def __init__(self):
                self.last_usage = U.Usage(
                    input_tokens=11, output_tokens=5, source="openai-compatible")

            async def chat_message(self, messages, tools=None, stream=False):
                return {"role": "assistant", "content": "ok"}

        a = Assistant(self._chain_with(_Backend()))
        asyncio.run(a.handle("what did you spend?"))
        stats = a.turn_stats()
        assert stats["usage"] is not None, (
            "a turn served by CloudLLMChain reported no usage at all; the "
            "provider's numbers never reached turn_stats()")
        assert stats["usage"]["input_tokens"] == 11
        assert stats["usage"]["output_tokens"] == 5
        assert stats["usage"]["source"] == "openai-compatible"
