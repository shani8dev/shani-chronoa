"""A tool result with no tool call, and a tool call with no result, both break the request.

Both are reachable here, and neither is hypothetical:

- **Unpaired calls** are produced by the assistant itself. `_over_budget()` is
  consulted *before* each dispatch inside the tool loop, so a turn that runs out
  of time partway through a batch of four has already recorded the assistant
  message that asked for all four and only one result. `loop.stopped` is checked
  after every result, so it is paired, but a `CancelledError` from
  `AsyncBridge.cancel_pending()` and a `ConnectionError` from a backend that
  died mid-turn both land in the same place.
- **Orphaned results** arrive with the transcript. `sessions.load()` reads a
  file on disk and skips only lines that will not parse, so a hand-edited or
  truncated conversation is loaded and replayed as-is.

The consequence is asymmetric in a way that made this survivable for a long
time. **Ollama is the lenient backend**: it accepts a history that Anthropic
rejects outright with a `tool_result` carrying no `tool_use`, and that the
OpenAI-compatible gateways report as `No tool call found for function call
output`. So the default local path works, and the failure appears only once a
user opts into the cloud fallback or switches to a model that validates. A bug
that reproduces on 1 of 5 backends and only after a settings change is a bug
that survives a long audit.

## Why idempotence is the property under test, not a nicety

`clean_history()` runs on **every** model request - four or five times inside a
single turn's tool loop. If a repair stamped a wall clock into its output, the
bytes of the prompt would differ on every one of those requests even though the
conversation had not changed, and every provider's prompt-cache prefix would
miss. Ollama, Anthropic and OpenAI all key their caches on an exact token
prefix, so that is a silent, per-turn tax on the most local-first thing Chronoa
does.

The mutation that matters is therefore the one nobody writes deliberately:
adding `time.time()` to a synthesized result because it seemed useful. Nothing
about the repair would look wrong, the history would still be valid, and the
cost would only appear as a cache hit rate nobody measures. So the tests below
assert byte-identical output across repeated repairs *and* that the module
imports no clock at all, which makes the mistake impossible rather than merely
untested.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import history_repair as hr  # noqa: E402


def _call(call_id: str = "c1", name: str = "get_datetime") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": {}}}


def _assistant(calls, content: str = "") -> dict:
    return {"role": "assistant", "content": content, "tool_calls": calls}


def _result(call_id: str = "c1", content: str = "18:30") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


#: A turn interrupted partway through a batch: three calls asked for, one answered,
#: then the user's next utterance. This is the exact shape `_over_budget()`
#: leaves behind, and the one both cloud backends reject.
INTERRUPTED = [
    {"role": "system", "content": "You are Chronoa."},
    {"role": "user", "content": "what time is it and how much disk is left"},
    _assistant([_call("c1"), _call("c2", "check_disk"), _call("c3", "get_battery")]),
    _result("c1"),
    {"role": "user", "content": "never mind"},
]


class TestOrphanedResultsAreDropped:
    def test_a_result_whose_call_was_never_made_is_removed(self):
        out = hr.clean_history([
            {"role": "user", "content": "hi"},
            _assistant([_call("c1")]),
            _result("c1"),
            _result("ghost", "output from a call nobody made"),
            {"role": "user", "content": "next"},
        ])
        assert [m.get("tool_call_id") for m in out if m.get("role") == "tool"] \
            == ["c1"], (
            f"an orphaned tool result survived into the request: {out}")

    def test_a_history_that_makes_no_call_is_left_alone(self):
        """The evidence rule, which is also why the fixture above needs a call.

        `sessions.load()` replays whatever is on disk and skips only lines that
        will not parse, so a restored transcript whose call line was lost really
        does arrive as results with nothing that asked for them. "I could not
        find its call" is then not evidence of anything, and
        `Assistant.build_messages()` must not delete a message on that basis -
        `tests/test_context_compression.py::TestThroughTheAssistant` holds it to
        preserving the message count. Deleting here would be irreversible and
        gained nothing, which is the same trade the empty-id case refuses.

        This fixture and the one above are the *same shape* as far as the orphan
        question goes. That is the whole reason a conversation has to make a call
        before a result in it can be called an orphan.
        """
        messages = [{"role": "user", "content": "what time is it?"},
                    _result("c1", "It is 18:30."),
                    {"role": "assistant", "content": "It is half past six."}]
        assert hr.clean_history(messages) is messages

    def test_a_result_preceding_its_own_call_is_removed(self):
        # The call has not been made at the point this result appears, so it
        # answers nothing - the `tool_result` with no preceding `tool_use` that
        # this module exists to prevent. The trailing user turn matters - without
        # one the assistant message is the frontier and is deliberately
        # unrepaired.
        out = hr.clean_history([_result("c1"), _assistant([_call("c1")]),
                                {"role": "user", "content": "next"}])
        assert out[0]["role"] != "tool", "a result before its call was kept"
        # ... and the call it orphaned is now answered truthfully.
        assert out[1]["role"] == "tool"
        assert out[1][hr.SYNTHESIZED_KEY] is True

    def test_an_orphan_and_its_call_leave_exactly_one_result(self):
        """The pair a set-based implementation gets wrong.

        Matching a result against calls appearing *later* would treat the orphan
        as answering `c1`, repair nothing, and then drop it - leaving a call with
        no result at all, which is the defect rather than a fix for it. The
        ordered walk cannot do that; this is what it produces instead.
        """
        out = hr.clean_history([_assistant([_call("c1")]),
                                _result("later", "from a call that is coming"),
                                {"role": "user", "content": "next"}])
        results = [m for m in out if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in results] == ["c1"], (
            f"expected exactly one result, for the call that was made: {results}")
        assert hr.SYNTHESIZED_KEY in results[0]

    def test_a_matching_result_is_kept(self):
        messages = [{"role": "user", "content": "hi"},
                    _assistant([_call("c1")]), _result("c1")]
        assert hr.clean_history(messages) == messages

    def test_dropping_orphan_exposes_a_call_that_needs_repair(self):
        """The orphan and its call, with the frontier out of the way.

        The trailing user turn is what makes this a repair case rather than the
        frontier case: with the assistant message last, its calls are the live
        frontier and are left alone on purpose.
        """
        out = hr.clean_history([
            _assistant([_call("c1")]),
            _result("later", "from a call that is coming"),
            {"role": "user", "content": "next"},
        ])
        repaired = [m for m in out if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in repaired] == ["c1"], (
            f"expected exactly one result, for the call that was made: {repaired}")
        assert hr.SYNTHESIZED_KEY in repaired[0]

    def test_nothing_to_drop_returns_the_same_list_object(self):
        # The no-op has to be visibly a no-op, or a test asserting "unchanged"
        # cannot tell a pass from a mutation of its input.
        messages = [{"role": "user", "content": "hi"}]
        assert hr.drop_orphaned_tool_results(messages) is messages


class TestDanglingCallsAreRepaired:
    def test_every_unanswered_call_gets_a_result(self):
        out = hr.clean_history(INTERRUPTED)
        results = [m for m in out if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in results] == ["c1", "c2", "c3"], (
            f"expected the two missing results alongside the real one: {results}")

    def test_a_synthesized_result_says_the_call_did_not_run(self):
        out = hr.clean_history(INTERRUPTED)
        made_up = [m for m in out if m.get(hr.SYNTHESIZED_KEY)]
        assert made_up, "nothing was repaired"
        for message in made_up:
            assert "not executed" in message["content"], (
                "a fabricated result reads as a real one; the model would reason "
                f"from it: {message['content']!r}")
            assert message["content"] != _result("c1")["content"]

    def test_a_synthesized_result_is_marked_so_the_transcript_is_readable(self):
        out = hr.clean_history(INTERRUPTED)
        for message in (m for m in out if m.get("role") == "tool"):
            if message["tool_call_id"] != "c1":
                assert message[hr.SYNTHESIZED_KEY] is True
                assert message[hr.REASON_KEY] == "orphaned"
        real = [m for m in out if m.get("tool_call_id") == "c1"][0]
        assert hr.SYNTHESIZED_KEY not in real, (
            "a real tool result was marked as synthesized - the transcript would "
            "say a tool ran when it did")

    def test_the_result_lands_next_to_its_call_not_at_the_end(self):
        """The property that makes this correct on the wire, and the one a naive
        implementation gets wrong while passing a length check.

        The history already ends with a fresh user turn. Appending the repair to
        the end of the list would leave a tool result answering nothing, which is
        the defect being repaired rather than a fix for it.
        """
        out = hr.clean_history(INTERRUPTED)
        roles = [m.get("role") for m in out]
        assert roles == ["system", "user", "assistant", "tool", "tool", "tool",
                         "user"], f"results are not paired with their call: {roles}"
        # Every tool result is contiguous with the assistant message that made
        # the calls, and no user turn intervenes.
        first_user_after = roles.index("user", 3)
        assert all(r == "tool" for r in roles[3:first_user_after])

    def test_the_last_assistant_message_is_left_alone(self):
        """The live frontier. A turn that is still running answers its own calls;
        repairing them would tell the model its in-flight call did not run."""
        messages = [{"role": "user", "content": "go"},
                    _assistant([_call("c1", "get_datetime")])]
        out = hr.clean_history(messages)
        assert out == messages, "the frontier was repaired away from under the loop"

    def test_the_frontier_can_be_repaired_when_asked(self):
        messages = [{"role": "user", "content": "go"},
                    _assistant([_call("c1", "get_datetime")])]
        out = hr.clean_history(messages, repair_last_message=True)
        assert out[-1]["role"] == "tool"
        assert out[-1]["tool_call_id"] == "c1"

    def test_a_complete_history_is_returned_unchanged(self):
        messages = [
            {"role": "user", "content": "what time is it"},
            _assistant([_call("c1"), _call("c2", "check_disk")]),
            _result("c1"), _result("c2", "20G free"),
            {"role": "assistant", "content": "Half past six, 20G free."},
        ]
        assert hr.clean_history(messages) is messages, (
            "a healthy history was rewritten, so every prompt now differs from "
            "the last one for no reason")


class TestOllamaOmitsIds:
    """The adaptation that is Chronoa's own rather than pydantic-ai's.

    `assistant.py` records `call.get("id", "")` because Ollama frequently emits
    no `id` on `tool_calls` at all. Under pydantic-ai's by-id walk every id-less
    call shadows the one before it, so an interrupted batch of four calls with
    two results would be repaired into *five* results - inventing an answer to a
    call that already had one. Matching the unnamed ones FIFO keeps the counts
    equal, which is the only thing a bare `""` can honestly be matched on.
    """

    def _batch(self, calls: int, results: int) -> "list[dict]":
        def nameless(index: int) -> dict:
            return {"type": "function",
                    "function": {"name": f"tool_{index}", "arguments": {}}}

        return [
            {"role": "user", "content": "go"},
            _assistant([nameless(i) for i in range(calls)]),
            *[_result("", f"out{i}") for i in range(results)],
            {"role": "user", "content": "and now"},
        ]

    def test_nothing_real_is_dropped(self):
        out = hr.drop_orphaned_tool_results(self._batch(calls=4, results=2))
        assert len([m for m in out if m.get("role") == "tool"]) == 2, (
            "real output was discarded because the backend omitted ids")

    def test_the_counts_end_up_equal(self):
        out = hr.clean_history(self._batch(calls=4, results=2))
        assert len([m for m in out if m.get("role") == "tool"]) == 4, (
            "an id-less interrupted batch did not balance; the repair invented "
            "or lost a result")
        assert hr.unpaired_tool_calls(out) == [], (
            "calls are still unanswered after the repair")

    def test_a_fully_answered_id_less_batch_is_untouched(self):
        messages = self._batch(calls=3, results=3)[:-1]
        assert hr.clean_history(messages) is messages

    def test_nothing_unanswered_is_left_behind(self):
        assert hr.unpaired_tool_calls(self._batch(calls=3, results=1)) != []

    def test_a_named_call_does_not_condemn_a_later_id_less_result(self):
        """The empty-id rule where it is actually reachable.

        A batch that makes no id-bearing call never reaches the drop loop at all -
        there is nothing to compare against - so this mixes in one named call
        first. Without the empty-id rule this result is judged a stranger and
        deleted, and a real tool's output vanishes from the request with a
        warning that names a backend bug rather than a lost message.
        """
        messages = [
            {"role": "user", "content": "go"},
            _assistant([_call("c1")]), _result("c1"),
            {"role": "user", "content": "again"},
            _assistant([{"type": "function",
                         "function": {"name": "t", "arguments": {}}}]),
            _result("", "real output from a backend that omits ids"),
            {"role": "user", "content": "and now"},
        ]
        out = hr.clean_history(messages)
        # Asserted on `SYNTHESIZED_KEY`, not just the id: a dropped id-less
        # result is silently replaced by a synthesized one carrying the same
        # empty id, so an id-only assertion passes either way and proves nothing.
        assert [(m["tool_call_id"], bool(m.get(hr.SYNTHESIZED_KEY)))
                for m in out if m.get("role") == "tool"] == \
            [("c1", False), ("", False)], (
            "a real id-less result was deleted and replaced by a repair: "
            f"{[(m['tool_call_id'], bool(m.get(hr.SYNTHESIZED_KEY))) for m in out
                if m.get('role') == 'tool']}")


class TestPipelineOrder:
    """**A correction, not a port.** pydantic-ai's docstring says the passes'
    order is load-bearing. Here it is - but not for the reason it gives, and the
    reason it gives does not hold at all.

    The upstream claim is that dropping an orphan first "can expose a call that
    then needs a synthesized result". That is not what happens here. A tool
    result *is* a whole message with one `tool_call_id`, and
    `unpaired_tool_calls()` matches by ordered walk, so an orphan can never mask
    a dangling call in the first place - it has no id in common with one, and
    removing it exposes nothing.

    **What does make the order matter is `repair_last_response=False`.** It means
    "leave the calls of the *last* assistant message alone", and the last message
    of a history is precisely what repair is about to append to. Repair first and
    the synthesized result becomes the new last message, so the assistant
    message is no longer the frontier and a turn that is still running gets told
    its own in-flight call did not run. Drop first and the orphan is gone, the
    assistant message is the last message again, and the frontier is left alone.

    Measured rather than assumed: 8,720 of the 177,155 histories of length 1-5
    built from an 11-message pool differ between the two orders. The test below
    walks lengths 1-3 for speed, where 70 of 1,463 differ.

    The previous version of this test asserted that the passes *commute*, and
    passed on all 177,155 - because it compared the documented order against
    itself: `clean_history` is `drop -> repair`, and the comparison was
    `repair(drop(x))`, the same order again with the now-deleted merge pass
    wrapped around both sides. A test that cannot fail is precisely the failure
    this class exists to document, and it had one.
    """

    def test_the_passes_do_not_commute(self):
        # If this ever goes green, the order stopped mattering and the reasoning
        # above needs revisiting rather than deleting.
        import itertools

        def call(i, n="t"):
            return _call(i, n)

        pool = [
            _assistant([call("c1")]),
            _assistant([call("c1"), call("c2", "check_disk")]),
            _assistant([call("c2")]),
            {"role": "assistant", "content": "prose"},
            _result("c1"), _result("c2", "20G"), _result("ghost"), _result(""),
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"},
            _assistant([{"type": "function",
                         "function": {"name": "noid", "arguments": {}}}]),
        ]
        non_commuting = 0
        for length in range(1, 4):
            for combo in itertools.product(pool, repeat=length):
                forward = hr.clean_history(list(combo))
                reverse = hr.drop_orphaned_tool_results(
                    hr.repair_dangling_tool_calls(list(combo)))
                if forward != reverse:
                    non_commuting += 1
        assert non_commuting > 0, (
            "the passes commute, so `repair_last_response` is being applied to a "
            "list repair has already extended and the docstring above is now "
            "wrong")

    def test_repairing_first_would_repair_a_live_frontier(self):
        """The one mechanism behind every differing history.

        `[assistant([c1]), tool("ghost")]` is a turn that asked for something and
        is still waiting for it, with one junk result after it. Repairing first
        answers the live call and hands the model a result saying its own
        in-flight tool did not run; dropping first leaves the frontier alone.
        """
        messages = [_assistant([_call("c1")]),
                    _result("ghost", "a leftover nothing asked for")]
        assert not [m for m in hr.clean_history(messages)
                    if m.get(hr.SYNTHESIZED_KEY)], (
            "the documented order repaired a live frontier")
        assert [m.get("tool_call_id") for m in
                hr.repair_dangling_tool_calls(messages)
                if m.get(hr.SYNTHESIZED_KEY)] == ["c1"], (
            "repairing before dropping no longer fabricates a result for a live "
            "call, so the ordering no longer has anything to protect")

    def test_the_order_still_produces_a_valid_history(self):
        """What the order is actually for, and the assertion that does hold."""
        messages = [
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"},
            _assistant([_call("c1")]),
            _result("ghost", "output for a call that was never made"),
            {"role": "user", "content": "three"},
        ]
        out = hr.clean_history(messages)
        assert [m.get("role") for m in out] == \
            ["user", "user", "assistant", "tool", "user"], (
            f"unexpected shape: {[m.get('role') for m in out]}")
        # Two user turns in a row stay two messages. An earlier version of this
        # module merged them; `Assistant.build_messages()` promises to preserve
        # the message count, and eight consecutive user messages collapsed into
        # one sent `test_context_compression.py` red with `2 == 11`.
        assert [m.get("content") for m in out[:2]] == ["one", "two"], (
            "adjacent same-role messages were folded together, which is a token "
            "saving wearing the clothes of a repair")
        # The orphan is gone and the call it could have been answering is not.
        assert [m.get("tool_call_id") for m in out if m.get("role") == "tool"] \
            == ["c1"]
        assert hr.unpaired_tool_calls(out) == []


class TestDeterminismAndPurity:
    """The prompt-cache guarantee, and what would break it."""

    def test_repairing_twice_gives_identical_bytes(self):
        once = hr.clean_history(INTERRUPTED)
        twice = hr.clean_history(once)
        assert once == twice, "a second repair changed the prompt"

    def test_repeating_the_whole_pipeline_is_stable(self):
        """What actually runs: `clean_history` is called on every request, and a
        turn makes four or five of them."""
        current = list(INTERRUPTED)
        seen = []
        for _ in range(5):
            current = hr.clean_history(current)
            seen.append(repr(current))
        assert len(set(seen)) == 1, (
            "the prompt differed between requests of one turn, so a provider's "
            "cache prefix missed every time")

    def test_the_input_is_never_mutated(self):
        # `_history` is the record and the transcript is the durable copy; both
        # keep every byte, exactly as `compression.compress()` does.
        original = [dict(m) for m in INTERRUPTED]
        hr.clean_history(INTERRUPTED)
        assert INTERRUPTED == original, "the input list was modified in place"

    def test_the_module_reads_no_clock(self):
        """Makes the mistake impossible rather than merely untested.

        Nothing in Chronoa's message path stamps a wall clock into a message,
        so a `time.time()` in a repair would be the only moving byte in an
        otherwise identical prompt. Asserting the import is absent is a stronger
        statement than asserting today's output happens to match: it fails the
        moment someone adds one, before the cost reaches a cache hit rate
        nobody measures.
        """
        source = Path(hr.__file__).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not ({"time", "datetime", "random", "uuid"} & imported), (
            f"a repair that reads a clock or a random source defeats prompt "
            f"caching: {sorted(imported)}")

    def test_a_synthesized_result_carries_no_time_of_its_own(self):
        result = hr.synthesized_tool_result(_call("c1"))
        assert set(result) == {"role", "tool_call_id", "content",
                               hr.SYNTHESIZED_KEY, hr.REASON_KEY}, (
            f"unexpected keys on a synthesized result: {sorted(result)}")

    def test_the_same_call_always_synthesizes_the_same_bytes(self):
        assert hr.synthesized_tool_result(_call("c1")) == \
            hr.synthesized_tool_result(_call("c1"))


class TestMalformedInputIsTolerated:
    """A backend that returns something unexpected must not make the assistant
    unable to answer at all - the same tolerance `llm.py` applies to a bad body."""

    @pytest.mark.parametrize("messages", [
        [],
        [{}],
        [{"role": "assistant"}],
        [{"role": "assistant", "tool_calls": "not a list"}],
        [{"role": "assistant", "tool_calls": [None, 7]}],
        [{"role": "tool"}],
        [{"role": "user", "content": "hi"}, {"role": "tool", "tool_call_id": 5}],
    ])
    def test_nothing_raises(self, messages):
        assert isinstance(hr.clean_history(messages), list)

    def test_a_call_with_no_name_still_gets_an_attributable_result(self):
        out = hr.clean_history([
            _assistant([{"type": "function"}]),
            {"role": "user", "content": "next"},
        ])
        result = [m for m in out if m.get("role") == "tool"][0]
        assert result["content"], "a nameless call produced an empty result"
        assert "unnamed_tool" in result["content"], (
            "an unattributable result is unreadable in a transcript months later")


class TestRepairOrphansForAnEndedTurn:
    """`repair_orphans` is the turn-level counterpart, and it repairs a batch in
    place rather than at the end - a turn that has stopped has no frontier, and
    every call it made will never be answered."""

    def test_it_closes_out_an_interrupted_batch(self):
        out = hr.repair_orphans(INTERRUPTED, "budget")
        assert hr.unpaired_tool_calls(out) == [], "calls left unanswered"
        # The real result keeps its place and its identity; the repairs go
        # behind it, not in front of it. Getting this order wrong would
        # reorder the record of a turn that *was* partly answered.
        assert [(m.get("tool_call_id"), m.get(hr.REASON_KEY))
                for m in out if m.get("role") == "tool"] == \
            [("c1", None), ("c2", "budget"), ("c3", "budget")], (
            "the real result lost its identity, was reordered, or a repair is "
            "unlabelled")

    def test_each_reason_names_itself(self):
        for reason in ("budget", "interrupted", "orphaned"):
            content = hr.synthesized_tool_result(_call("c1"), reason)["content"]
            assert "not executed" in content, f"{reason} does not say so"
        assert "time budget" in hr.synthesized_tool_result(_call(), "budget")["content"]
        assert "interrupted" in \
            hr.synthesized_tool_result(_call(), "interrupted")["content"]

    def test_an_unknown_reason_falls_back_rather_than_raising(self):
        assert hr.synthesized_tool_result(_call("c1"), "something-new")["content"]

    def test_a_complete_history_is_untouched(self):
        messages = [
            {"role": "user", "content": "hi"},
            _assistant([_call("c1")]),
            _result("c1"),
        ]
        assert hr.repair_orphans(messages, "budget") is messages

    def test_it_is_pure(self):
        original = [dict(m) for m in INTERRUPTED]
        hr.repair_orphans(INTERRUPTED, "budget")
        assert INTERRUPTED == original
