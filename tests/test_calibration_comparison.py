"""Did the importance matrix actually help? The comparison nobody was making.

`calibrated_quantize` builds **two** files on purpose — the same target from the
same source, one fitted with an importance matrix and one without — and its own
return value calls the second one *"beside it for comparison"*. Measured over the
tree before this test: `naive_path` had **no production reader at all**. It was
produced at `local_llm.py` and referenced by exactly one test, which asserts the
two paths *differ* — a fact about the filesystem, not about quality.

So the operation's entire justification went unverified at the only moment
somebody looks at it, and the report a person saw was:

    Built Q4_K_M at <scratch>-calibrated.gguf
    In use now: Q4_K_M

`adopt_path`'s gate compares the candidate against the model being **replaced**,
which is a different question. A rebuild that fitted a matrix for ten seconds and
produced a *worse* file said "Built" and nothing else.

The ratio that matters here is the one this repository keeps recording: the
missing measurement must never be rendered as a measured zero. "llama-perplexity
is absent" and "the matrix changed nothing" are different facts, and only the
second one is a result.
"""

from pathlib import Path

import pytest

from shani_chronoa import local_llm


@pytest.fixture
def scores(monkeypatch):
    """Perplexity for a named file, so a test states which model got which number.

    Keyed by path rather than returned in call order: a positional stub makes
    "the naive build measured worse" true for an arbitrary reason, and the
    verdict would then be a property of the order the test happened to write.
    """
    table = {}

    def fake(path, timeout=180.0):
        return table.get(Path(path).name)

    monkeypatch.setattr(local_llm, "perplexity", fake)
    return table


def _pair(tmp_path, before=1.800, after=1.750):
    """A result shaped exactly as `calibrated_quantize` returns on success."""
    naive, calibrated = tmp_path / "m-Q4_K_M-naive.gguf", tmp_path / "m-Q4_K_M-calibrated.gguf"
    naive.write_bytes(b"n")
    calibrated.write_bytes(b"c")
    return {"ok": True, "calibrated": True, "target": "Q4_K_M",
            "calibrated_path": str(calibrated), "naive_path": str(naive)}


class TestTheComparison:
    def test_a_better_matrix_is_reported_as_helping(self, tmp_path, scores):
        out = _pair(tmp_path, before=1.800, after=1.750)
        scores[Path(out["naive_path"]).name] = 1.800
        scores[Path(out["calibrated_path"]).name] = 1.750

        got = local_llm.compare_calibration(out)

        assert got["verdict"] == "helped", got
        assert got["change_percent"] < 0, got

    def test_a_worse_matrix_is_reported_as_hurting(self, tmp_path, scores):
        """**The case that was invisible.** Not a bug in the code - the absence
        of the measurement - so this is the row that would have caught it."""
        out = _pair(tmp_path)
        scores[Path(out["naive_path"]).name] = 1.750
        scores[Path(out["calibrated_path"]).name] = 1.800

        got = local_llm.compare_calibration(out)

        assert got["verdict"] == "hurt", got
        assert got["change_percent"] > 0, got

    def test_the_sentence_carries_both_numbers_and_not_only_the_percentage(self, tmp_path, scores):
        """A figure derived from two numbers stays true-looking when the numbers
        are dropped, so "0.4% better" alone cannot be checked against anything.

        This is the `merge_models` lesson in a second place: the refusal quotes
        a percentage derived from two perplexities, so a sentence carrying only
        the percentage survives having both figures deleted.
        """
        out = _pair(tmp_path)
        scores[Path(out["naive_path"]).name] = 1.800
        scores[Path(out["calibrated_path"]).name] = 1.750

        why = local_llm.compare_calibration(out)["why"]

        assert "1.800" in why and "1.750" in why, why
        assert "%" in why, why

    def test_two_identical_numbers_are_unchanged_not_helped(self, tmp_path, scores):
        out = _pair(tmp_path)
        scores[Path(out["naive_path"]).name] = 1.800
        scores[Path(out["calibrated_path"]).name] = 1.800

        got = local_llm.compare_calibration(out)

        assert got["verdict"] == "unchanged", (
            "a measured zero is a result and must not read as an improvement")
        assert got["change_percent"] == 0.0, got


class TestWhatIsNotAMeasurement:
    """`None` means unknown. It never means "no difference"."""

    def test_an_unmeasurable_calibrated_build_is_not_zero(self, tmp_path, scores):
        out = _pair(tmp_path)
        scores[Path(out["naive_path"]).name] = 1.800
        # calibrated: absent from the table, so `perplexity` returns None

        got = local_llm.compare_calibration(out)

        assert got["measured"] is False, got
        assert got["verdict"] == "unmeasured", got
        assert got["change_percent"] is None, got

    def test_it_names_which_build_could_not_be_measured(self, tmp_path, scores):
        out = _pair(tmp_path)
        scores[Path(out["calibrated_path"]).name] = 1.750

        why = local_llm.compare_calibration(out)["why"]

        assert "uncalibrated" in why, why
        assert "unknown" in why, (
            f"a missing measurement must say so: {why!r}")

    def test_both_missing_names_both(self, tmp_path, scores):
        got = local_llm.compare_calibration(_pair(tmp_path))

        assert got["measured"] is False, got
        assert "uncalibrated and calibrated" in got["why"], got

    def test_one_file_reported_twice_is_not_a_comparison(self, tmp_path, scores):
        """Two paths that are one path would report a number twice and call the
        result a measurement of nothing."""
        out = _pair(tmp_path)
        out["naive_path"] = out["calibrated_path"]
        scores[Path(out["calibrated_path"]).name] = 1.800

        got = local_llm.compare_calibration(out)

        assert got["measured"] is False, got
        assert got["verdict"] == "unmeasured", got

    def test_a_non_positive_baseline_is_refused_rather_than_divided(self, tmp_path, scores):
        """A percentage of zero or less is not a small number, it is not a number."""
        out = _pair(tmp_path)
        scores[Path(out["naive_path"]).name] = 0.0
        scores[Path(out["calibrated_path"]).name] = 1.750

        got = local_llm.compare_calibration(out)

        assert got["measured"] is False, got
        assert got["change_percent"] is None, got
        assert "1.750" in got["why"], (
            f"the figures it could read must still be reported: {got['why']!r}")

    def test_a_failed_build_has_no_pair_to_compare(self):
        """`ok: False` is what `calibrated_quantize` returns when `llama-imatrix`
        is unavailable — the refusal path, which hands back an uncalibrated file
        under a different key on purpose."""
        got = local_llm.compare_calibration(
            {"ok": False, "calibrated": False, "uncalibrated_fallback": "/x"})

        assert got["measured"] is False, got
        assert "no pair" in got["why"], got


class TestTheReportActuallyCarriesIt:
    """**Pressed, not called.**

    Every test above drives `compare_calibration` directly, which measures the
    behaviour behind the widget and says nothing about whether the Rebuild button
    shows it. That is this repository's own rule - a test that calls the function
    is not a test that the skill is reachable - and the dead-control defect it
    has recorded five times. So this drives the real handler and reads the lines
    a person would see.
    """

    @staticmethod
    def _run(monkeypatch, tmp_path, before, after):
        from shani_chronoa.settings_window import window as win

        out = _pair(tmp_path)
        scores = {Path(out["naive_path"]).name: before,
                  Path(out["calibrated_path"]).name: after}
        monkeypatch.setattr(local_llm, "perplexity", lambda p, **kw: scores.get(Path(p).name))
        monkeypatch.setattr(local_llm.shutil, "which", lambda n: "/usr/bin/" + n)
        # The handler picks its own source first: it scans `model_dir()` for an
        # F16/BF16 file and refuses with "No F16/BF16 model to re-quantize"
        # before `calibrated_quantize` is ever reached. Without this the test
        # measured a refusal and called it a report.
        monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
        (tmp_path / "source-F16.gguf").write_bytes(b"f16")
        monkeypatch.setattr(local_llm, "calibrated_quantize",
                            lambda *a, **kw: out)
        monkeypatch.setattr(local_llm, "adopt_path",
                            lambda *a, **kw: {"promote": True, "measured": True,
                                              "why": "perplexity 1.750 against 1.700"})
        lines = []
        win.SettingsWindow._rebuild_calibrated(lines.append)
        return lines

    def test_a_person_is_told_whether_the_matrix_helped(self, monkeypatch, tmp_path):
        lines = self._run(monkeypatch, tmp_path, 1.800, 1.750)

        assert any("1.800" in line and "1.750" in line for line in lines), lines
        assert any("better" in line for line in lines), lines

    def test_a_worse_build_is_said_so_on_screen(self, monkeypatch, tmp_path):
        lines = self._run(monkeypatch, tmp_path, 1.750, 1.800)

        assert any("worse" in line for line in lines), (
            f"a matrix that made the model worse must say it: {lines}")

    def test_an_unmeasured_comparison_is_announced_rather_than_omitted(
            self, monkeypatch, tmp_path):
        """The absence has to be visible too. Silence reads as "no difference",
        which is the exact substitution this function exists to refuse."""
        lines = self._run(monkeypatch, tmp_path, 1.800, None)

        assert any("NOT measured" in line for line in lines), (
            f"an unmeasured comparison must say so: {lines}")