"""The status line names who answered, not who could have.

The window says two things about the model: a **state** word ("Ready") under
the orb, and a **detail** line under it that says where answers come from.
The detail line is the one a person reads to answer "is my prompt leaving this
machine?" - and it used to print the chain's *membership*:

    Answering from the cloud: default, kilo-auto/free, cohere/north-mini-code

**That is `CloudLLMChain.model`, which is every backend's model joined** - a
list of what *could* answer, printed as if it were what did. It is also three
provider ids and two model names in one breath, on the line whose whole job is
to be readable.

The chain records who did answer, in `last_provider` ("which provider
answered the last call, by display name"). So the line names that, and when
nothing has answered yet it says so rather than listing the chain.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

from shani_chronoa import cloud_llm  # noqa: E402
from shani_chronoa.app.brain import BrainMixin  # noqa: E402


def _chain(model: str, provider: str) -> object:
    """A `CloudLLMChain` in the shape the status line reads.

    Built with `__new__` because the real constructor wants providers and API
    keys, and the status line reads three attributes and asks one question.
    """
    chain = cloud_llm.CloudLLMChain.__new__(cloud_llm.CloudLLMChain)
    chain.model = model
    chain.last_provider = provider
    chain.is_available = lambda: True
    return chain


def _status(llm) -> str:
    stub = types.SimpleNamespace(llm=llm, privacy=types.SimpleNamespace())
    return BrainMixin._llm_status_text(stub)


class TestTheCloudLineNamesTheProviderThatAnswered:
    def test_the_provider_that_answered_is_named(self):
        said = _status(_chain("default, kilo-auto/free, cohere/north-mini-code",
                              "Kilo Gateway"))
        assert "Kilo Gateway" in said, said

    def test_the_chain_membership_is_not_printed(self):
        """**The defect, in the shape it showed up.**

        Every backend's model joined - the chain's *membership* - printed as
        if it were the answer.
        """
        said = _status(_chain("default, kilo-auto/free, cohere/north-mini-code",
                              "Kilo Gateway"))
        assert "kilo-auto/free" not in said, said
        assert "cohere/north-mini-code" not in said, said

    def test_before_anything_answered_it_says_so_rather_than_listing(self):
        """No provider has answered yet, so there is no provider to name.

        The list of what *could* answer is not a worse answer than nothing -
        it is a different and more confusing one.
        """
        said = _status(_chain("default, kilo-auto/free, cohere/north-mini-code",
                              ""))
        assert said == "Answering from the cloud", said
        assert "kilo-auto/free" not in said, said


class TestTheOtherLinesAreUnchanged:
    """The local case already said the right thing; this pins it."""

    def test_a_local_model_names_itself(self):
        class _Local:
            model = "qwen3-1.7b"
            local = True

            def is_available(self):
                return True

        said = _status(_Local())
        assert said == "Answering on this computer: qwen3-1.7b", said

    def test_no_model_says_so(self):
        class _None:
            model = ""

            def is_available(self):
                return False

        said = _status(_None())
        assert said == "No model is answering yet", said
