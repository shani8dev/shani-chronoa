"""Context sizing from the model's real KV cost and the machine's VRAM.

Ported from Maze-AI's `maze_ai/llm/hardware.py`: the arithmetic is verified
against hand-computed values and, for the parsing path, by writing a real GGUF
with the official `gguf` writer and reading it back through the port. The
kernel-probe functions (`total_vram_bytes` over nvidia-smi/sysfs) are
substituted at the `server_args` boundary; there is no GPU in this
environment and the point here is the decision, not which label reads.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ctxfit, local_llm  # noqa: E402

# A fabricated qwen3-shaped model: 28 full-attention layers, 16 heads, 8 KV
# heads, embedding 2048, so head_dim = 2048 // 16 = 128.
_INFO_FULL = {
    "general.architecture": "qwen3",
    "qwen3.block_count": 28,
    "qwen3.attention.head_count": 16,
    "qwen3.attention.head_count_kv": 8,
    "qwen3.embedding_length": 2048,
}
# 28 layers * 8 kv-heads * (128 + 128) elems * 2 bytes (f16) = 114.688 kB/token.
_BYTES_PER_TOKEN_FULL = 28 * 8 * 256 * 2


class TestKvBytesPerToken:
    def test_full_attention_math(self):
        assert ctxfit.kv_bytes_per_token(_INFO_FULL) == _BYTES_PER_TOKEN_FULL

    def test_hybrid_models_only_count_full_attention_layers(self):
        info = dict(_INFO_FULL)
        info["qwen3.full_attention_interval"] = 3
        full = [i for i in range(28) if (i + 1) % 3 == 0]
        assert ctxfit.kv_bytes_per_token(info) == len(full) * 8 * 256 * 2

    def test_sliding_window_layers_are_not_full_cache(self):
        info = dict(_INFO_FULL)
        info["qwen3.block_count"] = 4
        info["qwen3.attention.sliding_window"] = 4096
        info["qwen3.attention.sliding_window_pattern"] = [True, True, True, True]
        assert ctxfit.kv_bytes_per_token(info) == 0

    def test_missing_metadata_returns_zero(self):
        assert ctxfit.kv_bytes_per_token({}) == 0
        assert ctxfit.kv_bytes_per_token({"general.architecture": ""}) == 0


class TestEstimateAndLadder:
    def test_estimate_need_scales_linearly(self):
        per_token = 128 * 1024  # 128 kB/token
        base = ctxfit.estimate_need(1_000_000_000, 0, per_token)
        later = ctxfit.estimate_need(1_000_000_000, 8192, per_token)
        assert later - base == 8192 * per_token

    def test_context_that_fits_picks_the_largest_fitting_rung(self):
        # 4 GiB weights in a 6 GiB card at ~115 kB/token: 32768 (8.15 GiB) and
        # 16384 (6.14 GiB) both overflow even the bare-fit check; 8192 (5.27 GiB)
        # is the first that fits inside the 0.9-budget.
        per_token = _BYTES_PER_TOKEN_FULL
        got = ctxfit.context_that_fits(4 * 1024**3, total_bytes=6 * 1024**3, per_token=per_token)
        assert got == 8192

    def test_context_that_fits_includes_the_tight_case(self):
        # 4 GiB of weights plus a full 32768-token KV (~3.5 GiB) is ~7.9 GiB on
        # an 8 GiB card: not 'gpu' by the 0.9 budget, but 'tight' - and 'tight'
        # is a fit, so this is the chosen rung.
        per_token = _BYTES_PER_TOKEN_FULL
        got = ctxfit.context_that_fits(4 * 1024**3, total_bytes=8 * 1024**3, per_token=per_token)
        assert got == 32768

    def test_unknown_card_shrinks_to_the_ladder_floor(self, monkeypatch):
        monkeypatch.setattr(ctxfit, "total_vram_bytes", lambda: None)
        got = ctxfit.context_that_fits(0, total_bytes=None, per_token=None)
        assert got == 2048, "no VRAM geometry is not a sign of permission to grow"

    def test_estimate_fit_on_known_numbers(self):
        report = ctxfit.estimate_fit(4 * 1024**3, 8192, total_bytes=8 * 1024**3,
                                     per_token=_BYTES_PER_TOKEN_FULL)
        assert report.verdict == "gpu"
        assert report.fits
        tight = ctxfit.estimate_fit(7 * 1024**3, 8192, total_bytes=8 * 1024**3,
                                    per_token=_BYTES_PER_TOKEN_FULL)
        assert tight.verdict in ("tight", "spill")


class TestGgufParsing:
    def test_real_gguf_rounds_trip(self, tmp_path):
        pytest.importorskip("gguf")
        from gguf import GGUFWriter

        path = tmp_path / "tiny.gguf"
        writer = GGUFWriter(str(path), arch="qwen3")
        writer.add_block_count(28)
        writer.add_embedding_length(2048)
        writer.add_head_count(16)
        writer.add_head_count_kv(8)
        writer.add_file_type(1)
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.close()

        info = ctxfit.gguf_model_info(path)
        assert info is not None
        assert info["general.architecture"] == "qwen3"
        assert info["qwen3.block_count"] == 28
        assert ctxfit.kv_bytes_per_token(info) == _BYTES_PER_TOKEN_FULL

    def test_a_missing_file_is_a_clean_fallback(self, tmp_path):
        assert ctxfit.gguf_model_info(tmp_path / "nope.gguf") is None


class TestServerArgsContext:
    def _write_current(self, tmp_path, monkeypatch):
        pytest.importorskip("gguf")
        from gguf import GGUFWriter

        path = tmp_path / "model.gguf"
        writer = GGUFWriter(str(path), arch="qwen3")
        writer.add_block_count(28)
        writer.add_embedding_length(2048)
        writer.add_head_count(16)
        writer.add_head_count_kv(8)
        writer.add_file_type(1)
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.close()
        # a GGUF this size pretends the harness has no real model; the fit
        # math keys off the *file size*, so truncate to a plausible weight.
        import os as _os
        _os.truncate(path, 4 * 1024**3)
        monkeypatch.setattr(local_llm, "current_link", lambda: path)
        return path

    def _args_ctx(self) -> str:
        args = local_llm.server_args(devices=["intel0:0"])
        return args[args.index("-c") + 1]

    def test_gpu_uses_the_fit(self, tmp_path, monkeypatch):
        self._write_current(tmp_path, monkeypatch)
        monkeypatch.setattr(ctxfit, "total_vram_bytes", lambda: 8 * 1024**3)
        # the fabricated model + card geometry fits 32768 ('tight'), per the
        # cv-fitting branch documented on estimate_fit.
        args_ctx = self._args_ctx()
        assert args_ctx == "32768"

    def test_no_gpu_keeps_8192(self, tmp_path, monkeypatch):
        self._write_current(tmp_path, monkeypatch)
        args = local_llm.server_args(devices=[])
        assert args[args.index("-c") + 1] == "8192"

    def test_missing_metadata_keeps_8192(self, tmp_path, monkeypatch):
        (tmp_path / "model.gguf").write_bytes(b"not a gguf")
        monkeypatch.setattr(local_llm, "current_link", lambda: tmp_path / "model.gguf")
        args = local_llm.server_args(devices=["intel0:0"])
        assert args[args.index("-c") + 1] == "8192"
