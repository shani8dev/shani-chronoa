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
