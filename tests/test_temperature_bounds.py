"""The temperature the reader divides by, and the one the fit already bounded.

`harness-study/laya` (the decision model) fits per-bucket temperatures for its
logits and states the rule its reader holds itself to, in `laya/calibrate.py`:

> Fitted temperatures go through `clamp_temperature` and therefore `[TEMP_MIN,
> TEMP_MAX]` from `laya.common`, **the same guard checkpoint load uses.** There is
> no second pair of bounds in this module.

and in `laya/common.py`, the same clamp, on what the clamp is *for*:

> `TEMP_MIN` (0.5) ... publishes that same 0.24 as 0.4993 - **so a threshold
> applied to these numbers still selects below model accuracy (issue #394).**
> The clamp bounds the damage; it does not calibrate anything.

Chronoa *had* the bounds - `fit_calibration` searches `log(_T_RANGE[0])` to
`log(_T_RANGE[1])`, so a **fitted** temperature is legal by construction - and it
had **no bounds at all on the read path**. A temperature out of a *model file* is
the only way a value outside `_T_RANGE` reaches `calibrated()`, and this file has a
measured precedent for a file that verified its own digest and held garbage:
unbalanced weights made `to_dict`'s `round(v, 6)` write zeros, and the file then
predicted 0.3333 for every verdict on every input.

**Measured, feeding the reader what a file could hold, before the fix:**

| T | top probability | what it meant |
|---|---|---|
| 0.05, `_T_RANGE`'s floor | 0.9975 | legal |
| **1e-06** | **1.000000** | **saturated** |
| 20.0, `_T_RANGE`'s ceiling | 0.3378 | legal |
| 0 / `True` | 0.4260 | the identity, by luck of `or` |
| `"0.5"` | `TypeError` | **a crash** |

So a hand-edited or older file had two ways to be wrong and neither was caught.
The two this port is for:

- **Below the floor, the calibrated probability saturates to 1.0** - so
  `Report.honest()`'s `detected` gate and `recommend()` act on a certainty nothing
  earned, which is laya's #394 exactly.
- **A non-numeric temperature raises `TypeError`** out of the read path, where
  every other failure in this module falls back to identity. A file that cannot be
  parsed is not a model that is wrong, and must not take the outcome model down.

`True` and `0` keep returning the identity, for a stated reason now rather than by
the accident of `or` - a bool is truthy and zero is falsy, and both stop being safe
the moment a caller divides by a rounded value.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

from shani_chronoa import learning  # noqa: E402
from shani_chronoa.learning import calibrated  # noqa: E402

_LOGITS = [0.9, 0.4, 0.6]


class TestTheTemperatureIsBoundedOnTheReadPath:
    def test_a_temperature_below_the_floor_does_not_saturate(self):
        """**The defect, as measured.** 1e-06 gave a top probability of 1.000000 -
        a certainty on every input, which is what `Report.honest()`'s detected gate
        would act on.
        """
        assert max(calibrated(_LOGITS, 1e-06)) < 0.9999, (
            "a temperature below the fitter's floor still saturates to a "
            f"certainty: {max(calibrated(_LOGITS, 1e-06))}")

    def test_a_temperature_above_the_ceiling_is_clamped(self):
        assert max(calibrated(_LOGITS, 1e9)) == pytest.approx(
            max(calibrated(_LOGITS, learning._T_RANGE[1])))

    def test_a_non_numeric_temperature_is_the_identity_not_a_crash(self):
        """**The other defect, as measured:** `"0.5"` raised `TypeError` out of the
        read path, where every other failure in this module falls back to identity.
        """
        for value in ("0.5", None, [1.0], {"t": 1}, object()):
            got = calibrated(_LOGITS, value)
            assert got == pytest.approx(calibrated(_LOGITS, 1.0)), (
                f"{value!r} did not read as the identity: {got}")

    def test_nan_and_infinity_are_the_identity(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            assert calibrated(_LOGITS, value) == pytest.approx(
                calibrated(_LOGITS, 1.0)), f"{value} was divided by"

    def test_zero_and_true_still_read_as_the_identity(self):
        """They did before, by the accident of `or` - a bool is truthy and zero is
        falsy. Kept as the answer, for a stated reason now."""
        assert calibrated(_LOGITS, 0) == pytest.approx(calibrated(_LOGITS, 1.0))
        assert calibrated(_LOGITS, True) == pytest.approx(calibrated(_LOGITS, 1.0))

    def test_the_legal_range_is_unchanged(self):
        """The port clamps; it must not move a value already inside the bounds. A
        calibration that changes a legal fit is a change nobody asked for."""
        for t in (learning._T_RANGE[0], 0.5, 1.0, 2.0, learning._T_RANGE[1]):
            expected = [math.exp(z / t) for z in _LOGITS]
            total = sum(expected)
            assert calibrated(_LOGITS, t) == pytest.approx(
                [v / total for v in expected])


class TestTheShiftIsBoundedTheSameWay:
    def test_a_shift_above_the_bound_is_clamped(self):
        """**What the clamp guarantees, and what it does not.** `_SHIFT_MAX` is 8.0
        in logits, and clamping to it still saturates on a small spread - so the
        bound is a ceiling on the *term*, not on the confidence. Stated here
        because the first version of this test asserted a saturation that the
        clamp does not provide, and would have failed once the clamp was right.

        Laya's own wording for the same asymmetry: "The clamp bounds the damage; it
        does not calibrate anything."
        """
        # **-99 clamps to -MAX, not to +MAX.** The first version of this compared
        # against `[_SHIFT_MAX] * 3`, which is a different vector - the clamp is
        # element-wise, so the middle term keeps its sign.
        wanted = [learning._SHIFT_MAX, -learning._SHIFT_MAX, learning._SHIFT_MAX]
        huge = calibrated(_LOGITS, 1.0, [99.0, -99.0, 99.0])
        assert huge == pytest.approx(
            calibrated(_LOGITS, 1.0, wanted)), (
            "a shift beyond the bound was not clamped to it, term by term")
        assert huge != pytest.approx(calibrated(_LOGITS, 1.0)), (
            "the shift was dropped entirely rather than bounded")

    def test_a_non_numeric_shift_element_is_the_identity(self):
        for value in (["a", "b"], [1.0, "x", 0.0], None, [float("nan")]):
            assert calibrated(_LOGITS, 1.0, value) == pytest.approx(
                calibrated(_LOGITS, 1.0)), f"{value!r} was applied as a shift"

    def test_a_legal_shift_is_unchanged(self):
        shift = [0.1, -0.2, 0.3]
        expected = [math.exp(z + b) for z, b in zip(_LOGITS, shift)]
        total = sum(expected)
        assert calibrated(_LOGITS, 1.0, shift) == pytest.approx(
            [v / total for v in expected])


class TestOnePairOfBounds:
    """**Laya's rule, which is the point of the port.** The fitter's bounds and the
    reader's are the same constants, not a second pair - so a file can never hold a
    temperature the fit would refuse."""

    def test_the_reader_uses_the_fitters_own_constants(self):
        assert learning.clamp_temperature(0) == 1.0
        assert learning.clamp_temperature(1e-9) == learning._T_RANGE[0]
        assert learning.clamp_temperature(1e9) == learning._T_RANGE[1]
        assert learning.clamp_temperature(1.0) == 1.0

    def test_a_bool_is_not_a_number(self):
        """`True`/`False` are excluded explicitly, not by the luck of `or`."""
        assert learning._is_number(True) is False
        assert learning._is_number(False) is False
        assert learning._is_number(1.0) is True
        assert learning._is_number(3) is True
        assert learning._is_number("1.0") is False
        assert learning._is_number(None) is False


class TestTheFittedPathIsUnchanged:
    """The clamp is in the reader, and the fit must be identical to before it -
    measured NLL from a fitted model already in a committed file is the evidence
    this repository quotes, and moving it silently would be the failure."""

    def test_a_fitted_temperature_survives_the_clamp(self):
        for t in (learning._T_RANGE[0], 0.5, 1.0, 2.0, learning._T_RANGE[1]):
            assert learning.clamp_temperature(t) == t

    def test_fit_calibration_still_returns_identity_when_it_cannot(self):
        """The floor, which predates this pass and must not move.

        **The first witness could not reach it.** One example and one label is
        already refused by `len(set(labels)) < 2`, so removing the weight floor
        changed nothing that test measured. The floor needs a case that is
        multi-class and still too small - two labels with weights summing to 1.0,
        well under `_MIN_CALIBRATION`.
        """
        tiny = learning.fit_calibration([[0.9, 0.1], [0.2, 0.8]], [0, 1],
                                        weights=[0.5, 0.5])
        assert tiny["fitted"] is False, (
            "a fit ran on a total weight of 1.0, below the sample floor")
        assert tiny["temperature"] == 1.0
        assert tiny["shift"] == [0.0, 0.0]
