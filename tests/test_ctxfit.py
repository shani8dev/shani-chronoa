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


class TestTheTensorRouteAgreesWithTheMetadataRoute:
    """Two derivations of one number, one of which cannot inherit the other's misreading.

    `kv_bytes_per_token` reads five GGUF conventions out of the header.
    `kv_width_from_shapes` reads the K/V projection tensors' own shapes. They are
    then asserted to agree - so a module that misreads a convention is caught by
    a route that never looked at it.

    This is edge0's `test_streaming_math.py:1-4` pattern - *"the exact path must
    match a plain fp16 reference implementation"* - on the one place Chronoa does
    arithmetic on a third-party file format. `tests/test_ctxfit.py` above asserts
    against literals written by the same hand that wrote the module, which cannot
    see a shared misreading; this can.
    """

    def _shapes(self, width=1024, dim=2048, layers=4):
        """A fake GGUF tensor list: attn_k/_v at [dim, width], plus noise."""
        out = []
        for i in range(layers):
            out.append((f"blk.{i}.attn_k.weight", [dim, width]))
            out.append((f"blk.{i}.attn_v.weight", [dim, width]))
            out.append((f"blk.{i}.attn_q.weight", [dim, dim]))
            out.append((f"blk.{i}.ffn_gate.weight", [dim, dim * 3]))
        return out

    def _info(self, width=1024, dim=2048, heads=16, layers=4, **over):
        info = {"general.architecture": "qwen3",
                "qwen3.block_count": layers,
                "qwen3.attention.head_count": heads,
                "qwen3.attention.head_count_kv": width // (dim // heads),
                "qwen3.embedding_length": dim}
        info.update(over)
        return info

    def test_the_width_from_tensors_is_the_kv_width_not_the_embedding(self):
        from shani_chronoa import ctxfit
        # GQA: 16 query heads, 8 KV heads, head_dim 128 -> 1024, not the 2048
        # embedding. Confusing the two is the bug this whole class exists for.
        assert ctxfit.kv_width_from_shapes(self._shapes()) == 1024
        assert ctxfit.kv_width_from_shapes(self._shapes(width=512)) == 512

    def test_a_single_layer_of_cache_from_the_two_routes_agree(self):
        """The per-layer figure, which is the convention-heavy half."""
        from shani_chronoa import ctxfit
        shapes = self._shapes()
        info = self._info()
        width = ctxfit.kv_width_from_shapes(shapes)
        assert width == 1024
        # One full-attention layer, f16 cache: K and V, 2 bytes each.
        from_tensors = 2 * width * 2
        per_layer = ctxfit.kv_bytes_per_token(info) // 4      # 4 full layers
        assert per_layer == from_tensors, (
            f"metadata says {per_layer} bytes/token/layer, the tensors say "
            f"{from_tensors} - the two routes disagree about the same model")

    def test_a_per_layer_kv_head_list_agrees_too(self):
        """`head_count_kv` may be a list; the module must not mis-handle it."""
        from shani_chronoa import ctxfit
        info = self._info()
        info["qwen3.attention.head_count_kv"] = [8, 8, 8, 8]
        assert ctxfit.kv_bytes_per_token(info) == 4 * 2 * 1024 * 2

    def test_the_key_length_override_moves_it_away_from_the_tensors(self):
        """**The control: it can disagree, and here is when it should.**

        A model that stores a *wider* cache than its projection width - which is
        what `key_length`/`value_length` express - is genuinely different in the
        two routes, and the metadata route is the right one there. Asserting the
        disagreement is what stops the parity test from being a rubber stamp:
        without it, a `kv_width_from_shapes` that always returned whatever the
        module computed would pass every case above.
        """
        from shani_chronoa import ctxfit
        info = self._info()
        info["qwen3.attention.key_length"] = 2048       # twice head_dim
        assert ctxfit.kv_bytes_per_token(info) == 4 * 8 * (2048 + 128) * 2
        assert ctxfit.kv_width_from_shapes(self._shapes()) == 1024

    def test_a_width_that_differs_between_k_and_v_is_refused_not_guessed(self):
        from shani_chronoa import ctxfit
        shapes = self._shapes() + [("blk.9.attn_k.weight", [2048, 4096])]
        assert ctxfit.kv_width_from_shapes(shapes) == 0

    def test_no_k_or_v_tensor_is_zero_not_a_width(self):
        from shani_chronoa import ctxfit
        assert ctxfit.kv_width_from_shapes([("blk.0.attn_q.weight", [2048, 2048])]) == 0
        assert ctxfit.kv_width_from_shapes([]) == 0
        assert ctxfit.kv_width_from_shapes(None) == 0

    def test_the_real_model_on_this_box_agrees(self):
        """The live check: the two routes meet a real file.

        Opt-in by naming a model (`CHRONOA_KV_PARITY_MODEL=/path/to.gguf`),
        because **the harness redirects both `HOME` and `XDG_DATA_HOME`**, so no
        route to a real model exists from inside the suite - measured: `~` and
        `files.data_home()` both answer a pytest temp dir. This is the same trap
        that made `local_llm.active()` answer `""` under `test_routines_live`.

        Without the variable it SKIPs and says so, rather than passing vacuously
        on no model at all. `shani-testbed/slot-tests/chronoa-cli-formats.sh`
        runs it against the image's own model.
        """
        gguf = pytest.importorskip("gguf")
        from shani_chronoa import ctxfit
        import os
        path = os.environ.get("CHRONOA_KV_PARITY_MODEL", "")
        if not path:
            pytest.skip(
                "no CHRONOA_KV_PARITY_MODEL set, and the harness redirects HOME "
                "and XDG_DATA_HOME so no real model is reachable from the suite")
        candidate = Path(path)
        if not candidate.is_file():
            pytest.fail(f"CHRONOA_KV_PARITY_MODEL names {candidate}, which is not a file")
        reader = gguf.GGUFReader(str(candidate))
        info = ctxfit.gguf_model_info(candidate)
        assert info, "could not read the named model's header"
        width = ctxfit.kv_width_from_shapes(reader.tensors)
        if width == 0:
            pytest.skip("this model's K/V widths are not uniform (fused attention?)")
        layers = len([t for t in reader.tensors if t.name.endswith("attn_k.weight")])
        from_tensors = layers * 2 * width * 2
        from_metadata = ctxfit.kv_bytes_per_token(info)
        # A hybrid model's metadata count is lower by design (sliding-window and
        # shared-KV layers), so the tensors are an exact match for a plain
        # full-attention model and an upper bound for a hybrid one.
        assert from_tensors >= from_metadata, (
            f"the tensors imply {from_tensors} bytes/token but the metadata route "
            f"answered {from_metadata} - the module is UNDER-counting the cache, "
            "which is the direction that spills a model onto the CPU")


class TestTheMoEActiveBytesAreDerivedNotDeclared:
    """`local_llm.REQUIRES` carries `active_bytes` for the one mixture-of-experts
    tier as a hardcoded constant marked `measured: False`, and
    `requirements_met()` refuses it as a default on that basis. Nothing could
    improve it, because nothing read the checkpoint to find out.

    A GGUF carries its own routing topology - `expert_count`,
    `expert_used_count`, `expert_feed_forward_length` - so the number is
    derivable whenever the model is on disk. The declared constant stays as the
    fallback for a tier that is not installed.

    The cross-check that matters is against a number recorded independently:
    `AGENTS.md` states Qwen3-30B-A3B stores 17.3 GiB and activates ~3.3B of 30.5B
    parameters, so it reads **~2 GiB per token**. The declared constant is
    2.05 GiB. A derivation that came out near either of those is describing the
    same model.
    """

    @staticmethod
    def _moe_info(**over):
        info = {
            "general.architecture": "qwen3moe",
            "qwen3moe.block_count": 48,
            "qwen3moe.embedding_length": 2048,
            "qwen3moe.expert_count": 128,
            "qwen3moe.expert_used_count": 8,
            "qwen3moe.expert_feed_forward_length": 768,
            "qwen3moe.feed_forward_length": 6144,
        }
        for name, value in over.items():
            info[f"qwen3moe.{name}"] = value
        return info

    def test_the_derived_figure_agrees_with_the_recorded_one(self):
        """Two independent numbers, one derived from the architecture and one
        recorded from it, have to land in the same place. This is the control that
        makes the derivation a measurement rather than a different guess."""
        from shani_chronoa import ctxfit
        derived = ctxfit.expert_bytes_per_token(
            self._moe_info(), 18_556_685_824)
        assert derived, "the header describes a MoE, so this must produce a figure"
        # Within 50% of the declared 2.05 GiB and of the recorded ~2 GiB. The
        # bound is deliberately loose: the derivation is arithmetic on rounded
        # architecture fields, not a measurement of a decode.
        assert 2_200_000_000 * 0.5 <= derived <= 2_200_000_000 * 1.5, derived

    def test_the_per_weight_cost_is_paid_against_the_stored_count(self):
        """The bug the first version had, and the reason the cross-check exists.

        The file stores **every** expert, so bytes-per-weight must be the file
        divided by all of them. Dividing by the *active* count undercounts by the
        expert ratio - twelve-fold here - and the derived figure came out 8x too
        large, which no amount of reading the arithmetic would have shown.
        """
        from shani_chronoa import ctxfit
        full = ctxfit.expert_bytes_per_token(self._moe_info(), 18_556_685_824)
        # Same model, but 16 experts routed instead of 8: the active share
        # doubles while the file's per-weight cost must not move.
        doubled = ctxfit.expert_bytes_per_token(
            self._moe_info(expert_used_count=16), 18_556_685_824)
        assert full and doubled
        assert doubled > full, (full, doubled)
        # And under 4x, not 8x: the routing is not the whole cost.
        assert doubled < full * 4, (full, doubled)

    def test_a_dense_model_is_not_a_moe(self):
        """`None` here means *not this case*, and the caller's fallback answers."""
        from shani_chronoa import ctxfit
        info = {"general.architecture": "qwen3", "qwen3.block_count": 36,
                "qwen3.embedding_length": 4096, "qwen3.feed_forward_length": 12288}
        assert ctxfit.expert_bytes_per_token(info, 4_500_000_000) is None

    def test_a_header_missing_any_field_it_needs_returns_none(self):
        from shani_chronoa import ctxfit
        base = self._moe_info()
        for missing in ("expert_count", "expert_used_count",
                        "expert_feed_forward_length", "block_count",
                        "embedding_length"):
            info = dict(base)
            del info[f"qwen3moe.{missing}"]
            assert ctxfit.expert_bytes_per_token(info, 18_556_685_824) is None, missing

    def test_nonsense_routing_is_refused_rather_than_used(self):
        """More experts active than exist is not a reading of anything."""
        from shani_chronoa import ctxfit
        info = self._moe_info(expert_count=4, expert_used_count=8)
        assert ctxfit.expert_bytes_per_token(info, 18_556_685_824) is None

    def test_an_installed_moe_overrides_the_declared_constant(self, monkeypatch, tmp_path):
        """The wiring: when the checkpoint is on disk the file wins, and the table
        is only the fallback for a tier that is not installed."""
        from shani_chronoa import ctxfit, local_llm
        spec = local_llm.SPECS["qwen3-30b-a3b"]
        fake = tmp_path / spec.filename
        fake.write_bytes(b"x")                      # content is not read, only its size
        monkeypatch.setattr(local_llm.os.path, "exists", lambda p: str(p) == str(fake))
        monkeypatch.setattr(ctxfit, "gguf_model_info",
                             lambda p: self._moe_info() if str(p) == str(fake) else None)
        assert local_llm.bytes_per_token("qwen3-30b-a3b"), \
            "an installed MoE must derive its figure rather than fall back"

    def test_an_absent_moe_still_falls_back_to_the_declared_constant(
            self, monkeypatch, tmp_path):
        """The tier is not installed on most machines, and the table entry is what
        they get - so the fallback must survive the new route being added."""
        from shani_chronoa import local_llm
        monkeypatch.setattr(local_llm.os.path, "exists", lambda p: False)
        assert local_llm.bytes_per_token("qwen3-30b-a3b") == 2_200_000_000


class TestTheCacheFloor:
    """`KV_CACHE_FLOOR` is the most compression the KV cache may take, and it is a
    measurement rather than a preference.

    Measured 2026-10-12 with this repo's own `llama-perplexity` path and its fixed
    corpus, on SmolLM2-135M-Q4_K_M, `-ctk` and `-ctv` both set, two identical runs
    each (the numbers repeat exactly):

        f16  59.1719     q8_0  59.1499   (-0.022)
        q5_0 59.2380     q4_0  62.7689   (+3.597, +6.1%)

    So `q8_0` and `q5_0` are free - inside the run-to-run noise - and `q4_0` costs
    6.1%. PersonaPlex's RVQ has exactly this control on its compression dial:
    `set_num_codebooks` asserts `n >= self.n_q_semantic`, so acoustic fidelity can
    be traded away and meaning never can.
    """

    def test_the_floor_is_the_cheapest_type_that_measured_free(self):
        from shani_chronoa import ctxfit
        assert ctxfit.KV_CACHE_FLOOR == "q8_0", (
            "q5_0 measured +8.53% on Qwen3-0.6B, worse than q4_0 costs on either "
            "other model; q8_0 is the only step cheap on all three")

    def test_the_ladder_stops_at_the_floor(self):
        from shani_chronoa import ctxfit
        ladder = ctxfit.CACHE_TYPE_LADDER
        assert ladder[-1] == ctxfit.KV_CACHE_FLOOR, ladder
        assert "q4_0" not in ladder, ladder

    def test_a_longer_ladder_cannot_buy_past_the_floor(self):
        """The bound is on the *dial*, not on the default ladder, so a caller
        handing in an extended ladder gets clamped rather than obeyed."""
        from shani_chronoa import ctxfit
        weights = int(5.4 * 2 ** 30)
        info = {"general.architecture": "qwen3", "qwen3.block_count": 36,
                "qwen3.attention.head_count": 32,
                "qwen3.attention.head_count_kv": 8,
                "qwen3.embedding_length": 4096}
        per_token = ctxfit.kv_bytes_per_token(info)
        long = ("f16", "q8_0", "q5_0", "q4_0", "q4_1", "q4_1")
        for context in (2048, 4096, 8192, 32768):
            chosen = ctxfit.cache_type_for_context(
                weights, context, total_bytes=int(6 * 2 ** 30),
                per_token_at_f16=per_token, ladder=long)
            assert chosen in ("f16", "q8_0", "q5_0"), (context, chosen)
            assert chosen != "q4_0" and chosen != "q4_1", (context, chosen)

    def test_the_floor_is_still_chosen_when_nothing_else_fits(self):
        """Nothing fits is not a reason to leave the dial at f16, which would
        claim a quality level the numbers do not support."""
        from shani_chronoa import ctxfit
        info = {"general.architecture": "qwen3", "qwen3.block_count": 36,
                "qwen3.attention.head_count": 32,
                "qwen3.attention.head_count_kv": 8,
                "qwen3.embedding_length": 4096}
        per_token = ctxfit.kv_bytes_per_token(info)
        chosen = ctxfit.cache_type_for_context(
            int(6 * 2 ** 30), 2048, total_bytes=int(6 * 2 ** 30),
            per_token_at_f16=per_token)
        assert chosen == ctxfit.KV_CACHE_FLOOR, chosen


class TestTheFloorReproducesOnASecondModel:
    """The `q5_0` floor was first measured on one small model, and the note said
    so: *"a floor taken further than it deserves is worse than no floor."*

    It has now been measured on a second, 50x larger one, with the same corpus
    and method. Perplexity as a percentage of each model's own f16 figure:

        type   SmolLM2-135M    Qwen3-8B-Q4_K_M
        q8_0      -0.04%          +0.32%
        q5_0      +0.11%          +1.35%
        q4_0      +6.08%          +5.81%

    **The ordering is the same on both, and `q4_0` costs ~6% on both.** That
    reproduction is what makes the floor a measurement rather than one box's
    accident.

    These tests pin the *shape* of the two measurements rather than re-running
    them - `llama-perplexity` takes minutes per pass on the 8B, and a test that
    forks a 4.7 GB model is not a unit test. The numbers live in
    `KV_CACHE_MEASUREMENTS`; this class asserts the two properties they jointly
    establish: the ordering, and that `q4_0` is the cliff on both.
    """

    def test_the_ordering_holds_on_every_model(self):
        from shani_chronoa import ctxfit
        per_model = ctxfit.KV_CACHE_MEASUREMENTS
        # Three, not two, and the number is the point rather than a round one.
        # Two models put the floor at `q5_0` - on the reading that `q5_0` was a
        # known cost below the cliff - and the third (Qwen3-0.6B, +8.53%)
        # overturned that. Removing it does not error anywhere; the floor simply
        # goes back to being justified by the two that were luck, so this is the
        # assertion that catches a deletion.
        # Four, and the fourth is the one that settled the *shape* of the cost.
        # With three models q5_0 read as "8.5% on the small one, 1.35% on the big
        # one" - a size trend. Qwen3-1.7B-Q4_K_M broke that reading: it is the
        # same weight quantization as the 8B and costs **+8.54%** against the 8B's
        # **+1.35%**, a 6x difference with the quantization held constant. So the
        # cost is bimodal and per-model, not a trend - and a model nobody measured
        # is a model whose side of the split is unknown. The 1.7B is also
        # `current.gguf`, the default tier, so this is the model the floor protects
        # most directly. Dropping its record leaves three models and a test that
        # still passes, which is why the count is four.
        assert len(per_model) >= 4, (
            f"the floor at q8_0 rests on four models, including Qwen3-1.7B "
            f"(q5_0 +8.54%) and Qwen3-0.6B (+8.53%), which together show the cost "
            f"is bimodal rather than a size trend; {len(per_model)} models measured")
        for model, costs in per_model.items():
            # Non-decreasing from `q8_0` on - not monotonic including `f16`.
            # `q8_0` measured -0.04% on the small model, i.e. *better* than
            # f16, which is the noise a compression cannot buy; asserting
            # monotonicity across that step would fail on correct data.
            steps = [costs[t] for t in ("q8_0", "q5_0", "q4_0")]
            assert steps == sorted(steps), (
                f"{model}: the costs are not monotonic in compression "
                f"{costs} - the ladder assumes worse compression costs more")

    def test_q4_0_is_the_cliff_on_both_models(self):
        """The one property the floor actually rests on. If a future measurement
        shows `q4_0` cheap on some model, the floor's basis is gone and this test
        should be what makes somebody revisit it."""
        from shani_chronoa import ctxfit
        for model, costs in ctxfit.KV_CACHE_MEASUREMENTS.items():
            cliff = costs["q4_0"]
            assert cliff >= 1.0, (
                f"{model}: q4_0 measured {cliff:.2f}% worse, which is not a cliff "
                f"anymore - the floor at q5_0 is no longer justified by it")
            cheaper = costs["q5_0"]
            assert cheaper < cliff / 2, (
                f"{model}: q5_0 at {cheaper:.2f}% is not clearly cheaper than "
                f"q4_0 at {cliff:.2f}%, so the floor is not separating anything")

    def test_the_floor_is_the_only_step_cheap_on_every_model(self):
        """The property the third model established, and the one that replaced an
        earlier and weaker claim.

        The floor was first set at `q5_0` from two models, on the reading that
        `q5_0` was "a known cost below the cliff". Qwen3-0.6B measured `q5_0` at
        **+8.53%** - worse than `q4_0` costs on either of the other two - so that
        reading was luck. `q8_0` is the only step cheap on all three, and a step
        below the floor has to be cheap on *every* model, not on the average.

        This is PersonaPlex's `n_q_semantic` taken properly: the semantic set is
        what the worst case allows, not what the mean suggests.
        """
        from shani_chronoa import ctxfit
        worst_q8 = max(c["q8_0"] for c in ctxfit.KV_CACHE_MEASUREMENTS.values())
        assert worst_q8 <= 1.0, (
            f"q8_0 costs {worst_q8:.2f}% on some model, so even the floor step "
            f"is not cheap everywhere; the ladder has no safe rung")
        # Every step the floor excludes must be demonstrably worse *somewhere*,
        # or excluding it buys nothing.
        for excluded in ("q5_0", "q4_0"):
            if excluded in ctxfit.CACHE_TYPE_LADDER:
                continue
            costs = [c[excluded] for c in ctxfit.KV_CACHE_MEASUREMENTS.values()
                     if excluded in c]
            assert costs and max(costs) > worst_q8 * 3, (
                f"{excluded} is excluded from the ladder but never costs more "
                f"than q8_0 does, so the exclusion is not earning its keep")


class TestTheFourthModelSettlesTheShapeOfTheCost:
    """The finding the fourth measurement produced, which is stronger than the
    one the third produced.

    With three models, `q5_0` read as a **size trend**: 0.11% at 135M, 1.35% at
    8B - and then 8.53% at 0.6B, which already broke monotonicity. Three points
    cannot say *what* the cost follows.

    Qwen3-1.7B-Q4_K_M settles it by holding the weight quantization constant:
    it and the 8B are both `Q4_K_M`, and their `q5_0` costs are **6.3x apart**.
    So the cost is not a function of size (the 1.7B is smaller and far more
    expensive) and not of weight quantization (identical here, 6x apart). It is
    **per-model and bimodal** - two models near zero, two near 8.5% - which is
    what makes the floor a per-model question rather than a formula.
    """

    def test_the_same_weight_quantization_costs_six_times_apart(self):
        from shani_chronoa import ctxfit
        a = ctxfit.KV_CACHE_MEASUREMENTS["qwen3-1.7b-q4_k_m"]["q5_0"]
        b = ctxfit.KV_CACHE_MEASUREMENTS["qwen3-8b-q4_k_m"]["q5_0"]
        assert a > b * 3, (
            f"the 1.7B and the 8B are both Q4_K_M, so weight quantization alone "
            f"would predict a similar cost; they measure {a}% and {b}%, a "
            f"{a / b:.1f}x spread, so the cost is per-model rather than derived "
            f"from the file's quantization")
        # And the ordering a size trend would predict is inverted: the smaller
        # model is the expensive one.
        assert a > b, (
            "the 1.7B is smaller than the 8B and costs more at q5_0, which is "
            "the direction a size trend would not predict")

    def test_the_floor_still_holds_with_the_fourth_model(self):
        """The floor is unchanged, and the fourth model is not an exemption."""
        from shani_chronoa import ctxfit
        worst = max(c[ctxfit.KV_CACHE_FLOOR]
                    for c in ctxfit.KV_CACHE_MEASUREMENTS.values())
        assert worst <= 1.0, (
            f"the floor step {ctxfit.KV_CACHE_FLOOR} costs {worst:.2f}% on some "
            f"model, so it is not cheap everywhere the ladder is applied")
        assert ctxfit.KV_CACHE_FLOOR == "q8_0"

    def test_the_default_tier_is_one_of_the_expensive_ones(self):
        """Why this matters beyond the number: the 1.7B is the loaded model.

        `current.gguf` is a symlink to Qwen3-1.7B, so `q5_0` at +8.54% is the
        cost on the tier a default install runs - the case where an untested
        floor would have been applied without anyone choosing it.
        """
        import os
        from shani_chronoa import ctxfit
        from shani_chronoa import local_llm
        # conftest redirects XDG_DATA_HOME to a scratch home, so the models
        # installed once into this repo's git-ignored cache are invisible unless
        # they are linked in - the same trap test_tts_loudness.py records, where
        # the engines "skipped on a box that has them".
        data_home = Path(os.environ["XDG_DATA_HOME"])
        real = Path(__file__).resolve().parent.parent / "cache" / "data-home" / "shani-chronoa" / "llm"
        link = data_home / "shani-chronoa" / "llm"
        if real.is_dir() and not link.exists():
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(real, link)
        current = local_llm.model_dir() / "current.gguf"
        if not current.exists():
            import pytest
            pytest.skip("no model installed on this machine")
        name = current.resolve().name.lower()
        assert any(k.split("-q")[0] in name or name[:8] in k
                   for k in ctxfit.KV_CACHE_MEASUREMENTS), (
            f"the loaded model {name} is not among the measured ones, so the "
            f"floor's worst case does not describe the model in use")


class TestTheFloorsCost:
    """`KV_CACHE_FLOOR` is now `q8_0`, which gives up the `q5_0`/`q4_0` steps. That
    trade was justified on quality - `q5_0` measured **+8.53%** on Qwen3-0.6B -
    but the other side of it, what VRAM headroom the floor costs, was unquantified.

    Measured on Qwen3-8B's shape (8 KV heads, head_dim 128, 4.68 GiB weights):

        vram     f16    q8_0    q5_0    q4_0
        10G     32768   32768   32768   32768
         9G     16384   32768   32768   32768
         8G     16384   32768   32768   32768
         7G      8192   16384   32768   32768
         6G      4096    8192   16384   16384
         5G      2048    2048    2048    2048

    **`q8_0` recovers the whole 2x doubling at every band where `f16` fails.**
    `q5_0` only ever buys the *third* increment, in a narrow band, on the models
    where that increment costs 8.5% perplexity. So the floor costs the last step
    of headroom and keeps the first one - which is the step from "cramped" to
    "usable", and the one that matters.
    """

    @staticmethod
    def _info():
        return {"general.architecture": "qwen3", "qwen3.block_count": 36,
                "qwen3.attention.head_count": 32,
                "qwen3.attention.head_count_kv": 8,
                "qwen3.embedding_length": 4096,
                "qwen3.attention.key_length": 128,
                "qwen3.attention.value_length": 128}

    def test_the_floor_step_actually_compresses(self):
        """The assertion that was missing, and whose absence made its neighbour
        vacuous.

        `test_the_floor_step_keeps_the_doubling_where_f16_fails` below skips any
        row where `at_floor == f16`, on the reading that f16 then fits and there
        is nothing to recover. But `at_floor == f16` is *also* what a floor step
        that has stopped compressing looks like - inflate `q8_0` to 1.9 bytes per
        element against f16's 2.0 and it equals f16 at every band, the guard skips
        all four rows, and the neighbouring test passes without asserting
        anything. The mutation run for `tools/mutations/floor_cost.py` found
        exactly that.

        So the compression is asserted on its own first, and then a step that
        delivers no headroom means the step is broken rather than that f16 fits.
        """
        from shani_chronoa import ctxfit
        bpe = ctxfit.KV_CACHE_BYTES_PER_ELEM
        ratio = bpe["f16"] / bpe[ctxfit.KV_CACHE_FLOOR]
        assert ratio >= 1.8, (
            f"the floor step {ctxfit.KV_CACHE_FLOOR} compresses only "
            f"{ratio:.2f}x ({bpe[ctxfit.KV_CACHE_FLOOR]} vs {bpe['f16']} bytes "
            f"per element); it is meant to be the ~2x step and a step that does "
            f"not compress makes the headroom assertions below vacuous")

    def test_the_floor_step_keeps_the_doubling_where_f16_fails(self):
        """The property the floor's cost rests on. If this ever stops holding, the
        floor is buying its quality guarantee at the price of the headroom that
        matters, and that is a different decision from the one made here.

        Now that `test_the_floor_step_actually_compresses` pins the compression,
        `at_floor == f16` below can only mean f16 fits - the other reading is
        already excluded by that test.
        """
        from shani_chronoa import ctxfit
        weights = int(4.68 * 2 ** 30)
        info = self._info()
        for vram_gb in (9.0, 8.0, 7.0, 6.0):
            total = int(vram_gb * 2 ** 30)
            f16 = ctxfit.context_that_fits(
                weights, total_bytes=total,
                per_token=ctxfit.kv_bytes_per_token(info, "f16"))
            at_floor = ctxfit.context_that_fits(
                weights, total_bytes=total,
                per_token=ctxfit.kv_bytes_per_token(info, ctxfit.KV_CACHE_FLOOR))
            if f16 == at_floor:
                continue                      # f16 fits; nothing to recover
            assert at_floor > f16, (
                f"at {vram_gb} GiB the floor step ({ctxfit.KV_CACHE_FLOOR}) buys "
                f"nothing over f16: {f16} -> {at_floor}")
            # And it buys at least a doubling, not a sliver.
            assert at_floor >= f16 * 2, (
                f"at {vram_gb} GiB the floor step only bought {f16} -> "
                f"{at_floor}, which is not the doubling it is there for")

    def test_the_excluded_steps_only_buy_where_the_floor_already_doubled(self):
        """So the exclusion is the last increment rather than the first one."""
        from shani_chronoa import ctxfit
        weights = int(4.68 * 2 ** 30)
        info = self._info()
        for vram_gb in (9.0, 8.0, 7.0, 6.0):
            total = int(vram_gb * 2 ** 30)
            def ctx_at(t):
                return ctxfit.context_that_fits(
                    weights, total_bytes=total,
                    per_token=ctxfit.kv_bytes_per_token(info, t))
            at_floor, cheaper = ctx_at(ctxfit.KV_CACHE_FLOOR), ctx_at("q5_0")
            if cheaper <= at_floor:
                continue
            # Where q5_0 buys more, the floor must already have delivered the
            # first doubling - never be the *only* thing standing between cramped
            # and unusable.
            f16 = ctx_at("f16")
            assert at_floor > f16, (
                f"at {vram_gb} GiB q5_0 is the only step that helps "
                f"(f16 {f16}, floor {at_floor}), so excluding it costs the "
                f"headroom that matters")
