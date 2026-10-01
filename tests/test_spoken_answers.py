"""Answering Chronoa's questions out loud.

The hint under every question says "just say your answer", and before this a
spoken answer opened a new turn while the tool sat blocked waiting for it -
and even routed, "Allow this once." (whisper's full stop) would have been
read as a refusal by permissions.parse_reply, which compares exactly.
"""

from unittest.mock import MagicMock

import pytest

from shani_chronoa import permissions
from shani_chronoa.gui import match_spoken_option

PERM = [permissions.ALLOW_ONCE_CHOICE, permissions.ALLOW_SESSION_CHOICE, permissions.DENY_CHOICE]


class TestMatchSpokenOption:
    @pytest.mark.parametrize("said, chosen", [
        ("Allow this once.", permissions.ALLOW_ONCE_CHOICE),
        ("allow this once, please", permissions.ALLOW_ONCE_CHOICE),
        ("Allow for this session!", permissions.ALLOW_SESSION_CHOICE),
        ("No, don't allow.", permissions.DENY_CHOICE),
        ("no don’t allow", permissions.DENY_CHOICE),   # a curly apostrophe
    ])
    def test_an_option_said_is_that_option(self, said, chosen):
        assert match_spoken_option(said, PERM) == chosen

    @pytest.mark.parametrize("said", ["allow", "yes", "sure, go ahead", "allow this", "", "once"])
    def test_anything_less_than_a_whole_option_is_not_an_allow(self, said):
        got = match_spoken_option(said, PERM)
        assert got not in (permissions.ALLOW_ONCE_CHOICE, permissions.ALLOW_SESSION_CHOICE)
        assert permissions.parse_reply(got).reply == permissions.Decision.DENY_SESSION

    def test_the_mapped_answer_is_what_permissions_reads_as_allow(self):
        got = match_spoken_option("Allow this once.", PERM)
        assert permissions.parse_reply(got).reply == permissions.Decision.ALLOW_ONCE

    def test_a_longer_option_wins_over_the_shorter_one_it_begins_with(self):
        assert match_spoken_option("red wine please", ["Red", "Red wine"]) == "Red wine"


class TestTheTranscriptAnswersAnOpenQuestion:
    def test_a_spoken_answer_goes_to_the_question_not_a_new_turn(self, stubbed_app):
        stubbed_app.window = MagicMock()
        stubbed_app.window.answer_pending_question.return_value = True
        stubbed_app._submit = MagicMock()
        stubbed_app._on_transcribed("Allow this once.")
        stubbed_app.window.answer_pending_question.assert_called_once_with("Allow this once.")
        stubbed_app._submit.assert_not_called()

    def test_without_a_question_it_is_a_new_turn(self, stubbed_app):
        stubbed_app.window = MagicMock()
        stubbed_app.window.answer_pending_question.return_value = False
        stubbed_app.window.take_attachments.return_value = ([], "")
        stubbed_app._submit = MagicMock()
        stubbed_app._on_transcribed("what is two plus two")
        stubbed_app._submit.assert_called_once_with("what is two plus two")
        assert stubbed_app._voice_turn is True
