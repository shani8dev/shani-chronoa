"""Mutation set for the temperature the reader divides by.

`harness-study/laya` (the decision model) states the rule for exactly this, in
`laya/calibrate.py`'s docstring:

> Fitted temperatures go through `clamp_temperature` and therefore `[TEMP_MIN,
> TEMP_MAX]` from `laya.common`, **the same guard checkpoint load uses.** There is
> no second pair of bounds in this module.

Chronoa's `fit_calibration` already searches `log(_T_RANGE[0])` to
`log(_T_RANGE[1])`, so a **fitted** temperature was legal by construction. The
read path had **no bounds at all**, and a temperature out of a *model file* is the
only way one outside the range reaches `calibrated()`. This file has a measured
precedent for a digest-valid file holding garbage: unbalanced weights made
`to_dict`'s `round(v, 6)` write zeros, and the file then predicted 0.3333 for
every verdict.

Measured, feeding the reader what a file could hold:

| T | before | after |
|---|---|---|
| 1e-06 | **1.000000** (saturated) | 0.997482 |
| `"0.5"` | **TypeError** | the identity |
| 0 | the identity, by luck of `or` | the identity, by rule |
| `True` | the identity | the identity |

Two design calls are pinned by their own mutations, because both are ways the
naive fix is worse than the bug:

- **0 is the identity, not `_T_RANGE[0]`.** Clamping 0 to the fitter's floor makes
  the sharpest legal temperature out of a value that means "rounded away" - a file
  that stored zeros would go from predicting 0.3333 to predicting ~0.997, which is
  a certainty instead of a flat model.
- **The shift's clamp bounds the term, not the confidence.** `_SHIFT_MAX` is 8.0
  in logits and a spread of 8 still saturates on small logits. Laya's own wording:
  "The clamp bounds the damage; it does not calibrate anything."
"""

TEST_FILE = "tests/test_temperature_bounds.py"

_L = "usr/lib/shani-chronoa/shani_chronoa/learning.py"

MUTATIONS = [
    # -- the clamp exists on the read path at all ---------------------------
    {
        "file": _L,
        "old": "    t = clamp_temperature(temperature)\n"
               "    bounded = clamp_shift(shift) if shift is not None else None\n"
               "    s = bounded if bounded else [0.0] * len(logits)\n",
        "new": "    t = temperature or 1.0\n"
               "    s = shift or [0.0] * len(logits)\n",
        "label": "**the finding, restored**: the reader divides by whatever the "
                 "file holds, unbounded",
        # This is the pre-port behaviour: 1e-06 saturated to 1.000000 and "0.5"
        # raised TypeError.
        "expect": ["test_a_temperature_below_the_floor_does_not_saturate",
                   "test_a_non_numeric_temperature_is_the_identity_not_a_crash"],
    },

    # -- 1. saturation below the floor --------------------------------------
    {
        "file": _L,
        "old": "    if t == 0.0:\n        return 1.0\n    return max(lo, min(hi, t))\n",
        "new": "    return max(lo, min(hi, t))\n",
        "label": "**the regression the clamp would introduce**: zero clamps to "
                 "the fitter's floor, the sharpest legal temperature",
        # Measured consequence: the file this repo once had, holding zeros, would
        # go from predicting 0.3333 on everything to predicting ~0.997.
        "expect": ["test_zero_and_true_still_read_as_the_identity"],
    },
    {
        "file": _L,
        "old": "    return max(lo, min(hi, t))\n",
        "new": "    return t\n",
        "label": "the clamp is gone entirely, so the fitter's bounds are not "
                 "shared with the reader",
        "expect": ["test_a_temperature_below_the_floor_does_not_saturate",
                   "test_a_temperature_above_the_ceiling_is_clamped"],
    },

    # -- 2. a non-number is not a crash -------------------------------------
    {
        "file": _L,
        "old": "    return isinstance(value, (int, float)) and not isinstance(value, bool)\n",
        "new": "    return isinstance(value, (int, float))\n",
        "label": "a bool passes as a number again, so `True` has a numeric type",
        # **The first version mutated the call site, which is inert:** with
        # `isinstance(value, (int, float))` a bool reaches `float(True)` = 1.0,
        # which is the same answer `_is_number` returns. The witness is on
        # `_is_number` itself, so that is where the mutation has to be.
        "expect": ["test_a_bool_is_not_a_number"],
    },
    {
        "file": _L,
        # Re-anchored 2026-10-12: the inline `t != t or t in (float("inf"), ...)`
        # check was consolidated onto `sysfs.finite()` (the shared reader this repo
        # already records extracting), so the old anchor matched nothing. The guard
        # is the same one and this mutation removes it.
        "old": "    if not sysfs.finite(t):\n        return 1.0\n",
        "new": "",
        "label": "NaN and infinity are divided by, so a file holding either "
                 "returns NaN on every probability",
        "expect": ["test_nan_and_infinity_are_the_identity"],
    },

    # -- the shift, element-wise --------------------------------------------
    {
        "file": _L,
        "old": "        out.append(max(-_SHIFT_MAX, min(_SHIFT_MAX, term)))\n",
        "new": "        out.append(term)\n",
        "label": "the shift is not clamped, so a file's shift of 99 is applied "
                 "as written",
        "expect": ["test_a_shift_above_the_bound_is_clamped"],
    },
    {
        "file": _L,
        "old": "        if not _is_number(term):\n            return []\n",
        "new": "",
        "label": "the shift's numeric guard is gone, leaving only the "
                 "try/except below it",
        # **Equivalent, and measured rather than assumed.** `float("a")` raises
        # ValueError, which the very next `except` catches and answers `[]` - so
        # removing the guard changes no observable. It stays in the set so the
        # day the except narrows, this mutant is the one that catches it.
        "expect": ["test_a_non_numeric_shift_element_is_the_identity"],
        "equivalent": True,
        "why": "float() on a non-numeric raises ValueError, which the adjacent "
               "except already catches and answers the same []",
    },

    # -- one pair of bounds, and the fit is unchanged ------------------------
    {
        "file": _L,
        "old": "    if not logits or sum(w) < _MIN_CALIBRATION or len(set(labels)) < 2:\n",
        "new": "    if not logits or len(set(labels)) < 2:\n",
        "label": "the sample floor is gone, so a fit runs on too few examples",
        "expect": ["test_fit_calibration_still_returns_identity_when_it_cannot"],
    },
]
