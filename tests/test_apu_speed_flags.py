"""The three settings chosen from measurements on this APU.

Flash attention and `nogttspill` were each a consistent win across two runs;
the 4096 context floor is the smallest window that holds Chronoa's own prompt
and tool list on the model currently installed. None of this is a guess from
a doc: the rates are in AGENTS.md beside the sweep that produced them.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ctxfit, local_llm  # noqa: E402


def test_flash_attention_is_requested_not_left_to_auto():
    args = local_llm.server_args(devices=[])
    assert args[args.index("-fa") + 1] == "on", (
        "auto can land on off, and the APU measurement only covered on")


def test_the_gpu_ladder_cannot_choose_2048():
    """When nothing fits, the last rung is what the server gets.

    A 2048 last rung answered health but refused a real 2775-token prompt with
    `exceed_context_size_error`; 4096 answers a 3700-token one.
    """
    assert local_llm.GPU_CONTEXT_LADDER[-1] == 4096
    got = ctxfit.context_that_fits(
        2_500_000_000, local_llm.GPU_CONTEXT_LADDER,
        total_bytes=2 * 1024**3, per_token=78336)
    assert got == 4096, "the ladder bottom is the fallback on a small APU"


def test_radv_flag_only_for_radv_devices(monkeypatch, tmp_path):
    monkeypatch.setattr(local_llm, "server_args", lambda devices=None: ["-m", "m.gguf"])
    monkeypatch.setattr(local_llm.files, "config_home", lambda: tmp_path)

    monkeypatch.setattr(local_llm, "gpu_devices", lambda: ["Vulkan0: AMD RADV RAVEON"])
    radv = local_llm._env_text()
    assert "RADV_PERFTEST=nogttspill" in radv

    monkeypatch.setattr(local_llm, "gpu_devices", lambda: ["Vulkan0: Intel Iris"])
    other = local_llm._env_text()
    assert "RADV_PERFTEST" not in other, "a non-AMD driver saw an AMD-only flag"


def test_writer_and_refresher_share_one_text():
    """A second copy of the env format is how the two drift apart."""
    src = pathlib.Path(local_llm.__file__).read_text()
    assert "wanted = _env_text()" in src
    assert "path.write_text(_env_text(devices)" in src
