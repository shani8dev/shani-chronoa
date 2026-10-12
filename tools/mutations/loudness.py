"""Mutation set for the loudness pass in `PiperTTS`.

Measured 2026-10-12 through this repo's own `cache/data-home`, one sentence per
engine via `synthesize_to_bytes`:

    espeak-ng  -19.89 dBFS RMS     piper  -18.86     kokoro  -24.69

A 5.83 dB spread against a just-noticeable-difference of about 1 dB, and `piper`
arriving at -0.00 dBFS peak - full scale, no headroom for a style's EQ to lift.
The pass brings all three to within 0.10 dB of the target.

These mutations are the four ways a normaliser stops normalising: never firing,
firing without a ceiling, applying the ceiling to a cut (which the first version
did, and which showed up as `piper` landing 1.9 dB under target for no reason the
measurement explained), and claiming a change it did not make.
"""

TEST_FILE = "tests/test_voice_loop_live.py"

_TTS = "usr/lib/shani-chronoa/shani_chronoa/tts.py"

MUTATIONS = [
    {
        "file": _TTS,
        "old": "            if abs(gain) < LOUDNESS_TOLERANCE_DB:\n                return \"\"\n",
        "new": "            if True:\n                return \"\"\n",
        # The pass never applies anything. That is the pre-fix state, reached
        # through the code rather than through an absent call site - and worse than
        # nothing, because the constant and the call now read as a working guard.
        "label": "the loudness pass never applies anything",
        "expect": ["test_the_pass_moves_a_file_towards_the_target_never_past_it",
                   "test_every_engine_lands_on_the_target"],
    },
    {
        "file": _TTS,
        "old": "            if gain > 0:\n                allowed = PEAK_CEILING_DBFS - peak\n                if gain > allowed:\n                    gain = allowed\n",
        "new": "",
        # The ceiling gone: a quiet file is lifted until it clips. Clipping is
        # audible on speech in a way a level error is not, which is why the ceiling
        # exists at all.
        "label": "the peak ceiling is removed",
        "expect": ["test_a_lift_stops_at_the_peak_ceiling"],
    },
    {
        "file": _TTS,
        "old": "            if gain > 0:\n                allowed = PEAK_CEILING_DBFS - peak\n                if gain > allowed:\n                    gain = allowed\n",
        "new": "            allowed = PEAK_CEILING_DBFS - peak\n            if gain > allowed:\n                gain = allowed\n",
        # The ceiling applied to a **cut** as well. A cut never raises a peak, so
        # this holds an already-loud engine further below target than the numbers
        # ask for - which is what the first version did, measured as `piper` landing
        # 1.9 dB under target with nothing in the measurement to explain it.
        "label": "the ceiling also limits a cut",
        "expect": ["test_a_cut_is_not_held_back_by_the_ceiling",
                   "test_the_pass_moves_a_file_towards_the_target_never_past_it"],
    },
    {
        "file": _TTS,
        "old": '            applied = subprocess.run(["sox", wav_path, staged, "gain", f"{gain:+.2f}"],\n                                     capture_output=True, text=True, timeout=30)\n            if applied.returncode != 0 or not os.path.exists(staged):\n                return "sox could not apply the gain"\n            os.replace(staged, wav_path)\n            return ""\n',
        "new": '            applied = subprocess.run(["sox", wav_path, staged, "gain", f"{gain:+.2f}"],\n                                     capture_output=True, text=True, timeout=30)\n            if applied.returncode != 0 or not os.path.exists(staged):\n                return "sox could not apply the gain"\n            os.unlink(staged)\n            return ""\n',
        # The rewritten file is thrown away and the original kept. Every level
        # assertion passes on the numbers the engine produced, and no caller can
        # tell, because the return is still the success one - a normaliser that
        # reports success without changing anything.
        "label": "the normalised file is discarded and the original kept",
        "expect": ["test_the_pass_moves_a_file_towards_the_target_never_past_it",
                   "test_every_engine_lands_on_the_target"],
    },
    {
        "file": _TTS,
        "old": "        except Exception as exc:  # noqa: BLE001 - a normaliser must not cost a reply\n            return f\"loudness not normalised: {type(exc).__name__}: {exc}\"\n",
        "new": "        except Exception as exc:  # noqa: BLE001\n            return \"\"\n",
        # A failure reported as success. The file is left as the engine wrote it,
        # which is the right outcome, but the caller is told the pass ran - so a
        # silent normaliser that looks like a working one.
        "label": "a failure is reported as success",
        "expect": ["test_a_file_that_cannot_be_measured_returns_a_reason"],
    },
]
