"""What each model tier needs, and why the answer is not "is there a GPU".

`local_llm.recommended_for_machine()` used to answer from RAM alone, which made
a CPU-only box with 30 GB of RAM choose a 14B that decodes at ~2 tok/s on it.
The second attempt gated on `gpu_devices()`, which is wrong in the other
direction: a Threadripper's 8 channels of DDR5 would still be told to run a
1.7B. Both mistakes are the same one - **decode cost follows the bytes read per
token, which is neither RAM nor "has a GPU"**, and for a mixture-of-experts the
bytes read are a fraction of the file.

These tests pin the measurement, the table, and the two refusals that keep an
estimate from becoming a default.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import local_llm  # noqa: E402


class _Measured:
    """A machine with a known bandwidth, so the numbers are not this box's."""

    def __init__(self, monkeypatch, gib_per_s: float, ram=30.5, cores=8, gpu=False):
        self._bw = gib_per_s * 1024 ** 3
        monkeypatch.setattr(local_llm, "effective_bandwidth", lambda: self._bw)
        monkeypatch.setattr(local_llm, "ram_gb", lambda: ram)
        # A shim, not a patch of the shared `os`: `cpu_count` is read inside
        # `requirements_met`, and patching the module object would change it for
        # everything else in the process for the duration of the test.
        monkeypatch.setattr(local_llm, "os", type("OS", (), {"cpu_count": staticmethod(lambda: cores)}))
        monkeypatch.setattr(local_llm, "gpu_devices", lambda: ["card"] if gpu else [])


class TestTheTableCoversEveryTier:
    def test_every_tier_has_a_row(self):
        missing = [spec.key for spec, _l, _r in local_llm.TIERS
                   if spec.key not in local_llm.REQUIRES]
        assert not missing, f"tiers with no requirements row: {missing}"

    def test_a_tier_with_no_row_is_not_silently_defaultable(self):
        """The first version iterated only rows that existed, so a new tier was
        skipped rather than refused - it never became a default and nothing said
        why."""
        assert set(local_llm.REQUIRES) >= {s.key for s, _l, _r in local_llm.TIERS}

    def test_the_rows_carry_the_units_the_machine_reports(self):
        for key, row in local_llm.REQUIRES.items():
            assert {"cpu", "ram", "min_tps"} <= set(row), (
                f"{key} is missing one of cpu/ram/min_tps")


class TestBytesPerToken:
    """The number everything else follows."""

    def test_a_dense_model_reads_its_whole_file(self):
        assert local_llm.bytes_per_token("qwen3-1.7b") == \
            local_llm.SPECS["qwen3-1.7b"].size_bytes

    def test_a_moe_model_reads_a_fraction_of_what_it_stores(self):
        """The point of the whole table: a 30B that reads like a 3B."""
        spec = local_llm.SPECS["qwen3-30b-a3b"]
        per_token = local_llm.bytes_per_token("qwen3-30b-a3b")
        assert per_token < spec.size_bytes / 4, (
            f"the 30B reads {per_token} of {spec.size_bytes} bytes per token - "
            "that is the dense model's cost, and the MoE sparsity has been lost")

    def test_an_unknown_key_is_none_not_zero(self):
        assert local_llm.bytes_per_token("no-such-model") is None


class TestSpeedIsBandwidthOverBytesPerToken:
    def test_it_divides_by_the_active_set_not_the_file(self, monkeypatch):
        monkeypatch.setattr(local_llm, "effective_bandwidth",
                            lambda: local_llm.SPECS["qwen3-30b-a3b"].size_bytes * 10)
        # 10x the file size of bandwidth, over the ACTIVE bytes.
        tps = local_llm.expected_decode_tps("qwen3-30b-a3b")
        assert abs(tps - 10 * local_llm.SPECS["qwen3-30b-a3b"].size_bytes
                   / local_llm.bytes_per_token("qwen3-30b-a3b")) < 1

    def test_unmeasured_bandwidth_is_none_not_a_guess(self, monkeypatch):
        monkeypatch.setattr(local_llm, "effective_bandwidth", lambda: None)
        assert local_llm.expected_decode_tps("qwen3-1.7b") is None


class TestTheSingleTokenArtefact:
    """`n_predict: 1` makes llama.cpp report a million tok/s."""

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def _server(self, monkeypatch, timings):
        """A fake llama-server returning `timings` for a completion."""
        monkeypatch.setattr(local_llm, "httpx", type("H", (), {
            "get": staticmethod(lambda url, **kw: self._Resp({"model_path": "/x"})),
            "post": staticmethod(
                lambda url, **kw: self._Resp({"timings": timings}))}))
        monkeypatch.setattr(local_llm.os.path, "getsize", lambda p: 1_100_000_000)
        # Both halves: the cache is now keyed on the loaded model, and
        # `_BANDWIDTH_MODEL` outlives the process otherwise.
        monkeypatch.setattr(local_llm, "_BANDWIDTH_CACHE", None)
        monkeypatch.setattr(local_llm, "_BANDWIDTH_MODEL", None)

    def test_a_rate_above_a_thousand_is_not_a_decode(self, monkeypatch):
        self._server(monkeypatch, {"predicted_n": 1,
                                   "predicted_per_second": 1000000.0})
        assert local_llm.effective_bandwidth() is None, (
            "a single-token probe reported a million tok/s as this machine's bandwidth")

    def test_a_real_decode_is_used(self, monkeypatch):
        self._server(monkeypatch, {"predicted_n": 16,
                                   "predicted_per_second": 15.0})
        assert local_llm.effective_bandwidth() == 1_100_000_000 * 15.0


class TestTheBandwidthKeepsItsLargestMeasurement:
    """**A small model under-reports this machine, and that is the finding.**

    Measured twice on one box within minutes, nothing else running:

        Qwen3-1.7B-Q4_K_M  6.81 tok/s x 1.03 GiB =  7.03 GiB/s
        Qwen3-8B-Q4_K_M    3.69 tok/s x 4.68 GiB = 17.29 GiB/s

    Both are "the same machine" and they disagree by 2.5x. A small model is not
    bandwidth-*saturated* - it is bound by per-token compute and latency - so it
    moves less memory per second than the hardware can. Caching whichever
    measurement arrived first therefore locked in a figure 2.5x too low, and
    every prediction for a *larger* model was built on it.

    The maximum is the right rule and not a fudge: a non-saturated model can
    only under-report, never over-report.
    """

    def _server(self, monkeypatch, state):
        """A fake llama-server whose answers the test changes between calls."""
        class R:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        monkeypatch.setattr(local_llm, "httpx", type("H", (), {
            "get": staticmethod(lambda url, **kw: R(
                {"model_path": state["path"]})),
            "post": staticmethod(lambda url, **kw: R({"timings": state["timings"]}))}))
        monkeypatch.setattr(local_llm.os.path, "getsize",
                            lambda p: state["weights"])
        monkeypatch.setattr(local_llm.os.path, "realpath", lambda p: p)
        monkeypatch.setattr(local_llm, "_BANDWIDTH_CACHE", None)
        monkeypatch.setattr(local_llm, "_BANDWIDTH_MODEL", None)
        state["probes"] = 0

    def test_a_bigger_model_raises_the_figure(self, monkeypatch):
        state = {"path": "/models/small.gguf", "weights": 1_100_000_000,
                 "timings": {"predicted_n": 16, "predicted_per_second": 6.81}}
        self._server(monkeypatch, state)
        small = local_llm.effective_bandwidth()
        assert small == 1_100_000_000 * 6.81

        # Now the big model is loaded and measured on the same box.
        state.update(path="/models/big.gguf", weights=5_027_783_488,
                     timings={"predicted_n": 16, "predicted_per_second": 3.69})
        big = local_llm.effective_bandwidth()
        assert big == 5_027_783_488 * 3.69
        assert big > small, (
            "the 8B moved 17 GiB/s and the 1.7B moved 7 on the same machine; "
            "keeping the smaller figure makes every prediction for a larger "
            "model optimistic by the same factor")

    def test_going_back_to_a_small_model_does_not_lower_it(self, monkeypatch):
        """**The control.** A `max` that any later call could lower is not one."""
        state = {"path": "/models/big.gguf", "weights": 5_027_783_488,
                 "timings": {"predicted_n": 16, "predicted_per_second": 3.69}}
        self._server(monkeypatch, state)
        high = local_llm.effective_bandwidth()

        state.update(path="/models/small.gguf", weights=1_100_000_000,
                     timings={"predicted_n": 16, "predicted_per_second": 6.81})
        again = local_llm.effective_bandwidth()
        assert again == high, (
            "a later, lower reading replaced the machine's real bandwidth - "
            "which is the original defect with the sign flipped")

    def test_the_same_model_is_not_probed_twice(self, monkeypatch):
        state = {"path": "/models/small.gguf", "weights": 1_100_000_000,
                 "timings": {"predicted_n": 16, "predicted_per_second": 15.0}}
        self._server(monkeypatch, state)
        calls = []
        real_get = local_llm.httpx.get
        monkeypatch.setattr(local_llm.httpx, "get",
                            staticmethod(lambda u, **k: (calls.append(u), real_get(u, **k))[1]))
        local_llm.effective_bandwidth()
        local_llm.effective_bandwidth()
        assert len(calls) <= 4, (
            f"{len(calls)} probes for two calls on an unchanged model - the "
            f"panel asks once per card and would decode per card")

    def test_changing_the_loaded_model_does_reprobe(self, monkeypatch):
        """The opposite of the test above, so the cache cannot pass by never
        re-measuring.

        **Asserts that a probe happened, not that the figure changed** - and the
        first version of this asserted the figure, which is why it failed. It
        loaded a "bigger" model that measured *lower* (5.03 GiB x 3.0 against
        1.10 GiB x 15.0), the max rule correctly kept the higher value, and the
        test called that a defect. Under the max rule a new model may legitimately
        leave the figure alone; the thing that must not happen is never asking.
        """
        state = {"path": "/models/small.gguf", "weights": 1_100_000_000,
                 "timings": {"predicted_n": 16, "predicted_per_second": 15.0}}
        self._server(monkeypatch, state)
        decodes = []
        real_post = local_llm.httpx.post
        monkeypatch.setattr(local_llm.httpx, "post", staticmethod(
            lambda u, **k: (decodes.append(u), real_post(u, **k))[1]))
        local_llm.effective_bandwidth()
        assert len(decodes) == 1, "the first call must decode exactly once"

        local_llm.effective_bandwidth()
        local_llm.effective_bandwidth()
        assert len(decodes) == 1, (
            f"{len(decodes)} decodes for three calls on an unchanged model - the "
            f"panel asks once per card and each decode costs ~4 s")

        state.update(path="/models/big.gguf", weights=5_027_783_488,
                     timings={"predicted_n": 16, "predicted_per_second": 12.0})
        local_llm.effective_bandwidth()
        assert len(decodes) == 2, (
            "a different model was loaded and nothing was re-measured - the "
            "figure belongs to the model it was measured from")


class TestTheRecommendationIsNotAGpuGate:
    """Why both earlier designs were wrong, pinned so neither comes back."""

    def test_a_laptop_cpu_is_not_handed_a_tier_it_cannot_use(self, monkeypatch):
        """The RAM-only mistake, pinned at its new boundary.

        This asserted `recommended_for_machine() == "qwen3-4b"` until the
        2026-10-11 bandwidth correction, when the 4B's predicted rate rose to
        7.4 tok/s and cleared `min_tps` on its own. The assertion was *right
        about the outcome and wrong about the reason*, and the reason mattered:
        the 4B has never been through `tools/task_eval.py`, so promoting the CPU
        default on a decode figure would be a metric standing in for the
        evidence. The rule is `_may_default` now, and this asserts the outcome
        stays where it was while the test says which rule holds it there.
        """
        _Measured(monkeypatch, 16.5, ram=30.5, cores=8, gpu=False)
        assert local_llm.recommended_for_machine() == "qwen3-1.7b", (
            "a CPU-only machine with 30 GB of RAM was recommended a tier it "
            "cannot decode fast enough - the RAM-only mistake")
        met, _missing = local_llm.requirements_met("qwen3-4b")
        assert met, "the 4B is not even offerable on this machine, so the "
        "ceiling is doing something the speed gate already does"

    def test_a_threadripper_gets_a_big_tier_on_cpu_alone(self, monkeypatch):
        """The whole point of measuring rather than asking `gpu_devices()`.

        8 channels of DDR5 is on the order of 86 GiB/s effective, where this
        laptop measures 16.5. A gate on the GPU would tell this machine to run a
        1.7B, which is under-serving exactly the user with the hardware for it.
        """
        _Measured(monkeypatch, 86.0, ram=30.5, cores=24, gpu=False)
        assert local_llm.recommended_for_machine() in (
            "qwen3-14b", "qwen3-30b-a3b"), (
            "a high-bandwidth CPU was not offered the tier it can run")

    def test_ram_still_binds(self, monkeypatch):
        _Measured(monkeypatch, 200.0, ram=6.0, cores=24, gpu=False)
        assert local_llm.recommended_for_machine() == "qwen3-1.7b", (
            "a machine that cannot hold the weights was recommended one anyway")

    def test_no_server_means_no_assumption_speed(self, monkeypatch):
        monkeypatch.setattr(local_llm, "effective_bandwidth", lambda: None)
        monkeypatch.setattr(local_llm, "ram_gb", lambda: 64.0)
        assert local_llm.recommended_for_machine() == local_llm.recommended(), (
            "an unmeasured machine was assumed fast")


class TestAnEstimateIsNeverADefault:
    """The refusal that keeps a confident number out of a default."""

    def test_the_unmeasured_tier_is_offered_but_not_defaulted(self, monkeypatch):
        _Measured(monkeypatch, 200.0, ram=64.0, cores=24, gpu=True)
        assert local_llm.REQUIRES["qwen3-30b-a3b"]["measured"] is False
        met, missing = local_llm.requirements_met("qwen3-30b-a3b")
        assert met is False
        assert any("estimated" in m for m in missing), (
            f"the refusal must say the figure is an estimate: {missing}")

    def test_it_is_still_offered_and_still_shows_an_estimated_speed(self, monkeypatch):
        _Measured(monkeypatch, 16.5, ram=30.5, cores=8, gpu=False)
        assert local_llm.expected_decode_tps("qwen3-30b-a3b") > 0, (
            "offered does not mean unscoreable - the estimate is still a number")
        assert "qwen3-30b-a3b" in local_llm.SPECS

    def test_flipping_the_measured_flag_makes_it_a_default(self, monkeypatch):
        """The control: the refusal is about the flag, not about the model.

        **The bandwidth here is load-bearing.** At 16.5 GiB/s the 30B decodes
        around 8 tok/s, which is 1.6x the floor - inside the `_may_default`
        margin as well as outside the `measured` gate, so the test would fail
        for a reason that has nothing to do with the flag. At a Threadripper's
        86 GiB/s it is well past the margin and the flag is the only thing that
        can still refuse it, which is the claim this is a control for.
        """
        _Measured(monkeypatch, 86.0, ram=30.5, cores=24, gpu=False)
        monkeypatch.setitem(local_llm.REQUIRES, "qwen3-30b-a3b",
                            dict(local_llm.REQUIRES["qwen3-30b-a3b"], measured=True))
        assert local_llm.recommended_for_machine() == "qwen3-30b-a3b"

    def test_the_speed_margin_is_the_second_way_in(self, monkeypatch):
        """**`_may_default`, the rule the 4B exposed.**

        A tier may be the default on a CPU-only machine if it has a measured
        eval, or if this machine decodes it at twice `min_tps`. Two tests, one
        per door.

        The margin is not a fudge: 1.48x (the 4B here at 7.4 tok/s) is a reply
        that arrives, and 2.4-3.0x (a 14B on a 7980X) is a reply that arrives
        comfortably. Drawing the line anywhere in between is arbitrary, so it is
        drawn at the point where the turn budget stops being the constraint.
        """
        _Measured(monkeypatch, 16.5, ram=30.5, cores=8, gpu=False)
        assert local_llm.expected_decode_tps("qwen3-4b") > local_llm.MIN_USABLE_TPS, (
            "the 4B clears the floor on this machine, so something other than "
            "speed is what stops it being the default")
        assert not local_llm._may_default("qwen3-4b"), (
            "the 4B has no eval and is not twice the floor; it must not be the "
            "CPU default - which is exactly what the 2026-10-11 change made "
            "possible and what tests/test_setup_wizard.py asserts against")

    def test_a_tier_with_an_eval_score_may_default_at_any_speed(self, monkeypatch):
        """The other door, and the reason a slow machine is not stuck.

        The 0.6B is 44/62 on the eval, so it stays defaultable however slow this
        machine is. Without this, a machine whose only measured tier is the 0.6B
        would have nothing at all to default to.
        """
        _Measured(monkeypatch, 1.0, ram=6.0, cores=2, gpu=False)
        assert local_llm._may_default("qwen3-0.6b")
        assert local_llm._may_default("qwen3-1.7b")

    def test_a_gpu_machine_is_not_held_to_the_ceiling(self, monkeypatch):
        """The weights are not coming off DRAM, so the speed argument is made for
        you. `tests/test_setup_wizard.py` asserts the 4B; this asserts the
        ceiling is *lifted*, not merely widened."""
        _Measured(monkeypatch, 1.0, ram=6.0, cores=2, gpu=True)
        assert local_llm._may_default("qwen3-4b")
        assert local_llm._may_default("qwen3-30b-a3b")


class TestTheMissingListNamesWhatIsShort:
    """A refusal nobody can act on is a refusal nobody fixes."""

    def test_names_the_ram_and_what_this_machine_has(self, monkeypatch):
        _Measured(monkeypatch, 200.0, ram=6.0, cores=24, gpu=False)
        met, missing = local_llm.requirements_met("qwen3-8b")
        assert met is False
        assert any("16 GB of RAM" in m and "6.0" in m for m in missing), missing

    def test_names_the_rate_and_what_this_machine_decodes_at(self, monkeypatch):
        _Measured(monkeypatch, 16.5, ram=30.5, cores=8, gpu=False)
        met, missing = local_llm.requirements_met("qwen3-14b")
        assert met is False
        assert any("tok/s" in m for m in missing), missing


class TestResidencyIsNotRunnability:
    """A machine short on RAM was told *"20 GB of RAM (this machine has 6.0)"*,
    which reads as "cannot run". airllm's whole thesis is that this is the wrong
    reading: `air_llm/airllm/airllm_base.py` says *"AirLLM only ever keeps one
    layer on the GPU at a time, so the VRAM you need depends on the model's layer
    size - not its total size"*, which is how a 70B runs in 4 GB.

    Chronoa's own `bytes_per_token()` already computes the comparable number -
    what one generated token actually reads, the whole file for a dense model and
    a fraction of it for a mixture-of-experts. The tier where those two diverge
    most is `qwen3-30b-a3b`: `ram=20` and `active_bytes` of 2.05 GiB, so a 6 GB
    box is short of the *resident* figure while its per-token cost fits with room
    to spare. The card shows both numbers and still led with the residency one as
    a requirement.
    """

    def test_a_short_of_residence_but_not_of_per_token_says_which_limit_it_is(
            self, monkeypatch):
        _Measured(monkeypatch, 16.5, ram=6.0, cores=24, gpu=False)
        met, missing = local_llm.requirements_met("qwen3-30b-a3b")
        assert met is False
        reason = missing[0]
        assert "resident" in reason, reason
        assert "2.05 GiB" in reason, reason
        # The distinction itself, which is the whole point of the sentence.
        assert "not a cap on whether it runs" in reason, reason

    def test_a_dense_tier_names_the_regime_too(self, monkeypatch):
        """The clause is not MoE-only. A dense model's per-token cost is the whole
        file, so the same arithmetic applies - which is what keeps the sentence
        uniform rather than a special case for one tier."""
        _Measured(monkeypatch, 16.5, ram=6.0, cores=24, gpu=False)
        _met, missing = local_llm.requirements_met("qwen3-8b")
        assert "resident" in missing[0], missing[0]
        assert "4.68 GiB" in missing[0], missing[0]

    def test_a_tier_whose_per_token_cost_does_not_fit_keeps_the_plain_reason(
            self, monkeypatch):
        """The other direction, so the new clause is not simply always on. When one
        generated token reads more than the machine has, "not the default" is not
        enough of a warning and the residency sentence would be a softer claim
        than the truth."""
        _Measured(monkeypatch, 16.5, ram=6.0, cores=24, gpu=False)
        _met, missing = local_llm.requirements_met("qwen3-14b")
        assert "resident" not in missing[0], missing[0]
        assert "24 GB of RAM" in missing[0] and "6.0" in missing[0], missing[0]

    def test_the_reason_never_claims_a_decode_rate(self, monkeypatch):
        """Nobody here has measured a model larger than RAM on a small box. An
        invented tok/s is the confident wrong answer this file keeps recording, so
        the sentence is deliberately silent on speed and says what it *does*
        know: the per-token figure, which is arithmetic and not a measurement."""
        _Measured(monkeypatch, 16.5, ram=6.0, cores=24, gpu=False)
        _met, missing = local_llm.requirements_met("qwen3-30b-a3b")
        assert "tok/s" not in missing[0], missing[0]

    def test_a_machine_with_room_gives_no_ram_reason_at_all(self, monkeypatch):
        """With room the clause does not fire, which is what keeps the "short of
        residence" reading specific to machines that are short of it.

        This tier is never `met` here - its bytes-per-token is estimated, not
        measured - so the claim is about the *absence of a RAM reason*, not about
        the overall verdict. An earlier version of this test asserted
        `met is True` and was asserting something the table already rules out.
        """
        _Measured(monkeypatch, 16.5, ram=64.0, cores=24, gpu=False)
        _met, missing = local_llm.requirements_met("qwen3-30b-a3b")
        assert not any("GB of RAM" in m for m in missing), missing
