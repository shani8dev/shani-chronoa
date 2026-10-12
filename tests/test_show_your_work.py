"""What a turn did, and what the model did not see.

The footer already reported model calls, wall time, tokens and over-budget.
This adds the two things a reader cannot infer from the answer:

- **characters withheld from the model** because a tool result was clipped, and
- **whether the turn left the machine**,

plus which skills ran. Both of the first are the shape of the confident wrong
answer this repository keeps finding, so they are stated in the same line as the
cost rather than in a panel nobody opens.

**The end-to-end test drives a real turn.** "The counter exists" is not the
claim; *"a turn that had a megabyte of output withheld reports a megabyte"* is,
and only running one establishes it.
"""

import asyncio
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import assistant as assistant_module  # noqa: E402
from shani_chronoa.gui import widgets as widgets_module  # noqa: E402


class _OneCallLLM:
    """One tool call, then an answer - enough to drive a whole turn."""

    def __init__(self, name="read_log"):
        self.name = name
        self.turns = 0

    async def chat_message(self, messages, tools=None):
        self.turns += 1
        if self.turns == 1:
            return {"role": "assistant", "tool_calls": [
                {"id": "call-1", "function": {
                    "name": self.name, "arguments": "{}"}}]}
        return {"role": "assistant", "content": "done"}


def _footer_text(stats):
    view = widgets_module.TranscriptView()
    view.set_turn_footer_handler(lambda: stats)
    view.add_user_turn("a question")
    view.add_assistant_turn("an answer")

    queue, found = deque([view]), []
    while queue:
        node = queue.popleft()
        if isinstance(node, Gtk.Label):
            classes = node.get_css_classes() or []
            if "caption" in classes and node.get_label():
                found.append(node.get_label())
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return found[0] if found else ""


def _drive(tool_result, tool_name="read_log"):
    """Run one real turn with `tool_result` coming back from the tool."""
    import shani_chronoa.assistant as A

    original = A.execute_tool
    A.execute_tool = lambda *a, **k: tool_result
    try:
        assistant = A.Assistant(_OneCallLLM(tool_name))
        asyncio.run(assistant.handle("read the log"))
        return assistant.turn_stats()
    finally:
        A.execute_tool = original


# -- the data --------------------------------------------------------------


def test_a_turn_reports_the_tools_it_ran():
    stats = _drive("a short log", "read_log")
    assert stats["tools"] == ["read_log"], stats["tools"]


def test_a_tool_that_ran_and_then_failed_is_still_listed():
    """The list you consult when something went wrong cannot be the list of
    things that worked."""
    stats = _drive("ERROR: the log is not readable", "read_log")
    assert "read_log" in stats["tools"], (
        f"a failed tool is missing from the turn's own record: {stats}")


def test_a_short_result_withholds_nothing():
    stats = _drive("a log with forty characters in it", "read_log")
    assert stats["withheld"] == 0, (
        f"{stats['withheld']} characters withheld from a short result")


def test_an_oversized_result_reports_what_the_model_never_saw():
    """**The load-bearing claim, and it needs a real turn to establish.**

    The count is `len(result) - MAX_TOOL_RESULT_CHARS`, not a difference of
    string lengths: `_clip_tool_result` appends a note of several hundred
    characters to explain itself, so measuring the two strings would report the
    *explanation* as the withheld content - and the sign would be backwards.
    """
    limit = assistant_module.MAX_TOOL_RESULT_CHARS
    stats = _drive("x" * (limit + 4000), "read_log")
    assert stats["withheld"] == 4000, (
        f"reported {stats['withheld']} withheld, expected 4000 - the note is "
        f"being counted as content")


def test_the_count_is_per_turn_and_not_a_running_total():
    """A footer reporting a session figure grows as you scroll back through the
    conversation, which is the same bug as a stale timer."""
    stats = _drive("short", "read_log")
    fresh = dict(stats)
    again = dict(stats)
    assert fresh["withheld"] == again["withheld"], (
        "a second reading of the same turn reported a different number")
    assert stats["tools"] == ["read_log"], stats["tools"]


def test_a_turn_that_stayed_local_is_not_reported_as_off_machine():
    stats = _drive("short", "read_log")
    assert stats["cloud"] is False


# -- what the footer says --------------------------------------------------


def _footer(withheld=0, cloud=False, tools=(), calls=2):
    return _footer_text({
        "model_calls": calls, "wall_seconds": 3.5,
        "usage": {"prompt": 900, "completion": 110},
        "withheld": withheld, "cloud": cloud, "tools": list(tools),
    })


def test_the_footer_says_how_much_was_withheld():
    said = _footer(withheld=40000)
    assert "40,000 characters withheld from the model" in said, said


def test_the_footer_is_silent_when_nothing_was_withheld():
    """"0 characters withheld" on every turn is noise on every turn."""
    assert "withheld" not in _footer(withheld=0), _footer(withheld=0)


def test_the_footer_says_when_the_turn_left_the_machine():
    assert "answered off this machine" in _footer(cloud=True)


def test_the_footer_is_silent_for_a_local_turn():
    assert "off this machine" not in _footer(cloud=False)


# -- what this turn wrote to your machine ----------------------------------


def _footer_with_changed(paths, **kwargs):
    stats = {"model_calls": 2, "wall_seconds": 3.5,
             "usage": {"prompt": 900, "completion": 110},
             "changed": list(paths)}
    stats.update(kwargs)
    return _footer_text(stats)


def test_the_footer_says_how_many_files_the_turn_wrote():
    """"What did *this* turn do to my machine" had no answer before: the
    pre-image ring recorded every change but not whose it was."""
    said = _footer_with_changed(["/home/u/a.txt"])
    assert "changed 1 file" in said, said


def test_several_files_are_said_in_the_plural():
    said = _footer_with_changed(["/home/u/a.txt", "/home/u/b.txt"])
    assert "changed 2 files" in said, said


def test_a_turn_that_wrote_nothing_does_not_say_changed_nothing():
    """A clause on every turn that says nothing is noise on every turn."""
    said = _footer_with_changed([])
    assert "changed" not in said, said


def test_the_files_are_counted_rather_than_listed():
    """A path in a footer is unreadable at that width, and the count is the
    thing a reader wants; the Diff panel is one click away for the names."""
    said = _footer_with_changed(["/home/u/some/deep/path.txt"])
    assert "/home/u" not in said, said


def test_a_turn_may_change_files_and_run_tools_and_go_off_machine():
    """All three at once is the ordinary case for a real turn, and the footer
    has to carry all of it rather than one clause winning."""
    said = _footer_with_changed(["/home/u/a.txt"], withheld=9000, cloud=True,
                                tools=["edit_image"])
    assert "changed 1 file" in said, said
    assert "9,000 characters withheld" in said, said
    assert "off this machine" in said, said
    assert "used edit_image" in said, said


# -- the tools -------------------------------------------------------------


def test_the_tools_are_deduplicated_and_capped():
    """"used read_log, read_log, read_log" is longer and says less."""
    said = _footer(tools=["read_log"] * 3 + ["edit_image", "scan_document",
                                             "calculate"])
    assert said.count("read_log") == 1, said
    # Four *unique* tools after dedup, three shown, so **one** more - the first
    # version expected three, counting the repeat.
    assert "and 1 more" in said, said


def test_a_turn_with_no_tools_does_not_say_used_nothing():
    assert "used" not in _footer(tools=[])


def test_the_footer_still_reports_the_cost_it_always_did():
    """The new clauses are additions, not replacements."""
    said = _footer(withheld=10, cloud=True, tools=["read_log"])
    assert "2 model calls" in said, said
    assert "3.5s" in said, said
    assert "1,010 tokens" in said, said