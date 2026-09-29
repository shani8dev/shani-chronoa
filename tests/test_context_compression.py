"""Count is the wrong unit for a context budget.

`MAX_HISTORY_MESSAGES = 40` bounds history by message count, and count passes
exactly the case the cap was meant to stop. A turn that read four 30KB files is
five messages: comfortably inside the cap, while carrying more text than a
hundred ordinary turns. Nothing errors, the request just gets slower and then
the model starts answering from a truncated view of its own history - or
refuses outright because the context no longer fits.

Size needed a threshold of its own. This is that threshold's contract, and most
of it is about what must NOT happen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import compression  # noqa: E402
from shani_chronoa.assistant import Assistant  # noqa: E402


def _tool(content: str, call_id: str = "1") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _oversized() -> str:
    """A result shaped like the ones that actually blow a context: a head worth
    recognising, a long middle, and the diagnostic at the very bottom."""
    return ("HEADER: read /var/log/app.log\n"
            + "log line\n" * 900
            + "TAIL-ERROR: permission denied on /etc/shadow\n")


def _with_old_result() -> "list[dict]":
    """A history where the oversized result is genuinely old.

    Not a detail: `KEEP_RECENT_MESSAGES` means a result inside the recent
    window is *never* elided, so a single-message history has nothing old in it
    and the first version of these tests asserted compression of a result the
    code is specifically built not to touch.
    """
    history = [_tool(_oversized())]
    history += [{"role": "user", "content": f"follow-up {i}"}
                for i in range(compression.KEEP_RECENT_MESSAGES + 2)]
    return history


def _aged(message: dict) -> "list[dict]":
    """`message` followed by enough turns to be genuinely old.

    Every role test has to go through this. A lone long message sits inside the
    recent window and is skipped before the role is ever examined, so the first
    version of these tests passed even with the role check deleted - a control
    that could not fail.
    """
    history = [message]
    history += [{"role": "user", "content": f"follow-up {i}"}
                for i in range(compression.KEEP_RECENT_MESSAGES + 2)]
    return history


class _StubLLM:
    async def chat_message(self, messages, tools=None):
        return {"role": "assistant", "content": "ok"}


class TestWhatGetsCompressed:
    def test_an_oversized_tool_result_is_elided(self):
        out = compression.compress(_with_old_result())
        assert "elided" in out[0]["content"]

    def test_a_sole_recent_result_is_never_elided(self):
        """Pinned deliberately, because it looks like a bug and is not.

        Nothing in a one-message history is old, so nothing is compressed. The
        model is reasoning about that result right now; eliding it would mean
        reasoning from a summary of a fact it needed in full. A result does
        become eligible once the conversation moves past it.
        """
        out = compression.compress([_tool(_oversized())])
        assert "elided" not in out[0]["content"]

    def test_a_small_result_is_left_alone(self):
        """Most calls are small. Rewriting them costs tokens and gains nothing."""
        small = _tool("saved to /tmp/x.png")
        out = compression.compress([small])
        assert out[0] == small

    def test_a_long_user_message_is_never_touched(self):
        """The user talking is not tool output, whatever its length.

        Aged, or the recent window skips it before the role is checked and the
        test passes no matter what the role filter says.
        """
        message = {"role": "user", "content": "z" * 50_000}
        out = compression.compress(_aged(message))
        assert out[0] is message
        assert out[0]["content"] == "z" * 50_000

    def test_a_long_assistant_message_is_never_touched(self):
        """A previous answer is not tool output either, and the model may still
        need to stand behind it."""
        message = {"role": "assistant", "content": "z" * 50_000}
        out = compression.compress(_aged(message))
        assert out[0] is message

    def test_a_non_string_content_is_not_crashed_on(self):
        out = compression.compress([{"role": "tool", "content": None}])
        assert out[0]["content"] is None


class TestWhatTheElisionKeeps:
    def test_it_keeps_the_head(self):
        out = compression.compress(_with_old_result())
        assert "HEADER: read /var/log/app.log" in out[0]["content"]

    def test_it_keeps_the_tail(self):
        """Errors and verdicts land at the bottom. A head-only elision throws
        away the one line that says what went wrong."""
        out = compression.compress(_with_old_result())
        assert "TAIL-ERROR: permission denied" in out[0]["content"]

    def test_it_says_how_much_was_removed(self):
        """A model reasoning from this has to know the rest of the answer exists
        and is not being hidden. Truncation that looks like completeness is how
        a model concludes a file was 900 lines long because it was."""
        original = _oversized()
        out = compression.compress(_with_old_result())
        body = out[0]["content"]
        assert str(len(original)) in body, "the elision does not report the real size"
        assert str(len(original) - compression.HEAD_CHARS - compression.FOOT_CHARS) in body


class TestRecentOutputIsUntouched:
    def _history_with_one_old_big_result(self):
        history = [{"role": "user", "content": "read the log"},
                   _tool(_oversized())]
        history += [{"role": "user", "content": f"follow-up {i}"}
                    for i in range(compression.KEEP_RECENT_MESSAGES + 2)]
        return history

    def test_a_recent_oversized_result_is_not_elided(self):
        """The model is reasoning about the newest results right now. Compressing
        the call it is currently working through means reasoning from a summary
        of a fact it needed in full."""
        history = [_tool(_oversized())] * 1
        history += [{"role": "user", "content": f"n{i}"} for i in range(3)]
        history.append(_tool(_oversized(), "2"))
        out = compression.compress(history)
        assert "elided" not in out[-1]["content"]

    def test_the_old_one_is_still_elided(self):
        out = compression.compress(self._history_with_one_old_big_result())
        assert "elided" in out[1]["content"]


class TestTheRecordIsNeverDamaged:
    """Compression is a transport saving, not a deletion."""

    def test_the_input_list_is_never_mutated(self):
        original = _tool(_oversized())
        history = [original]
        compression.compress(history)
        assert "elided" not in original["content"]

    def test_compress_returns_new_dicts_for_what_it_changes(self):
        original = _tool(_oversized())
        out = compression.compress(_with_old_result())
        assert out[0] is not original
        assert out[0]["tool_call_id"] == original["tool_call_id"], \
            "the correlation id back to the call must survive compression"

    def test_an_empty_list_is_fine(self):
        assert compression.compress([]) == []


class TestThroughTheAssistant:
    def test_history_keeps_every_byte_after_a_turn(self):
        """The whole point: `_history` is the record, the wire copy is not."""
        a = Assistant(_StubLLM())
        a._record({"role": "user", "content": "read the log"})
        a._record(_tool(_oversized()))
        for i in range(compression.KEEP_RECENT_MESSAGES + 2):
            a._record({"role": "user", "content": f"follow-up {i}"})

        before = compression.context_size(a._history)
        wire = a.build_messages()
        after = compression.context_size(a._history)

        assert after == before, "build_messages mutated the history"
        assert "elided" not in str(a._history)
        assert compression.context_size(wire) < before

    def test_build_messages_does_not_change_the_message_count(self):
        """Eliding content must not drop a message: a missing tool result breaks
        the tool_call_id correlation and the model's view of what it asked for."""
        a = Assistant(_StubLLM())
        a._record({"role": "user", "content": "read the log"})
        a._record(_tool(_oversized()))
        for i in range(compression.KEEP_RECENT_MESSAGES + 2):
            a._record({"role": "user", "content": f"follow-up {i}"})
        assert len(a.build_messages()) == len(a._history)

    def test_an_ordinary_conversation_is_untouched(self):
        """The common case must be a no-op, not a slightly different list."""
        a = Assistant(_StubLLM())
        a._record({"role": "user", "content": "what time is it?"})
        a._record({"role": "tool", "tool_call_id": "1",
                   "content": "It is 18:30."})
        a._record({"role": "assistant", "content": "It is half past six."})
        wire = a.build_messages()
        assert [m.get("content") for m in wire] == \
               [m.get("content") for m in a._history]

    def test_the_saving_is_real(self):
        """A guard against the threshold being set so high nothing ever fires."""
        a = Assistant(_StubLLM())
        a._record({"role": "user", "content": "read four big logs"})
        for n in range(4):
            a._record(_tool(_oversized(), str(n)))
        for i in range(compression.KEEP_RECENT_MESSAGES + 2):
            a._record({"role": "user", "content": f"follow-up {i}"})
        assert compression.context_size(a.build_messages()) < \
            compression.context_size(a._history) // 4


class TestTheAggregateFloor:
    """Bounds the history even when no single result is oversized.

    The per-message rule is blind to a history made of many *merely* large
    results: 40 results of 1,999 characters is 79,960 characters - about 20,000
    tokens - and not one of them crosses a 2,000-character threshold. The
    aggregate floor is what actually bounds the window, and gemini-cli masks on
    the same principle: the trigger is the total of prunable tool output, not
    the size of any one result.
    """

    def _many_merely_large(self, count: int = 40, size: int = 1999) -> "list[dict]":
        # One character under the threshold, so the per-message rule cannot
        # fire on any of them and the floor is provably the only thing acting.
        return [{"role": "tool", "tool_call_id": str(i), "content": "x" * size}
                for i in range(count)]

    def _prunable(self, messages: "list[dict]") -> int:
        return compression._prunable_tool_chars(
            messages, max(0, len(messages) - compression.KEEP_RECENT_MESSAGES))

    def test_no_single_result_is_large_enough_to_trigger_the_other_rule(self):
        rows = self._many_merely_large()
        assert max(len(r["content"]) for r in rows) <= compression.COMPRESS_THRESHOLD_CHARS, \
            "this fixture must be ineligible for the per-message rule, or it " \
            "proves nothing about the floor"

    def test_the_floor_bounds_a_history_no_single_message_would(self):
        rows = self._many_merely_large()
        out = compression.compress(rows)
        assert self._prunable(out) <= compression.TOTAL_TOOL_BUDGET_CHARS, (
            f"prunable tool output is {self._prunable(out)} characters, over the "
            f"{compression.TOTAL_TOOL_BUDGET_CHARS} budget"
        )

    def test_the_saving_is_substantial(self):
        rows = self._many_merely_large()
        out = compression.compress(rows)
        assert compression.context_size(out) < compression.context_size(rows) * 0.5, \
            "the floor bound the history by less than half, which is not worth the code"

    def test_the_protected_window_is_never_touched(self):
        rows = self._many_merely_large()
        out = compression.compress(rows)
        keep = compression.KEEP_RECENT_MESSAGES
        assert rows[-keep:] == out[-keep:], \
            "the floor elided a result the model is reasoning about right now"

    def test_the_deep_pass_engages_when_head_and_foot_are_not_enough(self):
        # Each shallow elision still keeps HEAD_CHARS + FOOT_CHARS + a note, so
        # ~34 of them cost more than the budget. A single pass exhausts every
        # candidate and stops short of the line; the second pass is what makes
        # the floor reachable.
        rows = self._many_merely_large()
        out = compression.compress(rows)
        cutoff = max(0, len(rows) - compression.KEEP_RECENT_MESSAGES)
        deeply = [m for m in out[:cutoff]
                  if compression._is_deeply_elided(m.get("content", ""))]
        assert deeply, "the budget was met without deepening, so the second pass is dead code"

    def test_deep_elision_is_only_applied_to_older_results(self):
        rows = self._many_merely_large()
        out = compression.compress(rows)
        cutoff = max(0, len(rows) - compression.KEEP_RECENT_MESSAGES)
        for index, message in enumerate(out):
            if compression._is_deeply_elided(message.get("content", "")):
                assert index < cutoff, "a recent result was stripped to a bare note"

    def test_a_result_already_reduced_to_a_note_is_recognised_as_such(self):
        # The second pass skips anything that is already a bare note, via this
        # check. Without it the pass would keep re-deepening notes it has
        # already written, and `_prunable_tool_chars` would stop moving.
        shallow = compression._elide("x" * 9000)
        assert not compression._is_deeply_elided(shallow), \
            "a shallow elision is not a note; the guard must not skip it"
        note = compression._elide(shallow, deep=True)
        assert compression._is_deeply_elided(note)
        assert "\n" not in note, "a bare note has no retained head or foot to go"

    def test_a_history_inside_the_budget_is_left_alone(self):
        # Below the floor, and below the per-message threshold: nothing should
        # be rewritten. The common case must cost nothing.
        rows = [{"role": "user", "content": "hi"}]
        rows += [{"role": "tool", "tool_call_id": str(i), "content": "y" * 500}
                 for i in range(10)]
        assert compression.compress(rows) == rows

    def test_the_floor_never_touches_a_user_or_assistant_message(self):
        rows = self._many_merely_large()
        text = [{"role": "user", "content": "why did the disk fill up?" * 900}]
        out = compression.compress(text + rows)
        assert out[0]["content"] == text[0]["content"], \
            "the floor compressed the conversation rather than the tool output"

    def test_the_history_is_never_mutated(self):
        rows = self._many_merely_large()
        before = compression.context_size(rows)
        compression.compress(rows)
        assert compression.context_size(rows) == before

    def test_the_floor_leaves_the_undersized_elision_rules_intact(self):
        # The floor is additive. A single oversized result that has aged must
        # still be elided the ordinary way, with its head and tail kept.
        history = [_tool(_oversized())]
        history += [{"role": "user", "content": f"q{i}"}
                    for i in range(compression.KEEP_RECENT_MESSAGES + 2)]
        out = compression.compress(history)
        assert "HEADER" in out[0]["content"] and "TAIL-ERROR" in out[0]["content"]


class TestThePinnedWindowDecision:
    """A recent result is never elided, and that is a choice, not an omission.

    This looks like a bug and is not. Nothing in a one-message history is old,
    the model is reasoning about that result right now, and eliding it would
    mean reasoning from a summary of a fact it needed in full. A result becomes
    eligible once the conversation moves past it.

    These are the tests that stopped me from relaxing this. If someone does
    decide the trade is wrong, these are the ones to change deliberately - and
    `test_the_per_message_rule_ignores_the_window_when_it_should` below is the
    one to add alongside.
    """

    def test_a_sole_oversized_result_is_never_elided(self):
        original = _tool(_oversized())
        out = compression.compress([original])
        assert "elided" not in out[0]["content"]
        assert out[0]["content"] == _oversized()

    def test_the_floor_does_not_reach_into_the_window_either(self):
        # Even at 40 messages with 34 prunable results, the newest 6 survive.
        rows = [{"role": "tool", "tool_call_id": str(i), "content": "x" * 1999}
                for i in range(40)]
        out = compression.compress(rows)
        keep = compression.KEEP_RECENT_MESSAGES
        for original, current in zip(rows[-keep:], out[-keep:]):
            assert original == current


class TestFloorAccounting:
    """The floor must not stop before it has met its own budget.

    Each of these exists because the corresponding code change left the suite
    green. All three were caught by mutation, not by reading the code, and two
    of them are cases where one bug quietly cancelled the other - which is the
    reason a test that only checks the final state is not enough.
    """

    def _rows(self, count: int = 40, size: int = 1999) -> "list[dict]":
        return [{"role": "tool", "tool_call_id": str(i), "content": "x" * size}
                for i in range(count)]

    def _prunable(self, messages: "list[dict]") -> int:
        return compression._prunable_tool_chars(
            messages, max(0, len(messages) - compression.KEEP_RECENT_MESSAGES))

    def test_no_prunable_result_is_left_at_full_size(self):
        # Crediting the loop with each result's *original* size instead of the
        # size it actually recovered stops it early. The second pass then strips
        # the survivors to bare notes, so the total still lands inside the
        # budget and every budget assertion still passes - but five results
        # were rewritten that never needed to be, and twenty-nine were destroyed
        # rather than trimmed. This is the assertion that sees the difference.
        rows = self._rows()
        out = compression.compress(rows)
        cutoff = max(0, len(rows) - compression.KEEP_RECENT_MESSAGES)
        untouched = [m for m in out[:cutoff] if m.get("content") == "x" * 1999]
        assert not untouched, (
            f"{len(untouched)} prunable result(s) were left at full size, so "
            "the floor stopped before it had met its own budget"
        )

    def test_the_floor_trims_rather_than_destroys_where_it_can(self):
        # The cheap outcome keeps head and foot on the newest material it had
        # to touch. Rewriting everything to bare notes is correct but wasteful,
        # and it is what the accounting bug degenerates into.
        rows = self._rows()
        out = compression.compress(rows)
        cutoff = max(0, len(rows) - compression.KEEP_RECENT_MESSAGES)
        trimmed = [m for m in out[:cutoff]
                   if m.get("content") != "x" * 1999
                   and not compression._is_deeply_elided(m.get("content", ""))]
        assert trimmed, "every result was stripped to a note; nothing was merely trimmed"

    def test_an_unreachable_budget_still_does_not_touch_the_window(self, monkeypatch):
        # The deep pass stops as soon as the budget is met, so on any input it
        # can actually meet, the loop breaks before it ever reaches the recent
        # messages. That made the window test pass without the bound existing.
        # Forcing the budget out of reach is the only way to see the loop run to
        # the end - and to see whether it stops at `cutoff` or runs to the end
        # of the history.
        monkeypatch.setattr(compression, "TOTAL_TOOL_BUDGET_CHARS", 1)
        rows = self._rows()
        out = compression.compress(rows)
        keep = compression.KEEP_RECENT_MESSAGES
        assert rows[-keep:] == out[-keep:], \
            "with the budget unreachable the deep pass ran into the protected window"
        assert self._prunable(out) > 1, "this fixture was supposed to be over budget"

    def test_a_note_reports_the_size_of_the_original_result(self):
        # Deep elision runs on text that has already been elided once, so a
        # naive len() describes the intermediate - 1,227 characters - rather than
        # the 1,999 the model actually asked for. A model told the wrong size
        # cannot tell a truncated result from a complete one.
        rows = self._rows()
        out = compression.compress(rows)
        cutoff = max(0, len(rows) - compression.KEEP_RECENT_MESSAGES)
        notes = [m["content"] for m in out[:cutoff]
                 if compression._is_deeply_elided(m.get("content", ""))]
        assert notes, "expected at least one result to be stripped to a note"
        import re
        for note in notes:
            claimed = int(re.match(r"\[(\d+) characters", note).group(1))
            assert claimed == 1999, (
                f"the note claims {claimed} characters were elided, but the "
                f"result it replaced held 1999"
            )

    def test_the_size_of_an_untouched_result_is_reported_correctly(self):
        assert compression._original_size("x" * 4321) == 4321
        shallow = compression._elide("x" * 4321)
        assert compression._original_size(shallow) == 4321
        assert compression._original_size(compression._elide(shallow, deep=True)) == 4321

    def test_an_unreadable_marker_falls_back_to_the_length(self):
        # Over-reporting an unknown size is safer than under-reporting a known
        # one, so a marker that does not parse must not be trusted to a number.
        assert compression._original_size("[something unexpected]") == \
            len("[something unexpected]")
