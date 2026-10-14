"""Mutation set for the status line's two lifetimes and its bounded wrap.

**The defect.** One label, 95 writers across 11 modules, four kinds of intent on
it - a refusal, a settings toggle, the tool clock, and the turn loop's clear -
and no ordering between them. Measured 2026-10-14 on the real window, through
the real `ConversationMixin`:

  * a refusal written while a tool ran was **gone within a second**;
  * a settings toggle was destroyed the same way;
  * anything said in the **last second of a turn** was erased before it was ever
    on screen, so it had no window in which it existed;
  * a **failed** turn wrote the idle label onto the detail line, and the window
    read `detail='Ready', state='Ready'` - a state word on the one line whose
    docstring says it cannot carry state.

Neither a refusal nor a settings toggle is toasted (`_TOAST_PREFIXES` is
`"Asking again", "Attached ", "Kept in", "Saved to", "Copied"`), so the detail
line was the **only** place either sentence lived.

A separate, also-measured defect in the same widget: it had no wrap and no
ellipsize, and a realistic 150-character refusal wanted 854px of a 455px chat
column at the window's own minimum - **399px clipped**, at the end, which is
where a refusal names the file and the limit.

**Why a precedence rule and not a second lane.** The window's minimum is
`set_size_request(380, 571)` = 451px of chrome plus exactly 120px of
transcript: **zero slack**. A second label would have had to be paid for out of
the conversation, which is the opposite of the fix. So the two lanes are two
fields behind one writer and one rule - *an aside outranks a running reading* -
and the wrap is bounded to two lines so it costs 16px only for text that needs
it (measured: 17px for one line, 33px for two).

**The four shapes, and the four that are not mutated.** The shipped defect (the
clock takes the line back), the turn loop's own clear (which took an aside with
it), the state word on the aside line, and the missing wrap are each restored
here. The over-fixes are the other direction: an aside that never decays, a
`clear_status` that stops clearing the progress lane, a rail sent back to
reading the label, and a bound loosened to five lines.

One of the five call sites changed - `app/voice.py:162`, the end of a listening
turn - is **not** mutated, because reaching it means standing up the whole voice
start path and this set has no witness for it. It is recorded, not counted. A
mutation set that claims coverage it does not have is worse than one that
admits a gap, because the gap then reads as coverage.

**Every anchor is asserted `count == 1` by the runner before it is applied**, and
the run happens on a snapshot - never the shared tree, which another session is
committing to.
"""

TEST_FILE = "tests/test_the_status_line_has_two_lifetimes.py"

_WIN = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"
_CONV = "usr/lib/shani-chronoa/shani_chronoa/app/conversation.py"

MUTATIONS = [
    # -- the shipped defect: the clock takes the line back --------------------
    {
        "file": _WIN,
        "old": "        self._status_progress = (progress or \"\").strip()\n"
               "        if self._status_aside:\n"
               "            return\n"
               "        self._write_detail(self._status_progress)",
        "new": "        self._status_progress = (progress or \"\").strip()\n"
               "        self._write_detail(self._status_progress)",
        "label": "**the shipped defect, restored**: the clock writes over an "
                 "aside every second, which is how a refusal and a settings "
                 "toggle were each gone within a second of being written",
        "expect": ["test_a_refusal_written_while_a_tool_runs_is_still_there_after_a_tick",
                   "test_a_settings_toggle_survives_a_running_tool",
                   "test_the_clock_stays_out_of_the_way_while_an_aside_shows"],
    },
    {
        "file": _CONV,
        "old": "        self.window.set_progress(self._tool_clock_text(0))",
        "new": "        self.window.set_status(self._tool_clock_text(0))",
        "label": "**the same defect at the clock's arming**: the tick that "
                 "starts the clock writes straight onto the aside lane, so it "
                 "steals the line before the first tick even arrives",
        "expect": ["test_a_refusal_written_while_a_tool_runs_is_still_there_after_a_tick",
                   "test_a_settings_toggle_survives_a_running_tool"],
    },
    {
        "file": _CONV,
        "old": "            self.window.set_progress(\n"
               "                self._tool_clock_text(time.monotonic() - self._tool_clock_started))",
        "new": "            self.window.set_status(\n"
               "                self._tool_clock_text(time.monotonic() - self._tool_clock_started))",
        "label": "**the same defect at the tick**: arming is fixed and the "
                 "per-second update is not, so a refusal stolen at the start of "
                 "a tool would come back and then be stolen again a second later",
        "expect": ["test_a_refusal_written_while_a_tool_runs_is_still_there_after_a_tick",
                   "test_a_settings_toggle_survives_a_running_tool"],
        "note": "`test_the_clock_stays_out_of_the_way_while_an_aside_shows` "
                "pumps 0.5s and the first tick is at 1.0s, so it is declared "
                "witness here by reach and not by bite",
    },

    # -- the shipped defect: the turn loop's own clear ------------------------
    {
        "file": _CONV,
        "old": "            # The turn's own clear, so it is the progress lane. Measured: an\n"
               "            # aside written in the last second of a turn was erased here\n"
               "            # before it had been on screen at all.\n"
               "            self.window.set_progress(\"\")",
        "new": "            self.window.set_status(\"\")",
        "label": "**the turn loop's own clear, restored**: a successful turn's "
                 "end erases whatever was just said, so a refusal issued in the "
                 "turn's last second is never on screen at all",
        "expect": ["test_an_aside_survives_the_end_of_a_successful_turn"],
    },
    {
        "file": _CONV,
        "old": "                self.window.set_progress(\"\")",
        "new": "                self.window.set_status(\"Ready\")",
        "label": "**the failed turn's write, restored**: the idle label goes "
                 "back onto the detail line, so the window answers \"what is "
                 "Chronoa doing\" on both lines at once and says Ready when the "
                 "turn in fact went wrong",
        "expect": ["test_an_aside_survives_the_end_of_a_failed_turn",
                   "test_a_failed_turn_does_not_write_a_state_word_onto_the_detail_line"],
    },

    # -- the shipped defect: the clip ----------------------------------------
    {
        "file": _WIN,
        "old": "        self._detail_label.set_wrap(True)",
        "new": "        self._detail_label.set_wrap(False)",
        "label": "**the clip, restored**: with no wrap a 150-character refusal "
                 "wanted 854px of a 455px column, so 399px was cut - at the "
                 "end, which is where the file and the limit are named",
        "expect": ["test_the_detail_line_wraps_within_a_bound"],
    },
    {
        "file": _WIN,
        "old": "        self._detail_label.set_ellipsize(Pango.EllipsizeMode.END)",
        "new": "        self._detail_label.set_ellipsize(Pango.EllipsizeMode.NONE)",
        "label": "**the clip, restored differently**: wrapping without an "
                 "ellipsize and without a line bound grows without limit, and "
                 "the window's chrome has zero slack",
        "expect": ["test_the_detail_line_wraps_within_a_bound"],
    },

    # -- the over-fixes --------------------------------------------------------
    {
        "file": _WIN,
        "old": "        self._status_aside = text",
        "new": "        self._status_aside = self._status_aside or text",
        "label": "**the over-fix**: the first aside is permanent, so the line "
                 "freezes on whatever happened first in the session and no later "
                 "notice is ever shown",
        "expect": ["test_a_later_aside_replaces_an_earlier_one"],
    },
    {
        "file": _WIN,
        "old": "        self._status_aside = \"\"\n"
               "        self._status_progress = \"\"\n"
               "        self._write_detail(\"\")",
        "new": "        self._status_aside = \"\"\n"
               "        self._write_detail(\"\")",
        "label": "**the over-fix**: `clear_status` stops clearing the progress "
                 "lane, so a caller that wiped the line would leave the rail "
                 "reporting a tool that is not running",
        "expect": ["test_clear_status_wipes_both_lanes"],
    },
    {
        "file": _WIN,
        "old": "        return (getattr(self, \"_status_progress\", \"\") or \"\").strip()",
        "new": "        label = getattr(self, \"_detail_label\", None)\n"
               "        return ((label.get_label() if label is not None else \"\")\n"
               "                or \"\").strip()",
        "label": "**the over-fix that is a new defect**: the rail reads the "
                 "label again, and `_fill_now` prints `Running {tool}`, so an "
                 "aside on the line is reported to the rail as a running tool",
        "expect": ["test_the_rail_reports_a_tool_and_not_an_aside",
                   "test_the_clock_stays_out_of_the_way_while_an_aside_shows"],
    },
    {
        "file": _WIN,
        "old": "DETAIL_MAX_LINES = 2",
        "new": "DETAIL_MAX_LINES = 5",
        "label": "**the over-fix**: the bound is loosened to five lines, which "
                 "is what an unwrapped wrap would have cost - 81px of chrome out "
                 "of a minimum that has none to spare",
        "expect": ["test_the_detail_line_wraps_within_a_bound",
                   "test_a_long_aside_is_cut_at_the_bound_and_not_before_it"],
    },
    {
        "file": _WIN,
        "old": "DETAIL_MAX_LINES = 2",
        "new": "DETAIL_MAX_LINES = 1",
        "label": "**the over-fix in the other direction**: one line, so the "
                 "bound holds and the clip is back - a refusal cut at the first "
                 "line still loses the file and the limit",
        "expect": ["test_the_detail_line_wraps_within_a_bound"],
    },
]