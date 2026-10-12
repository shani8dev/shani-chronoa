"""The tool selection discarded tools it had ranked first, and said nothing.

Four of the 220 scorable cases lost the correct tool **after** `ranked()` had
put it first, through two separate mechanisms. Both are here because the
symptom in each case is identical - a correct tool is simply absent from what is
sent - and they have nothing to do with each other. A fix for one would have
looked like a fix for the other.

**The controls matter more than the assertions.** Each half of each fix has a
case that must *not* change: a greeting must still get one tool, and "log in"
must still open a browser. Without those, a fix that simply stopped applying the
cues would pass every assertion below.
"""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

import pytest  # noqa: E402

from shani_chronoa import tools as T, tool_select as S  # noqa: E402

SCHEMAS = T.TOOLS


def sent(request):
    return [S._name(t) for t in S.select_tools(request, SCHEMAS)]


class TestAFalseBrowserCueWasSpendingTheWholeBudget:
    """`log ?in` had no word boundary, so it matched **"login"**.

    "Is fingerprint **login** set up on this machine?" therefore read as a
    request to act on a page. `browse` was added *and protected*, and it is 3496
    characters against a 1536-token budget - so protected CORE plus `browse`
    consumed 1395 of it, leaving 141 tokens, and `fingerprint_status` (200
    tokens) was dropped having been ranked first. One regex, and the cost was
    not an extra tool: it was the entire turn's tool budget.
    """

    def test_a_phrase_about_logging_in_is_not_a_page(self):
        assert "fingerprint_status" in sent(
            "Is fingerprint login set up on this machine?")

    @pytest.mark.parametrize("phrase", [
        "log in to my account", "sign in now", "check out the cart",
        "book a flight", "fill in the form",
    ])
    def test_the_cue_still_fires_on_an_actual_page_action(self, phrase):
        """The control. Drop the boundary and these stop matching too."""
        assert S.wants_a_browser(phrase), phrase

    @pytest.mark.parametrize("phrase", [
        "fingerprint login set up", "login_history query", "logins", "login",
    ])
    def test_login_is_not_log_in(self, phrase):
        assert not S.wants_a_browser(phrase), phrase


class TestTheConversationPathNoLongerSwallowsATask:
    """Over all 220 scorable cases exactly three turns took the conversation
    path, and **all three were misses** - each with the correct tool ranked
    first and each answered with a single `ask_user`. The cue cannot see that
    `explain_command` is precisely what "explain a command" asks for.
    """

    @pytest.mark.parametrize("turn,want", [
        ("Explain a command.", "explain_command"),
        ("What is plugged in, and Thunderbolt docks.", "usb_devices"),
        ("What is conflicting in a git repository, and how do I resolve it?",
         "resolve_conflict"),
    ])
    def test_a_strong_match_beats_the_conversation_cue(self, turn, want):
        # `turn`, not `request`: pytest reserves that name for a fixture, so the
        # first run of this file reported "1 error during collection" rather
        # than a red assertion - which is how a wrong parameter name can read
        # as a broken test.
        assert want in sent(turn), sent(turn)

    @pytest.mark.parametrize("greeting", [
        "thanks!", "hello", "good morning",
    ])
    def test_small_talk_still_gets_one_tool(self, greeting):
        """The control, and the failure that matters.

        The constant is a measurement with a 3.5-point margin, so being wrong in
        the safe direction - eight tools sent to "thanks!" again - is the
        failure worth catching rather than the other one.
        """
        assert sent(greeting) == ["ask_user"], (greeting, sent(greeting))


class TestTheThresholdIsMeasuredOnBothSides:
    def test_both_sides_of_the_constant_are_pinned(self):
        """The two ends, so the constant cannot move unnoticed in either
        direction. These are the five turns the value was chosen from."""
        below = S.ranked("good morning", SCHEMAS)      # 9.72, must stay a chat turn
        above = S.ranked("Explain a command.", SCHEMAS)  # 29.01, must yield
        assert below and above
        assert below[0][0] < S.CONVERSATION_YIELD < above[0][0], (
            below[0][0], S.CONVERSATION_YIELD, above[0][0])

    def test_the_margin_is_recorded_because_it_is_narrow(self):
        """A 3.5-point gap is a fact about this corpus, not a property.

        If a future case lands between the two, this fails and says so, rather
        than the constant quietly becoming wrong.
        """
        below = S.ranked("good morning", SCHEMAS)[0][0]
        assert S.CONVERSATION_YIELD - below < 5.0, (
            "the margin has grown - re-derive the constant rather than trusting it")