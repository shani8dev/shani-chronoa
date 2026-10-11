"""Text normalisation for the speech path.

The defect this module fixes is measured, not argued. `espeak-ng -X` on the
sentence a language model actually writes:

    It cost $5.00 at 42km/h, i.e. 3 items.
    -> Unpronouncable? 'km'
    -> It k'0st d'0l3 f'aIv pOInt z'i@roU z'i@roU at f'o@ti t'u: k,eI'Em sl'aS 'eItS

...which is "It cost dollar five point zero zero at forty two K-E-M slash H".
Nothing in this app normalised text for speech before, so that sentence is what
every image said out loud.

The tests here pin three different things, and the third is the one that would
otherwise be missed: what the normaliser must **not** change. A normaliser with no
negative tests grows until it rewrites the sentence it was meant to fix.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr", "lib", "shani-chronoa"))

from shani_chronoa.tts_text import normalize_for_speech  # noqa: E402


# --- the measured defect ---------------------------------------------------


@pytest.mark.parametrize("spoken,expected", [
    # The sentence that started the module. espeak's own words for this were
    # "forty two K E M slash H".
    ("It cost $5.00 at 42km/h, i.e. 3 items.",
     "It cost 5 dollars at 42 kilometres per hour, that is 3 items."),
    # A unit directly against a digit is the case `\b` cannot match, and the
    # one that matters most - so it is its own test rather than a footnote.
    ("Downloaded 8GB in 100ms.", "Downloaded 8 gigabytes in 100 milliseconds."),
    ("That is 5 km or 5km.", "That is 5 kilometres or 5 kilometres."),
    # A whole amount drops its zero cents; a real fraction names them.
    ("It cost $5.00.", "It cost 5 dollars."),
    ("It cost $0.99.", "It cost 99 cents."),
    ("It cost $4.50.", "It cost 4 dollars and 50 cents."),
    ("It cost 5 dollars already.", "It cost 5 dollars already."),
    # An initialism read as a word is the same failure as a unit read as letters.
    ("Use the API and the URL.", "Use the A P I and the U R L."),
])
def test_the_words_that_were_mangled(spoken, expected):
    out, _changes = normalize_for_speech(spoken)
    assert out == expected


def test_the_change_report_says_what_became_what():
    """The report has to name the replacement, not the pattern.

    A log line quoting a regex cannot be checked against the sentence that was
    spoken, and the first version printed a callable's repr instead - which is
    how this test exists.
    """
    _out, changes = normalize_for_speech("It cost $5.00 at 42km/h.")
    joined = "\n".join(changes)
    assert "'42km/h' -> '42 kilometres per hour'" in joined
    assert "'$5.00' -> '5 dollars'" in joined
    assert "function" not in joined, f"a repr leaked into the report: {joined}"
    assert "0x" not in joined, f"an address leaked into the report: {joined}"


# --- what must NOT change --------------------------------------------------


@pytest.mark.parametrize("untouched", [
    "Version 3.14 released in 2026.",
    "1st place, 2nd, 3rd.",
    "53,312 bytes and 2,048 kilobytes.",
    "It ran in 3.5 seconds.",
    "",
    "   ",
])
def test_numbers_that_already_read_correctly_are_left_alone(untouched):
    """Every one of these was measured through espeak-ng *before* this module.

    `3.14` reads "three point one four", `2026` reads as a year, ordinals and
    grouped digits are fine. Rewriting them would be change for its own sake,
    and a normaliser nobody can review is worse than the mangled `km` it fixed.
    """
    out, changes = normalize_for_speech(untouched)
    assert out == untouched
    assert changes == [], f"rewrote something that was already correct: {changes}"


def test_a_unit_inside_a_word_is_not_rewritten():
    """`km` in `kmod` is a kernel module, not a distance.

    The look-behind on a letter is what refuses it; a bare `\\b` or a bare
    substring would have rewritten it and produced "kilo metresod".
    """
    out, changes = normalize_for_speech("kmod and kmem and kmalloc")
    assert out == "kmod and kmem and kmalloc"
    assert changes == []


def test_normalising_twice_is_the_same_as_once():
    """Idempotence, because a reply is synthesised once but a sentence may be.

    If a second pass changed the text again the report would double-count and a
    caller that re-ran it would hear a different sentence.
    """
    once, first = normalize_for_speech("8GB in 100ms at 42km/h for $5.00.")
    twice, second = normalize_for_speech(once)
    assert twice == once
    assert second == []


def test_an_empty_reply_is_not_an_error():
    out, changes = normalize_for_speech("")
    assert out == ""
    assert changes == []


# --- a normaliser must never cost a reply ----------------------------------


def test_a_broken_normaliser_leaves_the_text_rather_than_the_silence(monkeypatch):
    """The failure mode that matters: a normaliser that raises.

    The call site in `tts.synthesize` catches it and speaks the raw text, because
    an engine that says "forty two K E M slash H" is better than no engine at
    all. This drives the real method with the normaliser forced to raise, so the
    guarantee is tested rather than described.
    """
    from shani_chronoa import tts as tts_mod

    def boom(_text):
        raise RuntimeError("normaliser exploded")

    monkeypatch.setattr(tts_mod, "normalize_for_speech", boom)

    class _Fake(tts_mod.PiperTTS):
        def _synthesize(self, text, output_file):
            # Record what the engine was actually handed.
            with open(output_file, "w") as fh:
                fh.write(text)
            return True

        def apply_timbre(self, _wav_path):
            return ""

    tab = _Fake()
    assert tab.synthesize("42km/h", "/tmp/opencode/tts-normalise-probe.wav")
    with open("/tmp/opencode/tts-normalise-probe.wav") as fh:
        assert fh.read() == "42km/h", "the raw text must reach the engine"
    os.path.exists("/tmp/opencode/tts-normalise-probe.wav") and os.unlink(
        "/tmp/opencode/tts-normalise-probe.wav")


def test_the_normal_text_reaches_the_engine_rewritten(monkeypatch):
    """The companion: with a working normaliser, the engine gets the rewrite.

    Without this the test above would pass against a call site that ignored the
    normaliser entirely - which is exactly the gap this module closed.
    """
    from shani_chronoa import tts as tts_mod

    class _Fake(tts_mod.PiperTTS):
        def _synthesize(self, text, output_file):
            with open(output_file, "w") as fh:
                fh.write(text)
            return True

        def apply_timbre(self, _wav_path):
            return ""

    tab = _Fake()
    assert tab.synthesize("42km/h", "/tmp/opencode/tts-normalise-probe.wav")
    with open("/tmp/opencode/tts-normalise-probe.wav") as fh:
        assert fh.read() == "42 kilometres per hour"
    os.path.exists("/tmp/opencode/tts-normalise-probe.wav") and os.unlink(
        "/tmp/opencode/tts-normalise-probe.wav")
