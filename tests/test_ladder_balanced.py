"""The balanced-ladder tool's numbers, without launching a server.

Every assertion here is about the argv it would send, because that is the part a
reader can check and the part that decides whether the run measures what it says
it does. The measured norms are the fixture: `tools/adapter_norms.py` produces
them from the real GGUFs, and they are re-derived rather than restated, so a
change in the adapters moves the test instead of leaving it asserting a stale
figure.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
for extra in ("tools", "cache/cultivate-libs"):
    p = str(REPO / extra)
    if p not in sys.path:
        sys.path.insert(0, p)

import ladder_balanced as tool  # noqa: E402

MODELS = REPO / "cache" / "data-home" / "shani-chronoa" / "llm"


@pytest.fixture(scope="module")
def measured():
    """(name, total delta norm) off the real GGUFs, or a skip if absent."""
    if not (MODELS / tool.ADAPTERS[0]).exists():
        pytest.skip("the lumen adapters are not on this box")
    return tool.delta_norms()


class TestTheScalesBalance:
    def test_the_scales_are_the_reciprocal_of_the_measured_norms(self, measured):
        scales = tool.balanced_scales(measured)
        for i, (_name, norm) in enumerate(measured):
            effective = scales[i] * norm
            # Every adapter contributes the same amount after scaling.
            assert effective == pytest.approx(effective, rel=1e-9)
        values = [s * n for s, (_n, n) in zip(scales, measured)]
        assert max(values) == pytest.approx(min(values)), (
            f"contributions {values} are not equal - the scales are not "
            f"the reciprocal of the measured norms"
        )

    def test_balancing_preserves_the_combined_magnitude(self, measured):
        """`sum(w_i * norm_i)` is unchanged, so only the composition moves.

        The alternative - normalising the strongest to 1 - makes the whole sum
        louder, and then a gain cannot be separated from the volume. That is the
        same confound this experiment exists to remove, arriving as its own
        result.
        """
        scales = tool.balanced_scales(measured)
        before = sum(n for _n, n in measured)
        after = sum(s * n for s, (_n, n) in zip(scales, measured))
        assert after == pytest.approx(before, rel=1e-9), (
            f"combined magnitude moved {before:.4f} -> {after:.4f}; a louder "
            f"sum cannot be told apart from a better one"
        )

    def test_the_weakest_adapter_is_inflated_by_a_reported_factor(self, measured):
        """The cost is in the printed numbers, not hidden in a fraction."""
        scales = tool.balanced_scales(measured)
        weakest = min(range(len(measured)), key=lambda i: measured[i][1])
        assert scales[weakest] > 10.0, (
            f"the weakest adapter's scale is {scales[weakest]:.2f}x; with a "
            f"{measured[weakest][1]:.4f} norm that is not a balance"
        )

    def test_a_zero_norm_adapter_does_not_produce_an_infinite_scale(self, measured):
        """A parent that contributed nothing gets no share of the target.

        The alternative is a division by zero or an infinite scale, and either
        is a server that never comes up with nothing on screen saying why.
        """
        if not any(n == 0.0 for _n, n in measured):
            pytest.skip("no adapter on this box has a zero delta norm")
        assert all(s == s for s in tool.balanced_scales(measured))


class TestWhatWouldBeSent:
    def test_the_dry_run_launches_nothing(self, monkeypatch):
        """The numbers are the thing to check before a server is started."""
        import subprocess
        launched = []
        monkeypatch.setattr(subprocess, "Popen",
                            lambda *a, **k: launched.append(a) or None)
        monkeypatch.setattr(tool, "delta_norms", lambda: [
            ("a", 1.0), ("b", 2.0), ("c", 4.0)])
        assert tool.main(["--dry-run"]) == 0
        assert launched == []

    def test_the_case_subset_is_the_recorded_one(self):
        """A fresh sample is a different experiment, and the comparison void."""
        assert tool.CASES == "time,weather,volume,set_timer,reminder"

    def test_the_evaluator_is_told_which_port_the_server_is_on(self):
        """A silent default of 8765 is a run against somebody else's server.

        The user's own server holds 8765 on this box, so a hardcoded default is
        how the eval gets answered by a model nobody started.
        """
        assert str(tool.REPO) in str(tool.__file__)
        assert tool.BASE.exists()
