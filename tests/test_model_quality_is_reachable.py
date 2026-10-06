"""Two model actions a person may want without switching anything.

`local_llm.perplexity()` and `quality_verdict()` run `llama-perplexity` — which
is installed on this machine and was **never invoked anywhere in this
repository** — but were reachable only through `use()`, i.e. only as a side effect
of changing which model is loaded. `calibrated_quantize()` had no caller at all.

Both are buttons on the Models page now, and both run off the main thread:
perplexity on a real model takes seconds and calibration takes minutes.
"""

import inspect

from shani_chronoa.settings_window.window import SettingsWindow


class TestBothRunOffTheMainThread:
    def test_the_runner_uses_a_daemon_thread(self):
        source = inspect.getsource(SettingsWindow._off_main)
        assert "threading.Thread" in source, source
        assert "daemon=True" in source, source

    def test_a_button_is_re_enabled_afterwards(self):
        """A button left disabled ends the feature for the rest of the session."""
        source = inspect.getsource(SettingsWindow._off_main)
        assert "set_sensitive(False)" in source, source
        assert source.count("set_sensitive(True)") >= 1, source
        # And on the failure path too, or one error is a dead end.
        assert "Failed:" in source, source

    def test_it_reports_rather_than_raising(self):
        source = inspect.getsource(SettingsWindow._off_main)
        assert "except Exception" in source, source


class TestMeasuringSaysSoWhenThereIsNothingToMeasure:
    def test_no_model_installed(self, monkeypatch, tmp_path):
        from shani_chronoa import local_llm

        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        monkeypatch.setattr(local_llm, "current_link", lambda: tmp_path / "current.gguf")
        said = []
        SettingsWindow._measure_quality(said.append)
        assert said and "No model" in said[-1], said

    def test_it_never_invents_a_number(self, monkeypatch, tmp_path):
        """A refused measurement must read as refused, not as a pass."""
        from shani_chronoa import local_llm

        (tmp_path / "a.gguf").write_bytes(b"x")
        # `current.gguf` has to EXIST, or the handler answers "No model is
        # installed yet" and never reaches the verdict at all - which is what my
        # first version did, so the test passed for the wrong reason.
        (tmp_path / "current.gguf").symlink_to(tmp_path / "a.gguf")
        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        monkeypatch.setattr(local_llm, "current_link", lambda: tmp_path / "current.gguf")
        monkeypatch.setattr(local_llm, "quality_verdict",
                            lambda *a, **k: {"measured": False, "why": "no tool"})
        said = []
        SettingsWindow._measure_quality(said.append)
        assert "Not measurable" in said[-1], said


class TestRebuildRefusesRatherThanPretending:
    def test_no_imatrix_tool(self, monkeypatch, tmp_path):
        from shani_chronoa import local_llm

        monkeypatch.setattr(local_llm.shutil, "which", lambda n: None)
        said = []
        SettingsWindow._rebuild_calibrated(said.append)
        assert "llama-imatrix" in said[-1], said

    def test_no_high_precision_source(self, monkeypatch, tmp_path):
        """Re-quantizing an already-quantized file is meaningless and the error
        is not obvious - the output is simply worse than the input while
        claiming to be an improvement."""
        from shani_chronoa import local_llm

        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        (tmp_path / "q4_k_m.gguf").write_bytes(b"x")
        said = []
        SettingsWindow._rebuild_calibrated(said.append)
        assert "F16" in said[-1] or "BF16" in said[-1], said

    def test_it_offers_a_refusal_verbatim(self, monkeypatch, tmp_path):
        from shani_chronoa import local_llm

        (tmp_path / "model-F16.gguf").write_bytes(b"x")
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        monkeypatch.setattr(local_llm, "calibrated_quantize",
                            lambda src, target, **k: {"ok": False,
                                                      "why": "llama-imatrix is unavailable"})
        said = []
        SettingsWindow._rebuild_calibrated(said.append)
        assert "Refused" in said[-1] and "llama-imatrix" in said[-1], said

    def test_a_success_names_the_target_and_the_path(self, monkeypatch, tmp_path):
        """**Strengthened, not weakened.** This used to assert that the target
        name appeared somewhere in the reports, which was the whole of the old
        contract: `_rebuild_calibrated` said `Done: Q4_K_M` and the built file
        went nowhere, because nothing could point `current.gguf` at a model the
        `SPECS` catalogue does not name. So the old assertion passed for a
        feature that could not be used.

        It now requires all three of the things that make the feature real: the
        path is named, `adopt_path` is called on it, and the outcome of the
        quality gate is reported either way.
        """
        from shani_chronoa import local_llm

        (tmp_path / "model-F16.gguf").write_bytes(b"x")
        built = tmp_path / "model-Q4_K_M-calibrated.gguf"
        built.write_bytes(b"quantized")
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        monkeypatch.setattr(local_llm, "calibrated_quantize",
                            lambda src, target, **k: {"ok": True, "target": target,
                                                      "calibrated_path": str(built)})
        adopted = []
        monkeypatch.setattr(local_llm, "adopt_path",
                            lambda path, **k: (adopted.append(path),
                                               {"promote": True,
                                                "why": "perplexity 42.571"})[1])
        said = []
        SettingsWindow._rebuild_calibrated(said.append)
        assert any("Q4_K_M" in line for line in said), said
        assert any(str(built) in line for line in said), (
            f"the built file was never named, so nobody can find it: {said}")
        assert adopted == [built], f"the rebuilt model was not put into use: {adopted}"
        assert any("In use now" in line for line in said), said

    def test_reclaim_reports_what_it_freed_and_what_it_kept(self, monkeypatch, tmp_path):
        """The reclaim row had **no test at all**, and a mutation proved it:
        replacing its call with a hardcoded "nothing to do" left every test
        green. A UI affordance that silently does nothing is worse than one that
        is absent, because the button still looks right.

        Reaching it required lifting the handler out of the closure it was in -
        `_build_models` cannot be called without a display - which is the same
        reason `_rebuild_calibrated` is already a staticmethod.
        """
        from shani_chronoa import local_llm

        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        idle = tmp_path / "chronoa-quant-idle"
        idle.mkdir()
        (idle / "m.gguf").write_bytes(b"x" * 4096)
        live = tmp_path / "chronoa-quant-live"
        live.mkdir()
        (live / "m.gguf").write_bytes(b"y" * 16)
        local_llm.adopt_path(live / "m.gguf", gate=False)

        said = []
        SettingsWindow._reclaim_scratch(said.append)
        joined = "\n".join(said)
        # The property, not a magic number: 4096 bytes is "4.1 kB" (decimal
        # kB, not KiB), and it must not be rounded away to "0 MB". My first
        # assertion here was `"4.0 kB"`, which failed on correct output - I had
        # divided by 1024 in my head and by 1000 in the code.
        assert "0 MB" not in joined, (
            f"a reclaim that freed real bytes reported nothing useful: {said}")
        assert any(unit in joined for unit in ("kB", "MB", "GB")), (
            f"the reclaimed size is not stated: {said}")
        assert not idle.exists(), f"the idle directory was not reclaimed: {said}"
        assert live.exists(), "the live model's directory was deleted"
        assert any("kept" in line for line in said), (
            f"a directory it declined to delete was not reported: {said}")

    def test_reclaim_says_so_when_there_is_nothing_to_reclaim(self, monkeypatch, tmp_path):
        from shani_chronoa import local_llm

        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        said = []
        SettingsWindow._reclaim_scratch(said.append)
        assert any("Nothing to reclaim" in line for line in said), said

    def test_a_refused_adoption_is_reported_with_the_path(self, monkeypatch, tmp_path):
        """A gate refusal must not look like a rebuild that worked."""
        from shani_chronoa import local_llm

        (tmp_path / "model-F16.gguf").write_bytes(b"x")
        built = tmp_path / "model-Q4_K_M-calibrated.gguf"
        built.write_bytes(b"quantized")
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        monkeypatch.setattr(local_llm, "calibrated_quantize",
                            lambda src, target, **k: {"ok": True, "target": target,
                                                      "calibrated_path": str(built)})
        monkeypatch.setattr(local_llm, "adopt_path",
                            lambda path, **k: {"promote": False,
                                               "why": "perplexity 44.2 against 43.0"})
        said = []
        SettingsWindow._rebuild_calibrated(said.append)
        assert any("NOT put into use" in line for line in said), said
        assert any("44.2" in line for line in said), (
            f"the reason for the refusal was not given: {said}")
