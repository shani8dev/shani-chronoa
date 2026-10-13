"""Mutation set for the tool-result backstop.

A tool result is the one thing in the loop the model reads but the user never
typed, and `MAX_HISTORY_MESSAGES` bounds the *count* of messages, not their size.
The skills that can return something large each bound themselves - `read_text_file`
refuses a file over 512 KiB, `webtext` caps page text at 4,000 characters - but that
discipline is per-module, so a new skill, a changed one, or an unbounded path in an
old one had no floor under it.

unsloth applies the same shape once, over every result: `_tool_result_max_chars()`.

These mutations are the two properties that make the backstop worth having - that
it is above every deliberate bound so it does not truncate legitimate output, and
that a cut never lands inside a `provenance` fence.
"""

TEST_FILE = "tests/test_tool_activity_panel.py"

_ASM = "usr/lib/shani-chronoa/shani_chronoa/assistant.py"

MUTATIONS = [
    {
        "file": _ASM,
        "old": "    if not isinstance(result, str) or len(result) <= limit:\n        return result\n",
        "new": "    if True:\n        return result\n",
        # The backstop never fires. That is the pre-fix state, reached through the
        # code rather than through an absent call site - and it is worse than
        # nothing, because the constant and the call now read as a working guard.
        "label": "the backstop never fires",
        "expect": ["test_an_oversized_result_says_what_was_dropped"],
    },
    {
        "file": _ASM,
        "old": "    dropped = len(result) - limit\n    return (result[:limit]\n            + f\"\\n\\n[... {dropped:,} more characters not shown. \"\n              f\"This result was cut at {limit:,} characters to keep the \"\n              f\"conversation within its context; ask for a narrower request, or \"\n              f\"a line range, if you need the rest.]\")\n",
        "new": "    dropped = len(result) - limit\n    return result[:limit]\n",
        # A silent truncation. The result is bounded but the model is never told,
        # so it reads a partial answer as a complete one and reasons from it -
        # the confident wrong answer the note exists to prevent.
        "label": "the truncation note is dropped (a silent cut)",
        "expect": ["test_an_oversized_result_says_what_was_dropped"],
    },
    {
        "file": _ASM,
        "old": "MAX_TOOL_RESULT_CHARS = 1024 * 1024\n",
        "new": "MAX_TOOL_RESULT_CHARS = 4000\n",
        # A backstop tighter than a deliberate bound truncates what a skill was
        # designed to return: `read_text_file` legitimately hands back up to
        # 512 KiB. The floor has to be above every bound it stands behind.
        "label": "the floor is tighter than a deliberate per-skill bound",
        "expect": ["test_the_limit_is_clear_of_every_deliberate_bound",
                   "test_an_oversized_result_says_what_was_dropped"],
    },
    {
        "file": _ASM,
        "old": "                result = clipped\n"
               "                self._record({\"role\": \"tool\","
               " \"tool_call_id\": call.get(\"id\", \"\"),\n"
               "                              \"content\": provenance.fence(\n"
               "                                  result, source).text},\n",
        "new": "                result = clipped\n"
               "                self._record({\"role\": \"tool\","
               " \"tool_call_id\": call.get(\"id\", \"\"),\n"
               "                              \"content\": provenance.fence(\n"
               "                                  _clip_tool_result(result),"
               " source).text},\n",
        # The clip moved inside the fence - the ordering that loses the closing
        # marker, so untrusted content is shown with no marker on it. This is the
        # security property, and it is why the call sits where it does.
        "label": "the clip is applied inside the fence (the close marker is lost)",
        "expect": ["test_the_driven_turn_keeps_its_fence_markers_balanced"],
    },
]
