"""The empty state offers a voice and a routine, not only the battery.

**Six chips about the battery, the clock and the screen** were the whole of
the empty state - and the two things the assistant does that no other tool
does, *speaking* and *running a saved request*, were not among them. A
newcomer reading them would not know either was possible.

Both are gated on the live registry like every other chip: a build without
the skill does not offer the prompt, because a chip that offers something
the machine cannot do is a broken promise.
"""

from __future__ import annotations

import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import capabilities, tools  # noqa: E402

VOICE = "What can be heard around the computer right now?"
ROUTINE = "What routines have I saved?"


def _suggestions():
    return capabilities.startup_suggestions(
        capabilities.find_capabilities(tools.TOOLS))


def test_the_empty_state_offers_a_voice():
    """**One that shows Chronoa can hear**, which is the other half of
    talking to it."""
    said = _suggestions()
    assert VOICE in said, said


def test_the_empty_state_offers_a_routine():
    """**One that shows Chronoa can do something on its own later** - a saved
    request, which is the one capability nothing else on screen hints at."""
    said = _suggestions()
    assert ROUTINE in said, said


def test_both_have_a_name_short_enough_for_a_chip():
    """**A chip is one word.** A prompt with no short name falls back to
    the whole question, which is the thing the table exists to avoid."""
    for prompt in (VOICE, ROUTINE):
        word = capabilities.SUGGESTION_WORDS.get(prompt)
        assert word, f"{prompt!r} has no short name"
        assert len(word) <= 16, f"{word!r} is too long for a chip"


def test_the_short_names_are_the_ones_on_screen():
    """**The chip shows the name; clicking sends the question.** A short
    name that is not the one the chip carries would be two vocabularies."""
    assert capabilities.SUGGESTION_WORDS[VOICE] == "Listen"
    assert capabilities.SUGGESTION_WORDS[ROUTINE] == "My routines"


def test_a_build_without_the_skill_does_not_offer_the_prompt():
    """**A chip that offers something the machine cannot do is a broken
    promise**, so the gate is on the registry rather than on the table."""
    class _Capability:
        def __init__(self, example):
            self.example = example

    only_battery = [_Capability("How much battery is left?")]
    said = capabilities.startup_suggestions(only_battery)
    assert VOICE not in said, said
    assert ROUTINE not in said, said
