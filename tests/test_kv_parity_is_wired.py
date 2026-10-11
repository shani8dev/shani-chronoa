"""The cross-check between the GGUF header and the model's own tensors.

`ctxfit.kv_width_from_shapes` was written as *"a second, independent derivation of
the number `kv_bytes_per_token` computes from metadata"* - documented, unit
tested, and with **no caller in `usr/` at all**. A cross-check nobody performs is
not a cross-check: the module could have been misreading every convention in the
header for its whole life and nothing would have noticed, because the two routes
never met.

These tests are the meeting. The comparison lives in `ctxfit`, the caller is
`local_llm._context_arg()`, and the thing being pinned is that the caller
actually **logs** what it is told - which is the difference between a check and a
function nobody calls.

The last test is the one that would have caught the scoping mistake this fix made
first: the cross-check was wired inside `if info and weights and total:`, and
`total` is `total_vram_bytes()`, which is None on a CPU-only machine. Chronoa's
default local brain runs on CPU, so as first wired the check never ran where it
matters most.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
import sys  # noqa: E402
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ctxfit  # noqa: E402
from shani_chronoa import local_llm  # noqa: E402


@pytest.fixture
def model(tmp_path):
    """A copy of a real GGUF, so the header and the tensors are both genuine.

    `CHRONOA_KV_PARITY_MODEL` and nothing else - the convention
    `test_ctxfit.py`'s parity test already set. There is deliberately **no**
    fallback to a path of this file's own: the suite redirects `HOME` and
    `XDG_DATA_HOME` per test, so a path resolved from `Path.home()` at fixture
    time is the scratch directory and every test in this file would silently
    skip - a check that cannot run is the failure mode this file exists to
    prevent.

    With no variable set the tests skip and say so, because a comparison between
    two readings of a file needs the file.
    """
    import os

    named = os.environ.get("CHRONOA_KV_PARITY_MODEL", "")
    source = Path(named) if named else None
    if source is None:
        pytest.skip("no CHRONOA_KV_PARITY_MODEL set, so no real GGUF to "
                    "cross-check the two routes against")
    if not source.is_file():
        pytest.fail(f"CHRONOA_KV_PARITY_MODEL names {source}, which is not a file")
    target = tmp_path / "model.gguf"
    shutil.copy(source, target)
    return target


def _doctored(info):
    """The harvest's exact failure: a misreading of `attention.head_count_kv`.

    Everything else is left as the file has it, so the disagreement is about one
    number and nothing else.
    """
    arch = str(info.get("general.architecture") or "")
    bad = dict(info)
    bad[f"{arch}.attention.head_count_kv"] = 6
    return bad


class TestTheTwoRoutesAreActuallyCompared:
    """`kv_width_from_shapes` was written as a second, independent derivation of
    the number `kv_bytes_per_token` computes from metadata - documented, tested,
    and with **no caller in `usr/` at all**. A cross-check nobody performs is not
    a cross-check: the module could have been misreading every convention in the
    header for its whole life and nothing would ever have noticed, because the
    two routes never met.

    `kv_width_disagreement()` is the meeting, and `local_llm.context_fit()` is
    now the caller that logs what it says.
    """

    @staticmethod
    def _info(head_count_kv=8):
        return {
            "general.architecture": "qwen3",
            "qwen3.block_count": 4,
            "qwen3.attention.head_count": 16,
            "qwen3.attention.head_count_kv": head_count_kv,
            "qwen3.embedding_length": 2048,
        }

    @classmethod
    def _shapes(cls, width=1024):
        return [(f"blk.{i}.attn_k.weight", [2048, width]) for i in range(4)] + \
               [(f"blk.{i}.attn_v.weight", [2048, width]) for i in range(4)]

    def test_a_width_that_agrees_says_nothing(self):
        from shani_chronoa import ctxfit
        assert ctxfit.metadata_kv_width(self._info()) == 1024
        assert ctxfit.kv_width_from_shapes(self._shapes()) == 1024

    def test_a_width_that_disagrees_is_named(self):
        """The harvest's exact failure: a misreading of `head_count_kv`. The
        header says one thing, the tensors say another, and the sentence says
        which is being used - so the wrong number is sayable rather than merely
        wrong."""
        from shani_chronoa import ctxfit
        truth = ctxfit.metadata_kv_width(self._info(head_count_kv=6))
        assert truth == 768, truth
        assert ctxfit.kv_width_from_shapes(self._shapes()) == 1024
        # The comparison needs a file, so it is driven through the reader seam.
        said = ctxfit.kv_width_disagreement_from_shapes(
            self._shapes(), self._info(head_count_kv=6))
        assert said and "768" in said and "1024" in said, said

    def test_no_comparison_is_not_the_same_answer_as_agreement(self):
        """`None` means *could not compare*, never *they agree*. A caller that
        reads a None as agreement has a check that passes by not running - the
        "a negative control that cannot fail" trap in a new place."""
        from shani_chronoa import ctxfit
        assert ctxfit.kv_width_disagreement_from_shapes(
            self._shapes(), {}) is None
        assert ctxfit.kv_width_disagreement_from_shapes([], self._info()) is None
        # A per-layer list is a real state the shape reader refuses, not a
        # disagreement to report.
        info = self._info()
        info["qwen3.attention.head_count_kv"] = [8, 8, 8, 8]
        assert ctxfit.kv_width_disagreement_from_shapes(
            self._shapes(), info) is None

    def test_the_disagreement_is_reported_and_not_acted_on(self):
        """The header stays the authority. It is the only route that knows about
        shared caches and hybrid layer patterns, and silently switching to the
        shapes on the strength of one file would trade a visible wrong number for
        an invisible right one."""
        from shani_chronoa import ctxfit
        info = self._info(head_count_kv=6)
        said = ctxfit.kv_width_disagreement_from_shapes(
            self._shapes(), info)
        assert said, said
        # The context decision still comes from the metadata route.
        assert ctxfit.kv_bytes_per_token(info) == 4 * 2 * 768 * 2

    def test_a_real_gguf_on_disk_agrees(self):
        """The end-to-end route, against a file rather than a fixture - because
        the whole point is a comparison between two readings of the *same* file,
        and a fixture built from one of them cannot show a disagreement the
        other way round.

        `CHRONOA_KV_PARITY_MODEL` rather than a path of its own, matching the
        convention the parity test above already set: without the variable the
        suite redirects HOME and XDG_DATA_HOME, so a model reachable from the
        developer's shell is not reachable from a test. A skip that says why is
        the honest outcome; a hardcoded absolute path is a test that runs on one
        box and silently skips everywhere else.
        """
        import os

        pytest.importorskip("gguf")
        from shani_chronoa import ctxfit
        named = os.environ.get("CHRONOA_KV_PARITY_MODEL", "")
        if not named:
            pytest.skip("no CHRONOA_KV_PARITY_MODEL set, so no real GGUF to "
                        "compare the two routes against")
        real = Path(named)
        if not real.is_file():
            pytest.fail(f"CHRONOA_KV_PARITY_MODEL names {real}, which is not a file")
        info = ctxfit.gguf_model_info(real)
        assert info, "could not read the named model's header"
        assert ctxfit.kv_width_disagreement(real, info) is None, (
            "the two derivations disagree about a real model on this box")


class TestTheCallerLogsWhatTheCheckSaid:
    def test_a_disagreement_reaches_the_log(self, model, caplog):
        info = ctxfit.gguf_model_info(model)
        original = ctxfit.gguf_model_info
        local_llm.ctxfit.gguf_model_info = (
            lambda p: _doctored(info) if str(p) == str(model) else original(p))
        local_llm.current_link = lambda: model
        try:
            with caplog.at_level(logging.WARNING):
                local_llm._context_arg()
        finally:
            local_llm.ctxfit.gguf_model_info = original
        assert any("disagree" in r.message for r in caplog.records), (
            "the cross-check found a disagreement and the caller dropped it")

    def test_an_agreement_says_nothing(self, model, caplog):
        local_llm.current_link = lambda: model
        with caplog.at_level(logging.WARNING):
            local_llm._context_arg()
        assert not any("disagree" in r.message for r in caplog.records), (
            "a model whose two routes agree produced a disagreement warning")

    def test_a_disagreement_does_not_change_the_decision(self, model):
        """Reported and **not acted on**. The header stays the authority because
        it is the only route that knows about shared caches and hybrid layer
        patterns, and silently switching on the strength of one file would trade
        a visible wrong number for an invisible right one."""
        info = ctxfit.gguf_model_info(model)
        original = ctxfit.gguf_model_info
        local_llm.ctxfit.gguf_model_info = (
            lambda p: _doctored(info) if str(p) == str(model) else original(p))
        local_llm.current_link = lambda: model
        try:
            doctored = local_llm._context_arg()
        finally:
            local_llm.ctxfit.gguf_model_info = original
        local_llm.current_link = lambda: model
        plain = local_llm._context_arg()
        assert doctored == plain, (doctored, plain)


class TestTheCheckRunsWithoutAGpu:
    """The scoping mistake this fix made first, pinned so it cannot come back.

    The cross-check was wired inside `if info and weights and total:`, and
    `total` is `total_vram_bytes()` - None on a CPU-only machine. Chronoa's
    default local brain runs on CPU, so as first wired the check never ran where
    it matters most, and no test here would have failed because every one of them
    would have skipped on the missing model anyway.
    """

    def test_it_runs_on_a_machine_with_no_vram(self, model, monkeypatch, caplog):
        info = ctxfit.gguf_model_info(model)
        monkeypatch.setattr(ctxfit, "total_vram_bytes", lambda: None)
        monkeypatch.setattr(ctxfit, "free_vram_bytes", lambda: None)
        original = ctxfit.gguf_model_info
        local_llm.ctxfit.gguf_model_info = (
            lambda p: _doctored(info) if str(p) == str(model) else original(p))
        monkeypatch.setattr(local_llm, "current_link", lambda: model)
        try:
            with caplog.at_level(logging.WARNING):
                local_llm._context_arg()
        finally:
            local_llm.ctxfit.gguf_model_info = original
        assert any("disagree" in r.message for r in caplog.records), (
            "the cross-check did not run on a machine with no VRAM - it is "
            "scoped inside the branch that needs it")
