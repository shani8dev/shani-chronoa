"""A model is not promoted on a matching digest alone.

`provision()` verifies a **digest** and nothing else about the artifact, so a
correctly-hashed bad quant passed every check there was. Meanwhile
`llama-perplexity` is installed on this machine and was **never invoked anywhere in
this repository** — `local_llm.py`, `local_vision.py` and `local_embed.py` all
download pre-quantized GGUFs and point `current.gguf` at one.

Measured on the real models already on this box:

    SmolLM2-135M-Instruct-Q4_K_M   ppl 1.805
    Qwen3-0.6B-Q8_0                 ppl 1.373
    -> promoting the first for the second: 31.5% worse, refused
"""

import subprocess
from pathlib import Path

import pytest

from shani_chronoa import local_llm

#: What `llama-perplexity` actually prints. Verified against the installed
#: binary; my first patterns were `perplexity = ...` and a case-sensitive
#: `\bppl`, so every measurement came back None on a box where the tool runs in
#: four seconds.
REAL_OUTPUT = "[1]1.0114,[2]1.0120,\nFinal estimate: PPL = 1.0128 +/- 0.00145\n"


class _Run:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _fake_ppl(monkeypatch, value, stderr=REAL_OUTPUT):
    """Replace the subprocess call, recording the command it was given."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Run(stdout=value if isinstance(value, str) else "", stderr=stderr)

    monkeypatch.setattr(local_llm.subprocess, "run", fake_run)
    monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
    return seen


class TestPerplexityIsActuallyRead:
    def test_it_parses_the_real_output_format(self, tmp_path, monkeypatch):
        model = tmp_path / "m.gguf"
        model.write_bytes(b"x")
        seen = _fake_ppl(monkeypatch, None)
        assert local_llm.perplexity(model) == pytest.approx(1.0128), (
            "the installed tool prints 'Final estimate: PPL = 1.0128 +/- ...'")
        assert "llama-perplexity" in seen["cmd"][0]
        assert str(model) in seen["cmd"]

    def test_both_models_score_the_same_corpus(self, tmp_path, monkeypatch):
        """Different numbers from different bytes would compare nothing."""
        model = tmp_path / "m.gguf"
        model.write_bytes(b"x")
        corpora = []

        def fake_run(cmd, **kwargs):
            corpora.append(Path(cmd[cmd.index("-f") + 1]).read_text(encoding="utf-8"))
            return _Run(stderr=REAL_OUTPUT)

        monkeypatch.setattr(local_llm.subprocess, "run", fake_run)
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        local_llm.perplexity(model)
        local_llm.perplexity(model)
        assert corpora[0] == corpora[1], "the corpus differs between runs"
        assert corpora[0].strip(), "an empty corpus makes the number meaningless"

    @pytest.mark.parametrize("output", [
        "",                                   # nothing at all
        "perplexity: 3 chunks\n",             # no estimate line
        "Final estimate: PPL = not-a-number", # unparseable
    ])
    def test_unreadable_output_is_unknown_not_a_pass(self, tmp_path, monkeypatch, output):
        model = tmp_path / "m.gguf"
        model.write_bytes(b"x")
        _fake_ppl(monkeypatch, None, stderr=output)
        assert local_llm.perplexity(model) is None, (
            f"unreadable output {output!r} produced a number")

    def test_a_missing_tool_is_unknown(self, tmp_path, monkeypatch):
        model = tmp_path / "m.gguf"
        model.write_bytes(b"x")
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: None)
        assert local_llm.perplexity(model) is None, (
            "no binary must read as 'unknown', never as 'fine'")

    def test_a_missing_model_is_unknown(self, tmp_path, monkeypatch):
        _fake_ppl(monkeypatch, None)
        assert local_llm.perplexity(tmp_path / "absent.gguf") is None

    def test_a_timeout_is_unknown_not_an_exception(self, tmp_path, monkeypatch):
        model = tmp_path / "m.gguf"
        model.write_bytes(b"x")

        def boom(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1.0)

        monkeypatch.setattr(local_llm.subprocess, "run", boom)
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        assert local_llm.perplexity(model) is None


class TestTheGate:
    def _pair(self, tmp_path, monkeypatch, candidate, reference):
        cand = tmp_path / "cand.gguf"
        cand.write_bytes(b"x")
        ref = tmp_path / "ref.gguf"
        ref.write_bytes(b"y")
        values = iter([str(candidate), str(reference)])
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(float(next(values)))
            return _Run(stderr=f"Final estimate: PPL = {seen[-1]} +/- 0.001\n")

        monkeypatch.setattr(local_llm.subprocess, "run", fake_run)
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        return cand, ref

    def test_a_better_model_is_promoted(self, tmp_path, monkeypatch):
        cand, ref = self._pair(tmp_path, monkeypatch, 9.0, 10.0)
        verdict = local_llm.quality_verdict(cand, ref)
        assert verdict["promote"] is True, verdict
        assert verdict["regression"] < 0, verdict

    def test_a_worse_model_is_refused_with_both_numbers(self, tmp_path, monkeypatch):
        """**The measurement from the real box: 1.805 against 1.373.**"""
        cand, ref = self._pair(tmp_path, monkeypatch, 1.805, 1.3726)
        verdict = local_llm.quality_verdict(cand, ref)
        assert verdict["promote"] is False, (
            f"a 31% regression was promoted: {verdict}")
        assert verdict["perplexity"] == pytest.approx(1.805)
        assert verdict["reference"] == pytest.approx(1.3726)
        assert "31.5%" in verdict["why"], verdict["why"]
        # **The absolute numbers too, not only the percentage.** The percentage is
        # derived from them, so a mutation that drops the raw values still leaves
        # "31.5% worse" in the sentence and a percentage-only assertion stays
        # green - which is what happened on the first attempt. Someone reading a
        # refusal needs both figures to decide whether to argue with the limit.
        assert "1.805" in verdict["why"] and "1.373" in verdict["why"], verdict["why"]
        assert "still there" in verdict["why"], (
            "the refusal must say the file is still available - a refusal with no "
            "route forward is a dead end")

    def test_a_small_regression_is_allowed(self, tmp_path, monkeypatch):
        cand, ref = self._pair(tmp_path, monkeypatch, 10.15, 10.0)
        assert local_llm.quality_verdict(cand, ref)["promote"] is True

    def test_unmeasured_promotes_but_says_so(self, tmp_path, monkeypatch):
        """A minimal install without the tool must still be able to pick a model."""
        cand = tmp_path / "c.gguf"
        cand.write_bytes(b"x")
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: None)
        verdict = local_llm.quality_verdict(cand, tmp_path / "ref.gguf")
        assert verdict["promote"] is True, verdict
        assert verdict["measured"] is False
        assert "unverified" in verdict["why"], verdict["why"]

    def test_use_returns_the_verdict_and_can_skip_the_gate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        monkeypatch.setattr(local_llm, "current_link", lambda: tmp_path / "current.gguf")
        key = next(iter(local_llm.SPECS))
        (tmp_path / local_llm.SPECS[key].filename).write_bytes(b"x")
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: None)

        verdict = local_llm.use(key)
        assert verdict["promote"] is True and verdict["measured"] is False
        assert (tmp_path / "current.gguf").is_symlink(), "the link was not made"

        # And a refused promotion leaves the link alone.
        (tmp_path / "current.gguf").unlink()
        (tmp_path / local_llm.SPECS[key].filename).write_bytes(b"y")
        monkeypatch.setattr(local_llm, "quality_verdict",
                            lambda *a, **k: {"promote": False, "measured": True,
                                             "why": "worse"})
        refused = local_llm.use(key)
        assert refused["promote"] is False
        assert not (tmp_path / "current.gguf").exists(), (
            "a refused promotion still swapped the model")
        # gate=False is the escape hatch, and it must work.
        assert local_llm.use(key, gate=False)["promote"] is True
        assert (tmp_path / "current.gguf").is_symlink()
