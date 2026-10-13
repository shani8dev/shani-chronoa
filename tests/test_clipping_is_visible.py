"""What the transcript shows must be what the model was given.

`on_tool_result` fires *before* `_clip_tool_result` is applied to the history, so
the card in the transcript was handed the full result while the model was handed
the cut one. Someone expanding that card read output the model never saw, and
nothing said a megabyte of it had been withheld.
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import assistant as assistant_module  # noqa: E402


def test_the_clipping_note_names_what_was_withheld():
    short = "a" * 10
    assert assistant_module._clip_tool_result(short) == short, (
        "a result inside the limit was altered")

    long = "b" * 5000
    clipped = assistant_module._clip_tool_result(long, limit=1000)
    assert len(clipped) < len(long)
    assert "4,000 more characters not shown" in clipped, (
        f"the note does not say how much was dropped: ...{clipped[-200:]!r}")
    assert "1,000 characters" in clipped, (
        "the note does not say what the limit was")


def test_a_short_result_is_not_marked_as_clipped():
    assert "[..." not in assistant_module._clip_tool_result("short enough")


def test_a_non_string_result_passes_through():
    """A skill answering with a dict is a different question, not this one's."""
    value = {"rows": 3}
    assert assistant_module._clip_tool_result(value) is value


class _OneCallLLM:
    """Emits one tool call, then a plain answer - enough to drive a whole turn."""

    def __init__(self):
        self.turns = 0

    async def chat_message(self, messages, tools=None):
        self.turns += 1
        if self.turns == 1:
            return {"role": "assistant", "tool_calls": [
                {"id": "call-1", "function": {
                    "name": "read_log", "arguments": "{}"}}]}
        return {"role": "assistant", "content": "done"}


def test_the_card_is_handed_the_clipped_text(monkeypatch):
    """**The end-to-end claim, and the one that was false.**

    Not "the clip function mentions the size" but *the string the transcript
    callback receives is the same one the history gets*. The two were computed at
    different points in the turn, which is how a card and a model could disagree
    about one tool call with nothing failing.
    """
    import asyncio

    from shani_chronoa import assistant as A

    big = "x" * (A.MAX_TOOL_RESULT_CHARS + 4096)
    monkeypatch.setattr(A, "execute_tool", lambda *a, **k: big)

    assistant = A.Assistant(_OneCallLLM())
    seen = []
    asyncio.run(assistant.handle(
        "read the log",
        on_tool_result=lambda n, a, r, ok: seen.append((n, str(r)))))

    assert seen, "the turn produced no tool result at all"
    name, shown = seen[0]
    assert name == "read_log"
    assert len(shown) < len(big), (
        f"the card got all {len(big)} characters while the model got a cut one")
    assert "more characters not shown" in shown, (
        "a clipped result reached the transcript with nothing saying so")

    # and the history agrees with the card
    tool_messages = [m for m in assistant._history if m.get("role") == "tool"]
    assert tool_messages, "no tool message was recorded"
    recorded = str(tool_messages[0].get("content", ""))
    # **Contained in, not equal to.** The history wraps the result in a
    # provenance fence (`[untrusted-content-<nonce> source=file]`) and that is
    # deliberate - it is the prompt-injection defence. So the two strings differ
    # by the fence, and equality is the wrong claim; what must hold is that the
    # card shows the *same text* the model was given, with nothing extra and
    # nothing missing.
    assert shown in recorded, (
        "the transcript and the model's own history disagree about this result")

    # **And the fence survives the clip, counted rather than assumed.**
    # Clipped *after* the fence, a cut can take the closing marker with it - the
    # model is then shown untrusted content with no marker on it, which is the
    # exact hole `provenance` exists to close. Measured: fence-then-clip yields
    # one open and zero close markers.
    #
    # This assertion is the one the mutation needed. The ordering test in
    # `test_tool_activity_panel.py` reconstructs the shipped order as an
    # expression and checks it - which cannot see a mutation to the real call
    # site, because the real call site is not in the expression. The mutation that
    # moved the clip inside the fence came back a **survivor** over a green suite
    # for exactly that reason, and this is the fix: drive the turn, then count.
    opens = len(re.findall(r"(?<!end-)untrusted-content-[0-9a-f]+", recorded))
    closes = len(re.findall(r"end-untrusted-content-[0-9a-f]+", recorded))
    assert (opens, closes) == (1, 1), (
        f"the opened fence closes {closes} times, not once - the clip landed "
        f"inside the fence and took the marker with it, so untrusted content "
        f"now reaches the model reading as trusted")


def test_a_result_inside_the_limit_reaches_the_card_whole(monkeypatch):
    """The control: clipping must not touch an ordinary result."""
    import asyncio

    from shani_chronoa import assistant as A

    small = "the log is short"
    monkeypatch.setattr(A, "execute_tool", lambda *a, **k: small)
    assistant = A.Assistant(_OneCallLLM())
    seen = []
    asyncio.run(assistant.handle(
        "read the log",
        on_tool_result=lambda n, a, r, ok: seen.append(str(r))))
    assert seen == [small], f"an unclipped result came back as {seen}"
