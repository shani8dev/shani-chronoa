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

    def test_a_success_names_the_target(self, monkeypatch, tmp_path):
        from shani_chronoa import local_llm

        (tmp_path / "model-F16.gguf").write_bytes(b"x")
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        monkeypatch.setattr(local_llm, "calibrated_quantize",
                            lambda src, target, **k: {"ok": True, "target": target})
        said = []
        SettingsWindow._rebuild_calibrated(said.append)
        assert "Q4_K_M" in said[-1], said
