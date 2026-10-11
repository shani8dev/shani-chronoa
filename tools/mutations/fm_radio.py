"""Mutation set for the FM stereo decoder — the four defects that were real.

Rebuilt on 2026-10-11 from the committed tests and code, after the original
`/tmp` harness was lost.

Each mutation restores one of the four shipped defects `AUDIT-HISTORY.md`
measured, plus the two filter bugs in `fmdsp.py` the same pass found. The
discriminator's phase wrap is the one that matters most: `atan2`'s branch cut
injects a spurious ±2π step whenever the accumulated phase crosses ±π.
"""

TEST_FILE = "tests/test_fm_radio_stereo.py"

_FM = "usr/lib/shani-chronoa/shani_chronoa/skills/fm_radio.py"
_FMDSP = "usr/lib/shani-chronoa/shani_chronoa/fmdsp.py"

MUTATIONS = [
    # -- the discriminator's phase wrap ----------------------------------------
    {
        "file": _FM,
        "old": "        step = phase - previous\n"
               "        if step > math.pi:\n"
               "            step -= _TWO_PI\n"
               "        elif step < -math.pi:\n"
               "            step += _TWO_PI\n"
               "        frequency.append(step * scale)\n"
               "        previous = phase\n",
        "new": "        step = phase - previous\n"
               "        frequency.append(step * scale)\n"
               "        previous = phase\n",
        "label": "the discriminator has no phase wrap (the branch cut clicks)",
        # **The witnesses are the stereo-separation tests, not the discriminator's
        # own.** The first version named the two tests that measure the phase step
        # directly; running it showed the wrap's real damage is downstream, in the
        # channels it stops separating.
        "expect": ["test_a_left_only_tone_does_not_reach_the_right_channel",
                   "test_a_right_only_tone_does_not_reach_the_left_channel",
                   "test_hard_panned_channels_stay_separate"],
    },
    {
        "file": _FM,
        "old": "        if step > math.pi:\n"
               "            step -= _TWO_PI\n"
               "        elif step < -math.pi:\n"
               "            step += _TWO_PI\n",
        "new": "        if step > _TWO_PI:\n"
               "            step -= _TWO_PI\n"
               "        elif step < -_TWO_PI:\n"
               "            step += _TWO_PI\n",
        "label": "the wrap fires at 2π instead of π, so the half that matters is missed",
        "expect": ["test_a_left_only_tone_does_not_reach_the_right_channel",
                   "test_hard_panned_channels_stay_separate"],
    },

    # -- the de-matrix ------------------------------------------------------------
    {
        "file": _FM,
        # **The shipped defect was a `0.5` on the difference, not a 0.75/0.25
        # pair.** The repo's own note says the two 0.5s *compounded* to 0.25,
        # which is what `m + 0.5*d` produces - the mutation restores it.
        "old": "        pairs = [(m + d, m - d) for m, d in zip(mono, difference)]\n",
        "new": "        pairs = [(m + 0.5 * d, m - 0.5 * d) for m, d in zip(mono, difference)]\n",
        "label": "the de-matrix compounds two 0.5s into a 0.75",
        "expect": ["test_a_left_only_tone_does_not_reach_the_right_channel",
                   "test_a_right_only_tone_does_not_reach_the_left_channel",
                   "test_hard_panned_channels_stay_separate",
                   "test_the_coefficient_is_one_and_the_channels_rebuild"],
    },

    # -- the de-emphasis pole ------------------------------------------------------
    {
        "file": _FM,
        "old": "    return math.exp(-1.0 / (tau * fs))\n",
        "new": "    return 1.0 / (1.0 + 2.0 * math.pi * tau * fs)\n",
        "label": "the pole is the bilinear-looking form, whose corner drifts with the rate",
        "expect": ["test_the_de_emphasis_corner_is_the_time_constant_not_the_rate",
                   "test_the_de_emphasis_the_receiver_actually_applies_is_flat",
                   "test_the_receivers_de_emphasis_corner_is_the_time_constant"],
    },
    {
        "file": _FM,
        "old": "    return math.exp(-1.0 / (tau * fs))\n",
        "new": "    return 1.0 - 1.0 / (tau * fs)\n",
        "label": "the pole omits the exponential entirely",
        "expect": ["test_the_de_emphasis_corner_is_the_time_constant_not_the_rate"],
    },

    # -- the de-emphasis recursion ---------------------------------------------------
    {
        "file": _FM,
        # **The shipped defect is a factoring, not a dropped coefficient.**
        # `a*(state + (1-a)*value)` distributes to `a*state + a*(1-a)*value`, and
        # the second coefficient carries an extra `a` - a DC gain of `a`.
        "old": "        state = a * state + (1.0 - a) * value\n"
               "        out.append(state)\n",
        "new": "        state = a * (state + (1.0 - a) * value)\n"
               "        out.append(state)\n",
        "label": "the DC gain is `a` rather than 1, so every signal is attenuated",
        "expect": ["test_pre_emphasis_then_de_emphasis_is_the_identity",
                   "test_the_round_trip_is_flat_across_the_audio_band"],
    },

    # -- the pilot --------------------------------------------------------------------
    # **The threshold is a strength floor, not a margin.** The first version aimed
    # at an `if margin > 1.0` line that does not exist; the real gate is
    # `strength < 0.02`, which is what stops silence and noise from locking.
    {
        "file": _FM,
        "old": "    if strength < 0.02:\n        return [], measured_hz, strength\n",
        "new": "",
        "label": "the pilot has no strength floor, so noise locks",
        "expect": ["test_noise_does_not_lock",
                   "test_silence_does_not_lock",
                   "test_a_nominal_pilot_locks_with_real_margin"],
    },
    {
        "file": _FM,
        "old": "    if max((abs(v) for v in band), default=0.0) < 1e-9:\n"
               "        return [], 0.0, 0.0\n",
        "new": "",
        "label": "the explicit silence guard is removed (the strength floor covers it)",
        "expect": ["test_silence_does_not_lock"],
        # **Equivalent.** With the guard gone, `measure_tone` on an all-zero band
        # returns a strength of 0, and the `strength < 0.02` floor below refuses
        # it anyway - so no input distinguishes the two paths. The comment says the
        # first version reported a pilot at 16.150 kHz with strength 0; the floor
        # is what makes that unreachable now, and this mutation is how we know the
        # guard is redundant rather than load-bearing.
        "equivalent": True,
        "why": "an all-zero band measures a strength of 0, which the strength floor "
               "refuses before the guard's own case is reached",
    },

    # -- the two fmdsp filters ----------------------------------------------------------
    # **The filter is a Butterworth second-order-section set, not a windowed sinc,
    # and the worst defect this module ever shipped was a missing zero at
    # Nyquist.** The poles were right, so the response looked correct at and below
    # the cutoff and only misbehaved far above it: 34,837x too high at 0.99 fs.
    # The comment above that branch stated the *opposite* of the true principle,
    # which is why it survived - so the mutation restores exactly that.
    {
        "file": _FMDSP,
        "old": "        poles = _lowpass_poles(order, float(cutoff), fs)\n"
               "        with_zeros = [[1.0, 2.0, 1.0, 1.0, s[4], s[5]]\n"
               "                      for s in _sections_from_poles(poles)]\n",
        "new": "        poles = _lowpass_poles(order, float(cutoff), fs)\n"
               "        with_zeros = [[1.0, 0.0, 0.0, 1.0, s[4], s[5]]\n"
               "                      for s in _sections_from_poles(poles)]\n",
        "label": "the lowpass has no zero at Nyquist (34,837x too high at 0.99 fs)",
        "expect": ["test_the_round_trip_is_flat_across_the_audio_band",
                   "test_the_de_emphasis_the_receiver_actually_applies_is_flat",
                   "test_hard_panned_channels_stay_separate"],
    },
    {
        "file": _FMDSP,
        "old": "        return _normalise(with_zeros, \"low\", fs)\n",
        "new": "        return with_zeros\n",
        "label": "the sections are not normalised to unity DC gain",
        "expect": ["test_a_left_only_tone_does_not_reach_the_right_channel",
                   "test_hard_panned_channels_stay_separate",
                   "test_noise_does_not_lock"],
    },

    # -- pre-emphasis --------------------------------------------------------------------
    {
        "file": _FM,
        # **The shipped pre-emphasis is feedforward and exact.** The obvious
        # "invert the difference equation" is an IIR with pole 1/(1-a) > 1 that
        # diverges, so the mutation feeds the output back instead.
        "old": "        out.append((value - a * previous) / gain)\n"
               "        previous = value\n",
        "new": "        out.append((value - a * previous))\n"
               "        previous = out[-1]\n",
        "label": "pre-emphasis feeds its own output back, so the pair diverges",
        "expect": ["test_pre_emphasis_rises_with_frequency",
                   "test_pre_emphasis_then_de_emphasis_is_the_identity"],
    },
]
