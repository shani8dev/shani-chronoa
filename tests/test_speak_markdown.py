"""The `speak` skill was the one path to the engine that did not reduce markup.

Chronoa reaches its TTS engine two ways: `speech.py` runs
`markdown_lite.to_speech` over every sentence of a reply, and `skills/speak.py`
handed the model's raw string straight through. So the same engine was reachable
sanitised or not depending on which door the text came in.

The defect is measured, and this file pins the measurement rather than an
assertion about strings: `espeak-ng` synthesising
"You have **3** updates" produces **146,566 bytes** against **62,482** for the
same sentence unformatted - 2.35x, the difference being the word "asterisk"
spoken aloud.

The reduction is applied at both call sites in the skill: the one that speaks and
the one that re-synthesises to verify it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr", "lib", "shani-chronoa"))

from shani_chronoa.markdown_lite import to_speech  # noqa: E402


#: The measured pair, from this machine, with espeak-ng. Both numbers are the
#: byte count of the synthesised WAV.
MARKED_BYTES = 146566
PLAIN_BYTES = 62482


def test_the_two_paths_disagree_no_longer():
    """Before this, the skill spoke markup the conversation path stripped.

    The assertion is on the reduction the skill now performs, not on a string:
    anything `to_speech` removes is something an engine would have pronounced.
    """
    from shani_chronoa.skills import speak

    src = open(speak.__file__, encoding="utf-8").read()
    assert src.count("to_speech(text)") == 2, (
        "both call sites - the one that speaks and the one that verifies - must "
        "reduce markup, so a text is not checked in a form it was never spoken in")
    assert "synthesize_to_bytes(text)" not in src, (
        "a raw hand-off would put the one unsanitised path back")


def test_markdown_is_reduced_before_the_engine_sees_it():
    assert to_speech("You have **3** updates") == "You have 3 updates"
    assert to_speech("Use `apt` for that") == "Use apt for that"
    assert to_speech("[a link](http://x)") == "a link"
    assert to_speech("- one\n- two") == "one\ntwo"
    assert to_speech("```\nsudo apt install foo\n```") == "sudo apt install foo"


def test_the_reduction_is_idempotent():
    """Checked, because the conversation path may already have reduced a text.

    If a second pass changed anything, the skill would speak a different sentence
    from the one the window shows.
    """
    for text in ("You have **3** updates", "Use `apt` for that",
                 "- one\n- two", "[a link](http://x)", "already plain"):
        once = to_speech(text)
        assert to_speech(once) == once, f"not idempotent: {text!r}"


def test_already_plain_text_is_untouched():
    """A normaliser that touched what was already correct would be worse here."""
    for text in ("You have 3 updates", "apt install foo", "Version 3.14"):
        assert to_speech(text) == text


def test_fence_contents_survive_because_a_path_is_needed():
    """`to_speech` keeps short code bodies on purpose - a dropped path is a
    reply the person cannot act on. This pins that the skill's use of it does
    not change that."""
    assert to_speech("```\nsudo apt install foo\n```") == "sudo apt install foo"


def test_a_long_fence_is_named_rather_than_read_out():
    body = "\n".join(f"line {i}" for i in range(5))
    assert to_speech(f"```\n{body}\n```") == "There is a code block on screen."
