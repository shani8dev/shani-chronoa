"""The loops a recogniser emits, which `clean_transcription` did not see.

Whisper's characteristic failure is not a different wrong sentence, it is the
right sentence repeated. Measured against the form it actually emits when the
audio trails off, the existing hallucination filter matched nothing:

    'thanks for watching'                    -> dropped        (the filter's case)
    ' '.join(['thanks for watching'] * 6)    -> 119 chars forwarded to the model

119 characters of a phrase the code already knew how to refuse, arriving at the
model as if a person had said it six times, emphatically. `collapse_repetitive_artifacts`
runs first so the loop reduces to the one phrase the filter already refuses.

**Two bugs in the first version of the fix**, both recorded here because both
produced a plausible-looking collapse that was wrong:

- **A multi-word phrase defeated both passes.** "thanks for watching" repeated
  has no two identical adjacent tokens, and its period in characters is the
  phrase *plus its separator* - twenty, not nineteen - so a sixfold repeat is
  119 characters and contains only five whole 20-character units. The
  character pass never reached its threshold. It works in tokens, on the
  smallest period that tiles the run, which removes the separator from the
  question.
- **Stopping at the threshold left most of the run behind.** Eight repeats of a
  four-character CJK unit collapsed to *three* copies, because the loop broke
  once `min_run` was met and then kept everything after that point. A run of
  forty is the same artefact as a run of six.

The threshold is six and not three because "no, no, no, no, no" is somebody
emphatic, and `test_rhetorical_repetition_survives` is the guard on the fix
being too eager - a collapse that eats real speech is a worse defect than the
loop it was written for.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr", "lib", "shani-chronoa"))

from shani_chronoa.stt import (  # noqa: E402
    REPETITION_RUN, clean_transcription, collapse_repetitive_artifacts)


# --- the defect, as measured -----------------------------------------------


def test_a_looped_hallucination_is_dropped_not_forwarded():
    """The measured defect: the same phrase, in the form whisper emits.

    The single form was already dropped. This asserts the looped form is too,
    which is the whole point - and asserts it through `clean_transcription`,
    not through the helper, because the helper alone would pass against a call
    site that never ran it.
    """
    looped = " ".join(["thanks for watching"] * 6)
    assert len(looped) == 119
    assert clean_transcription("thanks for watching") == "", (
        "the single form is what the filter was written for and must still drop")
    assert clean_transcription(looped) == "", (
        "119 characters of a refused phrase reached the model as one emphatic "
        "person; the collapse has to run before the filter for this to hold")


def test_a_long_loop_is_dropped_too():
    """Forty repetitions is the shape a trailing-off audio actually produces."""
    assert clean_transcription(" ".join(["thanks for watching"] * 40)) == ""


def test_real_speech_survives_and_only_the_loop_goes():
    """The half that matters: a collapse that eats real speech is worse.

    A person asking the time, followed by a recogniser loop, must keep the
    question. This is the assertion that stops the fix being over-eager, and it
    is why the threshold is a named constant rather than whatever passed.
    """
    heard = ("what time is it " + " ".join(["thanks for watching"] * 6))
    out = clean_transcription(heard)
    assert out.startswith("what time is it"), out
    assert out.count("thanks for watching") == 1, (
        f"the loop should reduce to the one phrase the filter then refuses, got {out!r}")


# --- the two shapes the passes exist for ------------------------------------


def test_a_multi_word_phrase_loop_collapses():
    """The case the character pass alone cannot see.

    No two adjacent tokens are identical, and the text's character period is the
    phrase plus its separator, so a sixfold repeat never contains six whole
    units of the naive width.
    """
    looped = " ".join(["screen lock"] * 7)
    assert collapse_repetitive_artifacts(looped) == "screen lock"


def test_a_single_word_loop_collapses_despite_punctuation():
    """Whisper punctuates a loop as it goes: "URL", "URL,", "URL." are one token."""
    assert collapse_repetitive_artifacts(" ".join(["URL,"] * 7)) == "URL,"
    assert collapse_repetitive_artifacts(" ".join(["URL"] * 8)) == "URL"


def test_a_loop_with_no_word_boundaries_collapses():
    """CJK: `split()` returns one element, so neither token pass can see a run."""
    assert collapse_repetitive_artifacts("谢谢观看" * 8) == "谢谢观看"


def test_the_whole_run_is_consumed_not_just_the_threshold():
    """The bug that left three copies of eight.

    Breaking out of the scan at `min_run` and then keeping the tail re-emits
    most of what was removed, which reads as a working collapse in a test that
    only looks for *a* difference.
    """
    out = collapse_repetitive_artifacts("谢谢观看" * 8)
    assert out.count("谢谢观看") == 1, out
    out = collapse_repetitive_artifacts(" ".join(["thanks for watching"] * 40))
    assert out == "thanks for watching"


# --- the guards against over-collapsing -------------------------------------


def test_rhetorical_repetition_survives():
    """"no, no, no, no, no" is somebody emphatic, not a recogniser loop.

    Below the threshold, and this is the assertion that fails if the fix is
    ever made eager enough to be wrong in the dangerous direction.
    """
    emphatic = "no, no, no, no, no"
    assert collapse_repetitive_artifacts(emphatic) == emphatic
    assert clean_transcription(emphatic) == emphatic
    assert REPETITION_RUN == 6, (
        "the threshold is a measured choice: five has to survive, the artefacts "
        "that matter are measured in the dozens")


def test_ordinary_prose_is_untouched():
    """Sentences that happen to repeat a word, and requests with repeated words."""
    for text in (
        "I need the volume up, not down, not up, but louder please",
        "set the volume to sixty percent",
        "run arping on wlan0 please",
        "he said he said he said nothing at all",
        "what is the weather like today and tomorrow",
    ):
        assert clean_transcription(text) == text, text


def test_empty_and_short_input_is_untouched():
    for text in ("", "   ", "hi", "a"):
        assert collapse_repetitive_artifacts(text) == text


def test_the_existing_behaviour_still_stands():
    """The annotation and hallucination filters this sits in front of."""
    for noise in ("[BLANK_AUDIO]", "[music]", "(applause)", "*sigh*"):
        assert clean_transcription(noise) == "", noise
    assert clean_transcription("Thanks for watching!") == ""
    assert clean_transcription("bye") == ""
    # Under two word-characters is not speech and never was. `"uh"` is *two*,
    # so it survives - I asserted otherwise first, and running it said so.
    assert clean_transcription("a") == ""
    assert clean_transcription("uh") == "uh"
