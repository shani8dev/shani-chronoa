"""Nothing showed what the model was offered, or why it picked.

Measured: **35% of the tools actually sent to the model share no word with the
request**, and `ranked()` reorders through learned multipliers in silence. A
person who sees a card saying `get_datetime` has no way to ask whether it was
chosen because the request said "time" or because it is always in the list.

This file holds three claims, in the order they can be checked:

1. **The record describes the selection that was made.** Not a similar one -
   `explain` and `select_tools` are compared against each other over the real
   225-case corpus, because a record built by a second implementation is how an
   inspector ends up confidently describing a selection that never happened.
2. **The sentences are built without a display.** Everything a person could be
   misled by lives in `tool_select.Selection.why`, which is why the tests that
   check the *words* need no GTK at all.
3. **The record reaches the card it belongs to.** Four hops - assistant, sink,
   window, transcript - and a card for a tool called in round 1 must be
   explained by round 1's offer set. A transcript holds cards for every turn at
   once, so "the selection in force" would answer about the wrong one.

**The layout contract for this block is not measured here** - see the note at
the foot of the file.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import assistant as assistant_mod  # noqa: E402
from shani_chronoa import tool_select  # noqa: E402
from shani_chronoa.tools import TOOLS  # noqa: E402

#: Every request the corpus holds, read by its own key. `say` is the field -
#: an earlier probe searched for `request`, matched 12 of 225, and reported a
#: comparison that had barely happened.
CORPUS = [c["say"] for c in
          json.loads((_REPO / "tools" / "eval_cases.json").read_text())["cases"]]

#: The requests that reach the awkward branches: the conversational early
#: return, the browser rule, the cue removals, and the word-boundary bug that
#: cost this module 91% of a turn's tool budget once.
AWKWARD = [
    "thanks!",
    "book the cheapest flight on blazedemo.com",
    "Open blaze demo dot com and find the cheapest flight",
    "Is fingerprint login set up on this machine?",
    "explain what a black hole is",
    "set the volume to 40 percent",
    "what is the time",
    "zzzz qqqq vwxk plfm tkjb",
    "the file manager",
    "download the file from example.com to my Downloads folder",
]
REQUESTS = list(dict.fromkeys(CORPUS + AWKWARD))


@pytest.fixture
def no_learning(monkeypatch):
    """Pin the learned weights to none.

    **Pinned rather than read**, because the learned table is a function of
    this machine's own call log: `ranked()` reads it when its argument is left
    alone, so the corpus builder in `tools/` drifted between two builds of one
    commit for exactly this reason. A test that silently depends on whatever
    this box has run is a test that answers a different question tomorrow.
    """
    monkeypatch.setattr(tool_select, "learned_weights", lambda: {})


def _recorded(request, **kw):
    return tool_select.explain(request, TOOLS, **kw)


# --------------------------------------------------------------------------
# 1. the record IS the selection


class TestTheRecordIsTheSelection:
    def test_it_sends_exactly_what_select_tools_sends(self, no_learning):
        """**The claim the whole feature rests on, over the real corpus.**

        Not "a similar set" and not "a superset": the same names in the same
        order, for every request the evaluation corpus holds and for each of the
        three `in_use` states the loop can be in. A record that drifted here
        would be a panel confidently explaining a selection nobody made.
        """
        # **The control, before the comparison.** An empty record equals an
        # empty selection, so if `select_tools` ever answered nothing for the
        # whole corpus this test would compare `()` to `()` over 675 times and
        # pass. Every request sends at least `ask_user`, so this is cheap to
        # assert and it is what makes the comparison mean something.
        sizes = {len(tool_select.select_tools(r, TOOLS)) for r in REQUESTS}
        assert min(sizes) >= 1 and max(sizes) >= 4, (
            f"the corpus sends between {min(sizes)} and {max(sizes)} tools, so "
            "there is nothing varied to compare")
        wrong = []
        for request in REQUESTS:
            for in_use in ((), ("get_datetime",), ("edit_file", "browse")):
                sent = [t["function"]["name"]
                        for t in tool_select.select_tools(request, TOOLS,
                                                         in_use=in_use)]
                record = _recorded(request, in_use=in_use)
                if list(record.sent) != sent:
                    wrong.append((request, in_use, list(record.sent), sent))
        assert not wrong, (
            "the record describes a selection that was not the one made:\n"
            + "\n".join(f"  {r!r} in_use={i}\n    record {a}\n    sent    {b}"
                        for r, i, a, b in wrong[:5]))

    def test_the_two_doors_agree_with_each_other(self, no_learning):
        """`select_with_reason` and `select_tools` are one call, not two.

        Asking for the schemas and then the record separately would run the
        whole selection twice and could straddle a change and disagree - the
        defect this record exists to prevent, reintroduced by collecting it.
        """
        for request in REQUESTS[:60]:
            sent, record = tool_select.select_with_reason(request, TOOLS)
            alone = tool_select.select_tools(request, TOOLS)
            names = [t["function"]["name"] for t in alone]
            assert [t["function"]["name"] for t in sent] == names
            assert list(record.sent) == names

    def test_every_tool_sent_is_given_a_reason(self, no_learning):
        """A sent tool with no reason is the absence-shaped wrong answer.

        This is the one that reads as a panel that knows something: a row with
        a score and no sentence is not obviously missing anything.

        **And the reasons are not all the same one.** `_record` falls back to
        `REASON_SCORE` for a name no rule recorded, so a build with every
        `say()` dropped would answer "matched the request" for everything and
        sail through the loop below. This asserts the corpus exercises the other
        reasons, which is what stops that fallback from being the answer.
        """
        reasons = {o.reason for request in REQUESTS
                   for o in _recorded(request).considered}
        assert len(reasons) >= 5, (
            f"only {len(reasons)} distinct reason(s) across the corpus: {reasons}. "
            "Either the rules are not being recorded or this file is not "
            "reaching them.")
        assert tool_select.REASON_SCORE not in reasons or len(reasons) > 1, (
            "every considered tool says only 'matched the request', so the "
            "reason tracking is not doing anything")
        for request in REQUESTS:
            record = _recorded(request)
            for name in record.sent:
                reason = record.says(name)
                assert reason and reason != "not part of this turn's selection", (
                    f"{request!r}: {name} was sent and nothing explains it")
                assert not reason.startswith("dropped:"), (
                    f"{request!r}: {name} was sent but reads as dropped - "
                    f"{reason!r}")

    def test_the_considered_set_and_the_sent_set_are_consistent(self, no_learning):
        """Every `Offered.sent=True` is really in `sent`, and vice versa.

        Both halves, because either alone is satisfied by a record that is
        simply empty - the failure this repository has recorded as "a check that
        cannot distinguish absent from unmeasured".
        """
        for request in REQUESTS:
            record = _recorded(request)
            sent = set(record.sent)
            assert record.considered, f"{request!r}: nothing was considered"
            assert any(o.name in sent for o in record.considered if o.sent)
            assert all(o.name in sent for o in record.considered if o.sent)
            assert all(o.sent for o in record.considered if o.name in sent)

    def test_the_denominator_covers_every_scored_tool(self, no_learning):
        """`scored_total` is the number `ranked()` produced, not the record's.

        Asserted by re-deriving it from `ranked()` rather than from the record,
        because a denominator restated from the thing it measures agrees with it
        whatever it says - the `skills_total` defect in miniature.
        """
        for request in REQUESTS[:80]:
            record = _recorded(request)
            expected = len(tool_select.ranked(request, TOOLS, learned={}))
            assert record.scored_total == expected, request

    def test_an_unknown_tool_is_answered_not_raised(self, no_learning):
        """`says` and `why` never raise for a name the selection never had.

        A card asks about a tool that ran, so a miss is a defect rather than an
        empty case - and raising out of a tooltip takes the turn down with it.
        """
        record = _recorded("what is the time")
        assert record.offered("no_such_tool") is None
        assert record.says("no_such_tool") == (
            "not part of this turn's selection")
        assert record.why("no_such_tool") == (
            "no_such_tool was not part of this turn's selection.",)

    def test_considered_puts_what_was_sent_first_in_send_order(self, no_learning):
        """The order a reader will meet the list in, and it is pinned.

        Sent in the order they were sent, then what the budget removed by
        descending score, then by name. Untested, this would be a sort that
        could quietly reverse and no one would know until a card read it.
        """
        record = _recorded("what is the time")
        sent = [one.name for one in record.considered if one.sent]
        assert sent == list(record.sent)
        dropped = [one.name for one in record.considered if not one.sent]
        assert dropped == sorted(dropped, key=lambda n: (
            -record.offered(n).score, n)), dropped

    def test_select_tools_still_returns_registry_order(self, no_learning):
        """The schemas come back in `tools`' order, not the ranking's.

        **A contract, not an implementation detail**: the assistant sends this
        list to the model, and several hundred tests compare selections by name.
        Reordering it into rank order would change the prompt without changing
        a single test in this file.
        """
        order = [t["function"]["name"] for t in TOOLS]
        sent = [t["function"]["name"]
                for t in tool_select.select_tools("what is the time", TOOLS)]
        assert sent == [n for n in order if n in set(sent)]


# --------------------------------------------------------------------------
# the specific decisions the record has to be able to state


class TestEachDecisionIsStated:

    def test_a_conversational_turn_says_there_was_nothing_to_call(self, no_learning):
        record = _recorded("thanks!")
        assert record.conversation is True
        assert list(record.sent) == ["ask_user"]
        assert record.says("ask_user") == tool_select.REASON_CONVERSATION

    def test_a_tool_the_person_named_says_so_and_scored_nothing(self, no_learning):
        """`forced` bypasses selection entirely, and the record says exactly that.

        Explaining a mechanism that did not run is the failure this whole record
        exists to be able to avoid, and the named-tool path is the one place it
        is easiest to do by accident.
        """
        record = tool_select.explain("anything at all", TOOLS,
                                     forced="get_datetime")
        assert record.forced == "get_datetime"
        assert list(record.sent) == ["get_datetime"]
        assert record.scored_total == 0
        assert record.says("get_datetime") == "the person named it"
        assert "the person named it" in " ".join(record.why("get_datetime"))

    def test_a_forced_tool_that_is_not_in_the_registry_sends_nothing(self, no_learning):
        """Asked to explain a name that does not exist, the record sends nothing.

        The assistant's forced branch filters the same way, so a record claiming
        otherwise would say a nonexistent tool was sent.
        """
        record = tool_select.explain("x", TOOLS, forced="no_such_tool")
        assert list(record.sent) == []

    def test_the_browser_rule_and_the_file_rule_are_both_stated(self, no_learning):
        """One request can add `browse` *and* displace the file tools.

        The two halves were once decided in different places and disagreed;
        the record is what makes a reader able to check that they do not.
        """
        record = _recorded("book the cheapest flight on blazedemo.com")
        assert record.says("browse") == tool_select.REASON_BROWSER

    def test_a_tool_a_cue_removed_is_reported_as_removed(self, no_learning):
        """`get_world_time` scored on "time" and was then taken back out.

        The cue table exists because a small model took these tools on a generic
        word and opened something; a record that said "matched the request" for
        one that was removed would be the exact lie the table prevents.
        """
        record = _recorded("what is the time")
        removed = record.offered("get_world_time")
        assert removed is not None, (
            "expected the cue to have considered get_world_time at all")
        assert removed.sent is False
        assert removed.reason == tool_select.REASON_NO_CUE

    def test_the_login_cue_is_a_word_boundary_and_the_record_shows_it(self, no_learning):
        """`log ?in` must not match inside "login".

        This is the most expensive single character in the module: it made
        `browse` protected, `browse` is ~850 tokens of a 1536 budget, and the
        correct tool was ranked first and dropped anyway. The record is how a
        reader would have seen it - `fingerprint_status` absent from a request
        that is entirely about fingerprinting.
        """
        record = _recorded("Is fingerprint login set up on this machine?")
        assert "browse" not in record.sent, (
            "the site cue fired on a question about a local fingerprint state")
        assert "fingerprint_status" in record.sent

    def test_a_dropped_tool_names_the_budget_and_not_a_guess(self, no_learning):
        """When the cap removes a tool, that is what the record says.

        "Dropped: did not fit the tool budget" is a fact a person can act on;
        a tool silently vanishing from the offer set is the 35% dilution this
        whole item is about.
        """
        seen = 0
        for request in REQUESTS:
            record = _recorded(request)
            for offered in record.considered:
                if not offered.sent and offered.reason == tool_select.REASON_BUDGET:
                    seen += 1
                    assert record.says(offered.name) == (
                        "dropped: " + tool_select.REASON_BUDGET)
        assert seen, (
            "no tool was ever dropped by the budget across the whole corpus, so "
            "this assertion cannot distinguish the two cases and means nothing")

    def test_the_budget_quoted_is_the_one_the_cap_used(self, no_learning):
        """`budget_tokens` is `WINDOW_TOKENS * SELECTION_SHARE`, read back.

        Typed into the test rather than imported, because importing it would
        make the assertion agree with whatever the constant is - and the two
        have been different numbers in this file's own history.
        """
        record = _recorded("what is the time")
        assert record.budget_tokens == int(tool_select.WINDOW_TOKENS
                                           * tool_select.SELECTION_SHARE)
        assert record.budget_tokens == 1536

    def test_the_earliest_rule_is_the_one_reported(self, no_learning):
        """First reason wins, and the claim is worth pinning.

        `get_datetime` is kept by three rules at once on "what is the time":
        it is already in use, it is core, and it matched. Reporting the *last*
        one would put "matched the request" on a card whose honest answer is
        "always offered" - and a reader who took that as a statement about
        selection would be misled about what the core list does.
        """
        record = _recorded("what is the time")
        held = tool_select.explain("what is the time", TOOLS,
                                   in_use=("get_datetime",))
        assert record.says("get_datetime") == tool_select.REASON_CORE
        assert held.says("get_datetime") == tool_select.REASON_IN_USE
        assert record.offered("get_datetime").score > 0, (
            "the fixture no longer matches on a word, so this test would pass "
            "for the wrong reason")

    def test_a_conversational_turn_still_gets_the_tool_it_is_using(self,
                                                                  no_learning):
        """The early return keeps `in_use`, and it is now the record's branch.

        The conversational cut to `{"ask_user"}` is an early return, so it was
        the one place in the function that built its answer without reaching
        the cap - and the place a record had to be built twice. Asserted
        against `in_use`, which is the case that distinguishes it from a plain
        conversation.
        """
        record = tool_select.explain("thanks!", TOOLS, in_use=("get_datetime",))
        assert record.conversation is True
        assert "ask_user" in record.sent
        assert "get_datetime" in record.sent, (
            "a turn mid-task was starved of the tool it is already using")
        assert record.says("get_datetime") == tool_select.REASON_IN_USE

    def test_the_weights_are_read_once_so_the_two_reads_cannot_straddle(
            self, monkeypatch):
        """**The hazard the single read removes, made measurable.**

        A stand-in that answers differently on successive calls is the log
        write landing between the sort and the record. Read twice, the card
        would quote the second answer for a turn sorted by the first - a
        multiplier that is confidently wrong, which is the one thing this
        record must not do.
        """
        calls = []

        def _weights():
            calls.append(len(calls) + 1)
            return {"get_datetime": 0.40 if len(calls) == 1 else 1.90}

        monkeypatch.setattr(tool_select, "learned_weights", _weights)
        record = _recorded("what is the time")
        assert len(calls) == 1, f"the weights were read {len(calls)} times"
        assert record.offered("get_datetime").multiplier == pytest.approx(0.40)

    def test_the_learned_multiplier_is_the_one_the_sort_used(self, monkeypatch):
        """**The multiplier reported is the multiplier applied.**

        This is the guard on the change that reads the weights once and hands
        the same dict to `ranked()`. Read twice - once for the re-sort and once
        for the record - and the two reads can straddle a log write, so the card
        would quote a number the turn was not sorted by.
        """
        learned = {"get_datetime": 0.42}
        monkeypatch.setattr(tool_select, "learned_weights", lambda: dict(learned))
        record = _recorded("what is the time")
        assert record.offered("get_datetime").multiplier == pytest.approx(0.42)
        # And it is the same dictionary `ranked` was given, so the sort order
        # and the reported number cannot come from two reads.
        assert "Learning multiplied its score by 0.42." in record.why(
            "get_datetime")

    def test_no_learning_read_twice(self, monkeypatch):
        """`learned_weights` is called once per selection, not once per door.

        Two calls is what "the record reads it separately" looks like from the
        outside, and it is the whole of the hazard the single read removes.
        """
        calls = []

        def _weights():
            calls.append(1)
            return {}

        monkeypatch.setattr(tool_select, "learned_weights", _weights)
        tool_select.select_with_reason("what is the time", TOOLS)
        assert len(calls) == 1, f"the weights were read {len(calls)} times"


# --------------------------------------------------------------------------
# 2. the sentences, with no display in the way


class TestTheSentences:

    def test_they_open_with_the_request_that_was_asked(self, no_learning):
        """Without the request, the score is a number about nothing.

        This is the chain item 6a asks for - request, then scores, then the
        learned multiplier - and it is unreadable out of order.
        """
        lines = _recorded("what is the time").why("get_datetime")
        assert lines[0] == "You asked: what is the time"

    def test_a_sent_tool_reports_its_place_and_its_score(self, no_learning):
        record = _recorded("what is the time")
        lines = record.why("get_datetime")
        place = record.sent.index("get_datetime") + 1
        assert f"Sent {place} of {len(record.sent)}" in lines[1], lines
        offered = record.offered("get_datetime")
        assert f"{offered.score:.2f}" in lines[2]
        assert "1536 tokens" in lines[2]

    def test_the_word_count_singular_and_plural_are_both_right(self, no_learning):
        """A grammar slip in a diagnostic reads as the diagnostic being wrong.

        Both branches, because a template that always says "words" is a template
        nobody has read.
        """
        record = _recorded("what is the time")
        one = record.offered("get_datetime")
        assert (one.hits == 1) == ("1 word of your request" in
                                   record.why("get_datetime")[2]), record.why(
                                       "get_datetime")

    def test_the_learning_line_is_absent_when_learning_did_nothing(self, no_learning):
        """**Silences are most of the feature.**

        A line saying "learning multiplied its score by 1.00" on every card is a
        line that teaches a reader to skip the ones that matter.
        """
        lines = _recorded("what is the time").why("get_datetime")
        assert not any("Learning multiplied" in one for one in lines), lines

    def test_the_budget_line_is_absent_when_nothing_was_dropped(self, no_learning):
        """Same rule, the other optional line.

        A turn that dropped nothing gets no sentence about the budget - and the
        control is that one turn in the corpus *did* drop something, so this is
        not passing for want of a case.
        """
        quiet = None
        busy = None
        for request in REQUESTS:
            lines = _recorded(request).why(record_first(request))
            dropped = any("did not fit the budget" in one for one in lines)
            if dropped:
                busy = lines
                break
            if quiet is None:
                quiet = lines
        assert quiet is not None and busy is not None, (
            "the corpus gave only one kind of turn, so neither the quiet nor "
            "the busy assertion can distinguish itself")
        assert not any("did not fit the budget" in one for one in quiet), quiet
        assert any("did not fit the budget" in one for one in busy), busy

    def test_an_untouched_tool_says_it_was_always_offered(self, no_learning):
        """`ask_user` is sent whatever the request, and says so rather than
        claiming it matched."""
        record = _recorded("zzzz qqqq vwxk plfm tkjb")
        assert record.says("ask_user") == tool_select.REASON_CORE
        assert record.offered("ask_user").score == 0.0

    def test_a_long_request_is_clipped_on_a_word(self, no_learning):
        """Not `text[:70]`: a cut that leaves half a word reads as a typo in
        something the person typed, and an emoji's ZWJ sequence is cut happily
        by any slice."""
        request = ("Is fingerprint login set up on this machine, and how do I "
                   "check the open ports on this box right now please")
        first = _recorded(request).why("get_datetime")[0]
        assert first.startswith("You asked: ")
        assert first.endswith("...")
        body = first[len("You asked: "):-3]
        # The cut landed **on a word**: what the body dropped begins with the
        # separator, so nothing was sliced in half. Asking whether the body
        # "contains a space" would pass on any phrase and fail on every one.
        assert request.startswith(body), body
        assert request[len(body)] == " ", (
            f"the cut is mid-word: ...{body[-12:]!r} then {request[len(body)]!r}")
        assert len(first) <= len("You asked: ") + 70 + 3

    def test_a_short_request_is_not_ellipsised(self, no_learning):
        assert _recorded("thanks!").why("ask_user")[0] == "You asked: thanks!"


def _labels(widget):
    """Every `Gtk.Label` under `widget`, as widgets - read, not re-typed."""
    out, stack = [], [widget]
    while stack:
        node = stack.pop(0)
        if node.__class__.__name__ == "Label":
            out.append(node)
        child = (node.get_first_child() if hasattr(node, "get_first_child")
                 else None)
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def record_first(request):
    """The first tool the selection sent - so a sentence test has a subject."""
    record = _recorded(request)
    return record.sent[0]


# --------------------------------------------------------------------------
# 3. the record reaches the card it belongs to


@pytest.fixture
def card_text():
    """Every string a widget tree carries, read through the widgets themselves."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    def texts(widget):
        out, stack = [], [widget]
        while stack:
            node = stack.pop(0)
            for getter in ("get_label", "get_text"):
                if hasattr(node, getter):
                    value = getattr(node, getter)()
                    if value:
                        out.append(value)
            child = (node.get_first_child() if hasattr(node, "get_first_child")
                     else None)
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return out

    assert Gtk is not None
    return texts


class TestTheCardShowsIt:

    def test_the_card_renders_the_records_own_sentences(self, card_text, no_learning):
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui import blocks

        record = _recorded("what is the time")
        card = blocks.ToolCallCard("get_datetime", {}, "It is half past six.",
                                   True, selection=record)
        shown = "\n".join(card_text(card))
        for line in record.why("get_datetime"):
            assert line in shown, f"missing from the card: {line!r}"
        assert "Why this tool" in shown

    def test_a_card_with_no_selection_shows_no_why(self, card_text, no_learning):
        """The argument is optional and absent cards are the common case.

        Anything built without one - every test, the MCP path, the CLI - must
        look exactly as it did.
        """
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui import blocks

        card = blocks.ToolCallCard("get_datetime", {}, "It is half past six.")
        assert "Why this tool" not in "\n".join(card_text(card))

    def test_the_block_is_inside_the_revealer_not_the_header(self, no_learning):
        """Collapsed by default, or six cards in a turn each grow six lines.

        Asserted on the tree rather than on a class name: the block has to be a
        descendant of the revealer, which is the property that keeps a turn
        readable.
        """
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui import blocks

        record = _recorded("what is the time")
        card = blocks.ToolCallCard("get_datetime", {}, "", True,
                                   selection=record)
        assert card.revealer.get_reveal_child() is False
        inner = card.revealer.get_child()
        stack, found = [inner], False
        while stack:
            node = stack.pop(0)
            if getattr(node, "get_label", None) and (
                    node.get_label() or "") == "Why this tool":
                found = True
            child = (node.get_first_child() if hasattr(node, "get_first_child")
                     else None)
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        assert found, "the why block is not inside the revealer"


class _Sink:
    """Records what the assistant fired, so the hop can be checked for real."""

    def __init__(self):
        self.selections = []
        self.tools = []

    def on_tool_start(self, name, arguments):
        self.tools.append(name)

    def on_selection(self, record):
        self.selections.append(record)

    def fire(self, what, *args):
        handler = getattr(self, what, None)
        if callable(handler):
            handler(*args)


def _call(call_id, name, arguments=None):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments or {}}}


def _reply(text=""):
    return {"message": {"role": "assistant", "content": text},
            "prompt_eval_count": 1, "eval_count": 1}


class TestTheRecordReachesTheCard:
    """The four hops, driven: assistant, sink, window, transcript."""

    @pytest.fixture
    def turn(self, monkeypatch):
        """One real `Assistant.handle` turn that calls a tool, then answers."""
        httpx = pytest.importorskip("httpx")
        import json as _json

        from shani_chronoa.ollama_llm import OllamaLLM

        calls = []

        def _execute(name, arguments, origin=None):
            calls.append(name)
            return f"{name} ok"

        monkeypatch.setattr(assistant_mod, "execute_tool", _execute)

        replies = [
            {"message": {"role": "assistant", "content": "",
                         "tool_calls": [_call("c1", "get_datetime")]},
             "prompt_eval_count": 5, "eval_count": 3},
            _reply("It is half past six."),
        ]

        def _handler(request):
            body = _json.loads(request.content)
            return httpx.Response(
                200, json=replies.pop(0) if replies else _reply("done"))

        llm = OllamaLLM(model="qwen3:4b")
        transport = httpx.MockTransport(_handler)

        async def _get():
            if llm.client is None or llm.client.is_closed:
                llm.client = httpx.AsyncClient(transport=transport,
                                               base_url=llm.host, timeout=5.0)
            return llm.client

        llm._get_client = _get  # type: ignore[method-assign]
        return llm, calls

    def test_the_assistant_fires_one_record_per_round(self, turn, no_learning):
        """One per round, so a turn that calls a tool and then answers fires two.

        Not one per turn: the second round re-selects, and a card built from a
        single record for the turn would be explained by whichever round wrote it
        last.
        """
        llm, ran = turn
        sink = _Sink()
        answer = asyncio.run(assistant_mod.Assistant(llm).handle(
            "what time is it?", sink=sink))
        assert answer == "It is half past six."
        assert ran == ["get_datetime"]
        assert sink.tools == ["get_datetime"]
        assert len(sink.selections) == 2, [r.request for r in sink.selections]

    def test_a_card_is_built_from_the_round_that_offered_the_tool(self, turn,
                                                                  no_learning):
        """**The defect this design exists to prevent, asserted.**

        A transcript holds cards for every turn at once, so anything that reads
        "the selection in force" when a card is built would answer with the
        **next** round's offer set - a different question, with a confident
        answer. The snapshot below is taken at `on_tool_start`, which is
        between round 1's selection and round 2's.
        """
        llm, _ = turn
        sink = _Sink()
        sink.in_force_when_the_tool_started = []

        def _start(name, arguments):
            sink.tools.append(name)
            sink.in_force_when_the_tool_started.append(sink.selections[-1])

        sink.on_tool_start = _start
        asyncio.run(assistant_mod.Assistant(llm).handle("what time is it?",
                                                       sink=sink))
        assert len(sink.in_force_when_the_tool_started) == 1
        first = sink.in_force_when_the_tool_started[0]
        assert first is sink.selections[0], (
            "the card would be explained by a later round's selection")
        assert "get_datetime" in first.sent

    def test_the_record_the_sink_gets_is_the_selection_the_turn_was_sent(
            self, turn, no_learning):
        """**The card's whole claim: the record is what this turn was sent.**

        Read against `select_tools` over the same request, so a record that
        drifted from the decision would fail here rather than read well on
        screen.
        """
        llm, _ = turn
        sink = _Sink()
        asyncio.run(assistant_mod.Assistant(llm).handle("what time is it?",
                                                       sink=sink))
        record = sink.selections[0]
        assert list(record.sent) == [
            t["function"]["name"]
            for t in tool_select.select_tools("what time is it?", TOOLS)]
        assert record.request == "what time is it?"

    def test_the_sink_a_caller_passes_is_the_one_used(self, turn, no_learning):
        """`handle(sink=...)` must reach the tool events as well as the new one.

        A regression worth naming: reading `self._sink` inside the loop instead
        of the parameter would leave `on_tool_start` and `on_tool_finish` firing
        against `None` for any caller that passes a sink without setting the
        attribute - and both of those are `if sink is not None`, so every tool
        event would be dropped **silently**.
        """
        llm, _ = turn
        sink = _Sink()
        asyncio.run(assistant_mod.Assistant(llm).handle("what time is it?",
                                                       sink=sink))
        assert sink.tools == ["get_datetime"], (
            "the sink passed to handle() received no tool events")

    def test_the_conversation_hands_the_record_to_the_window(self, no_learning):
        """`_add_tool_card` is where the worker thread crosses to the main one.

        Driven with a recording window, and then the **real** transcript view is
        asked to build a card from what it received - because asserting that a
        kwarg was passed is a wiring assertion, and this file's own lesson is
        that the wiring is not the behaviour.
        """
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")

        from shani_chronoa.app import conversation as conversation_mod
        from shani_chronoa.gui import widgets

        from gi.repository import GLib

        seen = {}

        class _Window:
            def add_tool_call(self, name, arguments, result, ok, artifacts,
                              selection=None):
                seen.update(name=name, selection=selection)

        mixin = conversation_mod.ConversationMixin.__new__(
            conversation_mod.ConversationMixin)
        mixin.window = _Window()

        record = _recorded("what is the time")
        mixin._selection = record
        # `_on_tool_result_main` is the seam: it runs on the bridge's worker
        # thread, reads `_selection` there, and carries it across with the rest
        # through `GLib.idle_add`. Driving `_add_tool_card` directly would
        # hand it `selection=None` and pass for the wrong reason.
        mixin._on_tool_result_main("get_datetime", {}, "half past six", True, ())
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)

        assert seen["name"] == "get_datetime"
        assert seen["selection"] is record

    def test_the_window_forwards_the_record_to_the_transcript(self, no_learning):
        """The middle hop, driven through the real code and the real view.

        `ChronoaWindow.add_tool_call` is called unbound with a stub standing in
        for the window, because a full window is not needed to exercise a
        forwarding function - but the **transcript it forwards to is the real
        `TranscriptView`**, and the assertion is about the card that comes out
        the far end. Asserting that a keyword argument was passed is a wiring
        assertion, and this file's own lesson is that the wiring is not the
        behaviour.
        """
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui import widgets, window as window_mod

        view = widgets.TranscriptView()

        class _Window:
            """A window with the two attributes the method actually reaches.

            `_note_artifacts` is the **real** one, bound rather than faked: it
            returns immediately on an empty list, so it needs no state, and
            hand-writing a stub for it would be the failure this file's own
            header warns about - a fake that has to be extended every time the
            product grows, and that fails loudly exactly once.
            """
            _transcript = view
            _note_artifacts = window_mod.ChronoaWindow._note_artifacts

        record = _recorded("what is the time")
        window_mod.ChronoaWindow.add_tool_call(
            _Window(), "get_datetime", {}, "half past six", True, (),
            selection=record)
        cards = [c for c in view._blocks_tool_children()
                 if c.__class__.__name__ == "ToolCallCard"]
        assert len(cards) == 1, cards
        assert cards[0].selection is record
        assert "Why this tool" in "\n".join(
            one.get_label() for one in _labels(cards[0]) if one.get_label())

    def test_a_record_none_builds_a_card_with_no_why(self, no_learning):
        """The default has to work, or every caller without a sink breaks."""
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui import widgets

        view = widgets.TranscriptView()
        view.add_tool_call("get_datetime", {}, "half past six")
        cards = [c for c in view._blocks_tool_children()
                 if c.__class__.__name__ == "ToolCallCard"]
        assert cards, "no card was built at all"
        assert cards[0].selection is None
        # **The other direction, or the guard above is half a claim**: the same
        # view, given a record, hands it through. A view that dropped the
        # argument would pass the `is None` assertion for the wrong reason.
        view.add_tool_call("get_datetime", {}, "quarter past", True, (),
                           selection=_recorded("what is the time"))
        both = [c for c in view._blocks_tool_children()
                if c.__class__.__name__ == "ToolCallCard"]
        assert len(both) == 2
        assert both[0].selection is None
        assert both[1].selection is not None


# --------------------------------------------------------------------------
# What this file does not measure
#
# **The layout contract for the why block.** It adds four labels to the inside
# of a folded revealer, and `tests/test_ui_layout_contract.py` has a check for
# exactly this class of thing - a long line of prose inside a card - that was
# not run for it. What *is* held here is that the lines wrap on a character
# boundary, for the same reason the argument grid does: a folded revealer still
# lends the card its child's minimum width, and one long line pushes the whole
# card off the right of the column.
