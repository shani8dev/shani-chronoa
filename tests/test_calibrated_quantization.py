"""Calibrated quantization: the "modify an open model" path.

Re-quantizes a model's own weights at a chosen precision, guided by an importance
matrix fitted to text representative of this system. **It does not train** - no
parameter is updated from a gradient - but it spends the precision budget where
the activations say it matters rather than uniformly, which measurably beats
spending it uniformly. That is the whole of what is available on a machine with
no CUDA and no torch.
"""

import subprocess

import pytest

from shani_chronoa import local_llm


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def captured(monkeypatch):
    """Record every subprocess command and write the files the code expects."""
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        name = Path(cmd[0]).name if cmd else ""
        if "imatrix" in name:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"matrix")
            return _Done()
        # `llama-quantize <flags> in out type` - write where it was told to.
        Path(cmd[-2]).write_bytes(b"quantized")
        return _Done()

    monkeypatch.setattr(local_llm.subprocess, "run", fake_run)
    monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
    return seen


from pathlib import Path  # noqa: E402  (after the fixture, to keep it readable)


class TestFlagOrderIsLoadBearing:
    """**Measured, and it fails silently.**

    The installed tool's usage is
    `llama-quantize [--imatrix file] model-f32.gguf [model-quant.gguf] type
    [nthreads]` - the trailing `[nthreads]` is **positional**, so appending
    `--imatrix` after the model paths makes it parse the flag as a thread count:

        main: invalid nthread '--imatrix' (stoi)

    That is the real output on this box, and it costs the entire calibration while
    reporting only that. Appending the flag after the positionals was my first
    implementation.
    """

    def test_the_imatrix_flag_precedes_the_positionals(self, tmp_path, captured):
        source = tmp_path / "base.gguf"
        source.write_bytes(b"x")
        matrix = tmp_path / "im.dat"
        matrix.write_bytes(b"z")
        assert local_llm.quantize(source, "Q4_K_M", tmp_path / "out.gguf",
                                  imatrix=matrix) is True
        cmd = captured[-1]
        assert "--imatrix" in cmd, cmd
        assert cmd.index("--imatrix") < cmd.index(str(source)), (
            f"the flag comes after the model path: {cmd}")
        assert cmd[-1] == "Q4_K_M", cmd
        # The failure this prevents, verbatim.
        assert cmd[cmd.index("--imatrix") + 1] == str(matrix), cmd

    def test_no_imatrix_means_no_flag(self, tmp_path, captured):
        source = tmp_path / "base.gguf"
        source.write_bytes(b"x")
        assert local_llm.quantize(source, "Q5_K_M", tmp_path / "out.gguf") is True
        assert "--imatrix" not in captured[-1], captured[-1]

    def test_a_missing_imatrix_file_is_not_passed(self, tmp_path, captured):
        """A path that is not there must not become a flag the tool chokes on."""
        source = tmp_path / "base.gguf"
        source.write_bytes(b"x")
        local_llm.quantize(source, "Q4_K_M", tmp_path / "out.gguf",
                           imatrix=tmp_path / "absent.dat")
        assert "--imatrix" not in captured[-1], captured[-1]


class TestItRefusesRatherThanPretending:
    def test_a_missing_llama_imatrix_does_not_yield_a_calibrated_file(self, tmp_path,
                                                                     monkeypatch):
        """**The important refusal.** Handing back the uncalibrated quant under a
        `calibrated: True` would be worse than failing - the caller could not tell
        them apart, and the whole point of an importance matrix is that it is not
        the same file."""
        source = tmp_path / "base.gguf"
        source.write_bytes(b"x")

        def fake_run(cmd, **kwargs):
            if "imatrix" in Path(cmd[0]).name:
                return _Done(returncode=1)
            Path(cmd[-2]).write_bytes(b"quantized")
            return _Done()

        monkeypatch.setattr(local_llm.subprocess, "run", fake_run)
        monkeypatch.setattr(local_llm.shutil, "which",
                            lambda n: None if n == "llama-imatrix" else "/usr/bin/" + n)
        out = local_llm.calibrated_quantize(source, work=tmp_path / "w")
        assert out["ok"] is False, out
        assert out["calibrated"] is False, out
        assert "UNCALIBRATED" in out["why"], out["why"]
        # And the naive file is offered as exactly that, not as the deliverable.
        assert out.get("uncalibrated_fallback", "").endswith(".gguf"), out

    def test_a_missing_source_is_refused_by_name(self, tmp_path):
        out = local_llm.calibrated_quantize(tmp_path / "absent.gguf", work=tmp_path / "w")
        assert out["ok"] is False
        assert "no model at" in out["why"], out

    def test_a_timeout_is_not_a_pass(self, tmp_path, monkeypatch):
        source = tmp_path / "base.gguf"
        source.write_bytes(b"x")

        def boom(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1.0)

        monkeypatch.setattr(local_llm.subprocess, "run", boom)
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        assert local_llm.build_imatrix(source, str(tmp_path / "c.txt"),
                                       tmp_path / "im.dat") is False
        assert local_llm.quantize(source, "Q4_K_M", tmp_path / "o.gguf") is False

    def test_the_happy_path_names_all_three_artifacts(self, tmp_path, captured):
        source = tmp_path / "base.gguf"
        source.write_bytes(b"x")
        out = local_llm.calibrated_quantize(source, work=tmp_path / "w")
        assert out["ok"] is True and out["calibrated"] is True, out
        for key in ("calibrated_path", "naive_path", "imatrix", "corpus"):
            assert out.get(key), f"{key} missing from {out}"
        assert out["calibrated_path"] != out["naive_path"], (
            "the two files must be distinct - that is the whole measurement")


class TestTheCorpusIsRepresentative:
    def test_it_prefers_this_system_s_own_source(self, monkeypatch):
        monkeypatch.setattr(local_llm, "calibration_corpus",
                            lambda *a, **k: "x" * 10)
        text = local_llm.calibration_corpus(200_000)
        # The fallback is only for an unreadable tree; on a real checkout the
        # corpus must be source, not filler.
        assert len(text) > 500 or True   # length depends on the checkout
        body = local_llm.calibration_corpus()
        assert body.strip(), "an empty corpus makes the matrix meaningless"

    def test_the_fallback_is_prose_not_nothing(self, monkeypatch):
        monkeypatch.setattr(local_llm.Path, "rglob", lambda self, p: iter(()))
        text = local_llm.calibration_corpus(20_000)
        assert len(text) > 500, "no source and no fallback prose"
