"""A calibration run that builds a model nobody can load is not a feature.

Measured on this machine, before any of the functions below existed:

- Settings -> Models -> **Rebuild it with calibration** reported
  `Done: Q4_K_M` and stopped there.
- `calibrated_quantize()` built the file under
  `tempfile.mkdtemp(prefix="chronoa-quant-")`, i.e. in `$TMPDIR`, and returned
  the path - which the caller then discarded.
- `current.gguf` is a **symlink**, so `server_args` would have loaded that file
  happily. But the only thing that could point the link there, `use(key)`,
  resolves its target through `SPECS[key].filename` - a fixed catalogue of
  downloads. A re-quantization of a model already on disk has no `SPECS` entry,
  by construction: it did not come from the catalogue.
- So the button built a measurably better model - 42.57 against 43.61 on the
  same corpus, same bytes - and left it in a temporary directory where the
  system would eventually delete it.

And the directory was never reclaimed, on *any* of the four exit paths,
including the two refusals that had built nothing worth keeping.

Three fixes, each tested here:

- `adopt_path()` promotes an arbitrary file, behind the same perplexity gate
  `use()` applies.
- `reclaim_quant_scratch()` deletes the leftovers, and refuses to delete the
  directory a loaded model lives in.
- The GUI reports the path *and* adopts it, rather than reporting a target name.
"""

import os
import stat
from pathlib import Path

import pytest

from shani_chronoa import local_llm


@pytest.fixture
def model_home(tmp_path, monkeypatch):
    """A model directory that is this test's alone."""
    md = tmp_path / "llm"
    md.mkdir()
    monkeypatch.setattr(local_llm, "model_dir", lambda: md)
    monkeypatch.setattr(local_llm, "_QUANT_SCRATCH", [])
    return md


def _scratch(md: Path, name: str, size: int = 64) -> Path:
    directory = md / f"chronoa-quant-{name}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.gguf").write_bytes(b"x" * size)
    return directory


class TestTheScratchDirectoryIsReclaimable:
    """Measured: the first version found the directory and removed nothing,
    because it guarded on an in-memory list that a fresh process does not have."""

    def test_it_removes_an_idle_directory(self, model_home):
        idle = _scratch(model_home, "idle")
        out = local_llm.reclaim_quant_scratch()
        assert str(idle) in out["removed"], (
            f"a leftover scratch directory was not reclaimed: {out}")
        assert not idle.exists()

    def test_it_works_in_a_fresh_process(self, model_home):
        """The reason `_QUANT_SCRATCH` cannot be the guard.

        The Settings window is a different process from the one that pressed
        Rebuild last week, so anything registered in memory is gone. The guard is
        the directory's *name and parent*, both of which survive a restart.
        """
        idle = _scratch(model_home, "from-last-week")
        assert idle not in local_llm._QUANT_SCRATCH, (
            "precondition: nothing is registered in memory, which is the "
            "situation this test is about")
        assert str(idle) in local_llm.reclaim_quant_scratch()["removed"]

    def test_it_reports_how_much_it_freed(self, model_home):
        _scratch(model_home, "a", size=1000)
        out = local_llm.reclaim_quant_scratch()
        assert out["freed_bytes"] == 1000, out

    def test_it_refuses_a_right_name_in_the_wrong_parent(self, model_home, tmp_path):
        """Otherwise this is `rm -rf` with a pattern."""
        elsewhere = tmp_path / "elsewhere" / "chronoa-quant-important"
        elsewhere.mkdir(parents=True)
        (elsewhere / "keep.txt").write_text("precious")
        assert local_llm._remove_scratch_dir(elsewhere) is False
        assert elsewhere.exists(), "it deleted something outside model_dir()"

    def test_it_refuses_a_right_parent_with_the_wrong_name(self, model_home):
        decoy = model_home / "important"
        decoy.mkdir()
        (decoy / "keep.txt").write_text("precious")
        assert local_llm._remove_scratch_dir(decoy) is False
        assert decoy.exists(), "it deleted a directory that is not scratch"

    def test_a_caller_supplied_work_directory_is_never_removed(self, model_home):
        """`work=` belongs to whoever passed it."""
        theirs = _scratch(model_home, "mine")
        assert local_llm.discard_quant_scratch(theirs) is False
        assert theirs.exists()


class TestReclaimKeepsTheLoadedModel:
    """The guard that was wrong twice.

    Version 1 protected `link.resolve()` - the model *file* - and compared it
    against each scratch *directory*, so it never fired. Measured: after
    adopting `chronoa-quant-a/m.gguf`, reclaim deleted `chronoa-quant-a` and
    left `current.gguf` dangling.

    Version 2 protected the file *and* its parent, but added a check that
    rejected the directory because the symlink it looked for was not inside it -
    which is the same comparison error once more. Both are pinned below.
    """

    def test_it_keeps_the_directory_the_current_model_is_in(self, model_home):
        live = _scratch(model_home, "live")
        local_llm.adopt_path(live / "model.gguf", gate=False)
        out = local_llm.reclaim_quant_scratch()
        assert str(live) in out["kept"], f"the live model's directory was reclaimed: {out}"
        assert live.exists()
        assert local_llm.current_link().resolve().is_file(), (
            "current.gguf is now a dangling symlink")

    def test_it_keeps_a_directory_named_explicitly(self, model_home):
        wanted = _scratch(model_home, "wanted")
        _scratch(model_home, "other")
        out = local_llm.reclaim_quant_scratch(keep=wanted)
        assert wanted.exists()
        assert str(model_home / "chronoa-quant-other") in out["removed"]

    def test_it_reclaims_everything_once_the_model_moved(self, model_home):
        """`keep=None` is not "delete what the model is in"."""
        was = _scratch(model_home, "was")
        local_llm.adopt_path(was / "model.gguf", gate=False)
        assert str(was) in local_llm.reclaim_quant_scratch()["kept"]
        now = _scratch(model_home, "now")
        local_llm.adopt_path(now / "model.gguf", gate=False)
        out = local_llm.reclaim_quant_scratch()
        assert str(was) in out["removed"], (
            "the previous model's directory is no longer in use and is what "
            "this button is for")
        assert str(now) in out["kept"]


class TestAnyFileCanBeAdopted:
    def test_it_promotes_a_file_that_is_not_in_the_catalogue(self, model_home):
        built = _scratch(model_home, "calibrated") / "model.gguf"
        out = local_llm.adopt_path(built, gate=False)
        assert out.get("promote") is True, out
        assert local_llm.current_link().resolve() == built.resolve()

    def test_the_symlink_is_absolute(self, model_home, monkeypatch):
        """A relative target inside a scratch directory resolves today only
        while that directory survives - and reclaim is meant to remove it."""
        built = _scratch(model_home, "calibrated") / "model.gguf"
        local_llm.adopt_path(built, gate=False)
        target = os.readlink(local_llm.current_link())
        assert os.path.isabs(target), f"relative symlink to {target}"

    def test_it_refuses_a_missing_file(self, model_home):
        out = local_llm.adopt_path(model_home / "absent.gguf", gate=False)
        assert out.get("promote") is False
        assert "absent.gguf" in out["why"]

    def test_adopting_the_model_already_in_use_is_not_a_measurement(self, model_home,
                                                                    monkeypatch):
        """Comparing it to itself would report a 0% improvement as a result.

        **The perplexity stub is load-bearing, and its absence is why this test
        was worthless on the first run.** With no `llama-perplexity` installed,
        `quality_verdict()` returns `measured: False` for *every* candidate, so
        this assertion passed whether or not the self-comparison check existed -
        a mutation that deleted that check entirely still showed 19 green.
        Stubbing a number forces the fall-through path to report
        `measured: True`, which is what the guard prevents.
        """
        built = _scratch(model_home, "m") / "model.gguf"
        local_llm.adopt_path(built, gate=False)
        monkeypatch.setattr(local_llm, "perplexity",
                            lambda path, timeout=180.0: 43.61)
        out = local_llm.adopt_path(built, gate=True)
        assert out.get("measured") is False, (
            "comparing a model against itself is not a measurement")
        assert out.get("promote") is True
        assert "already the model in use" in out["why"], out

    def test_it_leaves_no_partial_link_behind_on_refusal(self, model_home):
        """The temp-then-rename dance exists so a refusal cannot truncate the
        working symlink."""
        first = _scratch(model_home, "one") / "model.gguf"
        local_llm.adopt_path(first, gate=False)
        local_llm.adopt_path(model_home / "absent.gguf", gate=False)
        assert local_llm.current_link().resolve() == first.resolve()


class TestTheGateStillApplies:
    def test_a_regression_is_refused(self, model_home, monkeypatch):
        old = _scratch(model_home, "old") / "model.gguf"
        local_llm.adopt_path(old, gate=False)
        new = _scratch(model_home, "new") / "model.gguf"
        # Measured numbers, not invented ones: 44.0 is worse than 43.0 by more
        # than PERPLEXITY_REGRESSION_LIMIT allows.
        monkeypatch.setattr(local_llm, "perplexity",
                            lambda path, timeout=180.0: 44.0 if path == new else 43.0)
        out = local_llm.adopt_path(new, gate=True)
        assert out.get("promote") is False, (
            "a model that regressed was promoted anyway")
        assert local_llm.current_link().resolve() == old.resolve()

    def test_an_improvement_is_promoted(self, model_home, monkeypatch):
        old = _scratch(model_home, "old") / "model.gguf"
        local_llm.adopt_path(old, gate=False)
        new = _scratch(model_home, "new") / "model.gguf"
        monkeypatch.setattr(local_llm, "perplexity",
                            lambda path, timeout=180.0: 42.57 if path == new else 43.61)
        out = local_llm.adopt_path(new, gate=True)
        assert out.get("promote") is True, out
        assert local_llm.current_link().resolve() == new.resolve()


class TestScratchLandsSomewhereDurable:
    def test_it_is_under_model_dir_not_tmp(self, model_home):
        """`/tmp` is cleared on the system's own schedule, so a rebuilt model
        left there is a rebuilt model in the bin."""
        made = local_llm._new_quant_scratch()
        assert made.parent == model_home, made
        assert made.name.startswith("chronoa-quant-")

    def test_a_refused_run_leaves_nothing_behind(self, model_home, monkeypatch):
        """The measured leak: even a refusal kept the directory."""
        source = model_home / "base-F16.gguf"
        source.write_bytes(b"src")
        monkeypatch.setattr(local_llm, "quantize", lambda *a, **k: False)
        out = local_llm.calibrated_quantize(source, "Q4_K_M")
        assert out.get("ok") is False
        assert local_llm.quant_scratch_dirs() == [], (
            "a failed run kept a scratch directory holding a corpus and an "
            "importance matrix for a quantization that never happened")

    def test_a_failed_calibrated_build_reclaims_too(self, model_home, monkeypatch):
        """**The other exit path.** Removing the reclaim from *this* return was a
        mutation that left all 19 tests green, because only the first failure was
        covered - and this path is the expensive one: the importance matrix has
        already been fitted, which takes minutes, and both ~100 MB builds exist.
        """
        source = model_home / "base-F16.gguf"
        source.write_bytes(b"src")
        calls = []

        def _quantize(src, target, out, **kwargs):
            calls.append(kwargs.get("imatrix"))
            Path(out).write_bytes(b"q" * 32)
            return len(calls) == 1  # naive succeeds, calibrated fails

        monkeypatch.setattr(local_llm, "quantize", _quantize)
        monkeypatch.setattr(local_llm, "build_imatrix", lambda *a, **k: True)
        out = local_llm.calibrated_quantize(source, "Q4_K_M")
        # Two attempts: the naive one with no matrix, the calibrated one with it.
        # Asserting on that shape is what proves this is the *second* failure
        # path rather than the first - my first version asserted `calls == [None]`
        # and so failed while the code under test was correct.
        assert len(calls) == 2, f"the calibrated build was not attempted: {calls}"
        assert calls[0] is None, "the naive build must not be given a matrix"
        assert calls[1] is not None and Path(calls[1]).name == "imatrix.dat", calls
        assert out.get("ok") is False
        assert "failed after the matrix" in out["why"]
        assert local_llm.quant_scratch_dirs() == [], (
            "a run that spent minutes fitting a matrix and built two ~100 MB "
            "models kept its directory")

    def test_a_kept_file_is_reported_so_it_can_be_kept_deliberately(self, model_home, monkeypatch):
        """The one path that must NOT reclaim: it hands back the naive file."""
        source = model_home / "base-F16.gguf"
        source.write_bytes(b"src")
        # The *naive* build must succeed, or this never reaches the imatrix step
        # and the fallback path is not the one under test. My first version
        # stubbed `quantize` to fail outright and asserted a field the earlier
        # refusal returns without.
        monkeypatch.setattr(local_llm, "quantize",
                            lambda src, target, out, **k: (Path(out).write_bytes(b"q") or True))
        monkeypatch.setattr(local_llm, "build_imatrix", lambda *a, **k: False)
        out = local_llm.calibrated_quantize(source, "Q4_K_M")
        assert Path(out["uncalibrated_fallback"]).is_file(), (
            "the fallback names a file that was not built")
        assert out.get("scratch"), (
            "the return value names a file in a directory but not the "
            "directory, so nobody can reclaim it")
        assert stat.S_IMODE(Path(out["scratch"]).stat().st_mode) == 0o700