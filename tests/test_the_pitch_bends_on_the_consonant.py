"""The rule "bend on the consonant, not the vowel" was written down and not used.

`Syllable.onset` carries the reasoning, and its docstring is specific:

> When the pitch for this syllable should have arrived. The *start* of the
> syllable, not its vowel. Real singing moves the pitch on the consonant going
> into the note, so bending to the target at the vowel would sound like a late
> slide into every single note.

**It had no reader anywhere in the tree.** Not one `.onset` in `usr/` or `tests/`.
And the one line where the invariant actually applies - `song_plan`, which decides
when each pitch shift starts - read `syllable.start` directly, so the property was
a redundant alias for the field beside it, carrying an explanation of a decision
nobody was making.

`song_plan` reads `syllable.onset` now. **The plan is byte-identical**, because the
property returns `self.start`; what changed is that the reasoning lives where the
scheduling happens, so the next edit to that line inherits it.

## This one is documentary, and the test says so

Unlike the `fm_radio` decode fix, nothing about the output changed. A test that
asserted different numbers would be asserting a change that is not there. So this
asserts **reachability** - that the property is read where the invariant applies -
and separately records that the two values coincide today.

`sing_effects` gets the same treatment for the opposite reason: its docstring said
*"Kept as the old name so callers keep working"*, and it has **no callers**. The
wording implied a live API surface; it now says what actually holds it up.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import prosody as P  # noqa: E402

SOURCE = ROOT / "usr/lib/shani-chronoa/shani_chronoa/prosody.py"


def _reads_onset(function_name):
    """Does `function_name` read `.onset` on anything? By AST, not by substring."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr == "onset":
                    return True
    return False


def test_the_scheduling_code_reads_the_onset():
    """**The defect.** The property explaining the rule had no reader."""
    assert _reads_onset("song_plan"), (
        "song_plan schedules pitch shifts from `syllable.start` again, so "
        "`Syllable.onset` is unreached and its explanation of *why on the "
        "consonant* is attached to nothing")


def test_onset_is_the_syllable_start_not_its_vowel():
    syllable = P.Syllable(index=0, start=0.40, end=0.95, tokens=(), text="ka")

    assert syllable.onset == 0.40
    assert syllable.onset == syllable.start, (
        "onset and start now differ, which would mean the property and the field "
        "have diverged - every caller reading one got the other's value")


def test_the_plan_is_unchanged_by_wiring_it():
    """**Explicitly not a behaviour change.** The property returns `self.start`,
    so this pins that reading the property is equivalent - and would fail loudly
    if a future edit to one of them did not keep it that way."""
    syllables = [P.Syllable(index=0, start=0.0, end=0.4, tokens=(), text="ka"),
                 P.Syllable(index=1, start=0.4, end=0.9, tokens=(), text="to"),
                 P.Syllable(index=2, start=0.9, end=1.2, tokens=(), text="ri")]
    melody = P.Melody(semitones=(0, 2, 4))

    plan = P.song_plan(syllables, melody)

    end_of_audio = syllables[-1].end
    expected, by_hand = [], []
    for syllable, target in zip(syllables, melody.semitones):
        lead = 0.02 if not expected else 0.0
        start = max(0.0, syllable.start - lead)
        stop = min(end_of_audio, syllable.end)
        if stop - start < 0.01:
            continue
        expected.append((start, stop - start, target))

    got = [(s.start, s.duration, s.semitones) for s in plan]

    assert got == expected, f"{got} != {expected}"
    assert by_hand == []
    assert plan, "song_plan produced nothing to compare"


def test_sing_effects_no_longer_claims_callers_it_does_not_have():
    """The other half: a docstring that implied a live API surface."""
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("def sing_effects(")
    doc = text[start:text.index('"""', text.index('"""', start) + 3)]

    assert "no callers" in doc, (
        "sing_effects' docstring implies callers keep working, but there are "
        "none anywhere in the tree")
    # **`apply_song`, not `song_plan`.** The docstring says what *runs* the plan
    # it returns; the alias *forwards to* `song_plan`. Asserting `song_plan` here
    # failed against a correct docstring, and would have been testing my reading of
    # the code rather than the code.
    assert "apply_song" in doc, (
        "the alias no longer says what runs the slices it returns")
    # **Not with an empty list.** `song_plan` refuses that on purpose -
    # `ValueError: no syllables: the alignment found no vowel to sing` - so the
    # first version of this assertion passed empty lists and raised.
    syllables = [P.Syllable(index=0, start=0.0, end=0.5, tokens=(), text="ka")]
    melody = P.Melody(semitones=(0,))

    assert P.sing_effects(syllables, melody) == P.song_plan(syllables, melody), (
        "the alias must still forward")
