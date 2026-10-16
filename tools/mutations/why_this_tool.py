"""Mutation set for the "why that tool" inspector - MAIN-UI-TODO 6a.

**The defect.** Measured before this work: **35% of the tools actually sent to
the model share no word with the request**, and `learning.reorder` re-sorts
through learned multipliers without saying so. A card reading `get_datetime`
carried no way to ask whether the request said "time" or whether the tool is
simply always in the list. Item 6a asked for the chain - request, offered tools
with scores, learned multiplier, winner - on the card.

**Why this set is shaped around "the record describes the selection that was
made".** The whole feature rests on `explain` and `select_tools` being one
function with two doors. Every way that can go wrong is a *second answer*:

- a record whose `sent` is a different set from the schemas;
- a reason dict that records nothing, so every tool falls back to the default
  `REASON_SCORE` and the panel says "matched the request" for everything;
- `unsay` that subtracts without recording, or records without subtracting -
  a cue-removed tool described as sent, or silently sent;
- `say` overwriting instead of first-wins, so a tool kept by three rules is
  explained by the last one;
- the weights read twice, so a card quotes a multiplier the turn was not
  sorted by;
- a record carried across the worker-thread boundary as `None`.

**The one that already shipped.** The conversational early return gave
`ask_user` and a tool already in use the same sentence. The *set* was right -
`in_use` still survives the cut - and the sentence was wrong: "nothing to call"
on the card a person reads in the middle of a task. Found by
`test_a_conversational_turn_still_gets_the_tool_it_is_using`, written to pin
the mutation `_CONVERSATION_REASON_MASKED`, which is in this set.

**Both directions are carried on every guard.** A learning line that always
fires teaches a reader to skip the ones that matter; one that never fires is
the 6a chain with its third link missing. A budget line emitted when nothing
was dropped and withheld when something was are both the same lie.
"""

TEST_FILE = "tests/test_why_this_tool.py"

_TS = "usr/lib/shani-chronoa/shani_chronoa/tool_select.py"
_ASSISTANT = "usr/lib/shani-chronoa/shani_chronoa/assistant.py"
_CONVERSATION = "usr/lib/shani-chronoa/shani_chronoa/app/conversation.py"
_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"
_WIDGETS = "usr/lib/shani-chronoa/shani_chronoa/gui/widgets.py"
_BLOCKS = "usr/lib/shani-chronoa/shani_chronoa/gui/blocks.py"

# --- the two doors are one function ----------------------------------------

#: The central claim. A record whose `sent` is a different set from the schemas
#: is a panel confidently describing a selection nobody made - and it is
#: indistinguishable on screen from a correct one, which is what makes it worth
#: a mutation rather than a code review.
_DIFFERENT_SET = (
    "    return [t for t in tools if _name(t) in keep], record\n",
    "    return list(tools), record\n",
)

#: The schemas alone would be right, so only a test that reads `sent` can catch
#: it. Kept as its own mutation because it fails through a different witness.
_SCHEMAS_ONLY = (
    "    keep, record = _select(request, tools, limit, in_use)\n",
    "    keep, record = _select(request, tools, limit, in_use)\n"
    "    keep = set(keep) | {t['function']['name'] for t in tools}\n",
)

#: `ranked()` reading the weights itself is the pre-change code. It agrees with
#: the record on a stand-in that answers the same both times, and disagrees on
#: a real log write landing between the sort and the record.
_RANKED_READS_AGAIN = (
    "    scored = ranked(request, tools, learned=learned)\n",
    "    scored = ranked(request, tools)\n",
)

# --- the reason dict: the absence-shaped wrong answer ----------------------

#: Every rule that keeps a tool drops its reason. `_record` falls back to
#: `REASON_SCORE`, so nothing raises, nothing is empty, and the panel says
#: "matched the request" for a tool that is always offered. Read well and
#: wrong - the failure this file's `test_every_tool_sent_is_given_a_reason`
#: is shaped to catch.
_NO_REASONS_RECORDED = (
    "    def say(names: Iterable[str], reason: str) -> None:\n",
    "    def say(names: Iterable[str], reason: str) -> None:\n"
    "        return\n",
)

#: The other half: `unsay` records the removal but no longer subtracts. The
#: cue table stops doing its job and a tool the request never asked for goes
#: back into the offer set - the `log ?in` matching inside "login" again, which
#: once cost 850 tokens of a 1536 budget and dropped the correct tool.
_REMOVAL_NOT_SUBTRACTED = (
    "        keep.difference_update(gone)\n",
    "        pass\n",
)

#: `say` overwrites instead of first-wins. `get_datetime` on "what is the time"
#: is kept by three rules at once; the last one to fire would explain it as
#: "matched the request", which is a claim about selection that is false.
_LAST_REASON_WINS = (
    "            reasons.setdefault(name, reason)\n",
    "            reasons[name] = reason\n",
)

#: The one that already shipped: `ask_user` and a tool already in use given the
#: same sentence. `REASON_IN_USE` fires first and is then overwritten, so a card
#: in the middle of a task reads "nothing to call".
_CONVERSATION_REASON_MASKED = (
    "        say((n for n in held if n in known), REASON_IN_USE)\n"
    "        say(keep, REASON_CONVERSATION)\n",
    "        say(keep, REASON_CONVERSATION)\n",
)

#: `unsay` recording a removal for a name that was never in `keep`. Subtract and
#: record are one operation for a reason: recording alone invents a candidate
#: the selection never had, and the panel then explains a tool that was never
#: under consideration.
_REMOVAL_INVENTED = (
    "        gone = set(names) & keep\n"
    "        keep.difference_update(gone)\n",
    "        gone = set(names)\n",
)

# --- the sentences ----------------------------------------------------------

#: Every card claims a place in the send order it did not have.
_RANK_OFF_BY_ONE = (
    "            place = self.sent.index(name) + 1\n",
    "            place = self.sent.index(name) + 2\n",
)

#: The multiplier line always. A line on every card is a line a reader learns
#: to skip, which is what the one that matters gets lost behind.
_LEARNING_LINE_ALWAYS = (
    "        if one.multiplier != 1.0:\n",
    "        if True:\n",
)

#: The multiplier line never. 6a's chain is request, scores, multiplier; this
#: removes the third link and nothing raises.
_LEARNING_LINE_NEVER = (
    "        if one.multiplier != 1.0:\n",
    "        if False:\n",
)

#: The budget line when nothing was dropped by the budget - a fact about a
#: decision this turn did not make.
_BUDGET_LINE_ALWAYS = (
    "        if dropped:\n",
    "        if self.considered:\n",
)

#: The request is the context the score is a score *about*. Without it the
#: panel is three numbers that could describe any turn.
_NO_REQUEST_LINE = (
    "        lines = [\"You asked: %s\" % _clip_words(self.request, 70)]\n",
    "        lines = []\n",
)

#: `_clip_words` cutting mid-word. A truncated half-word reads as a typo in
#: something the person typed, and an emoji's ZWJ sequence is cut happily by
#: any slice - the defect `markdown_lite.clip` exists for in the argument grid.
_CLIP_MID_WORD = (
    "    if \" \" in cut:\n"
    "        cut = cut[:cut.rindex(\" \")]\n",
    "",
)

#: `says` raising for a name the selection never had. A card asks about a tool
#: that ran, so a miss is a defect rather than an empty case - and raising out
#: of a tooltip takes the turn down with it.
#:
#: Re-anchored 2026-10-16, and this one was not rotted - it never matched.
#: Measured against `tool_select.py` at `3792014` (the commit that added this
#: set) **and** at its parent: the needle counted **0** in both, because it
#: carried a doubled backslash where the source has always carried a single
#: one. `git log -S` finds one commit touching that line and its added side is
#: the single-backslash form, so there is no version of the source this needle
#: ever matched. The other four rotted anchors in this batch were genuine rot -
#: both `orb_space` #5 and `proposals_skill` 1-3 counted 1 in their source at
#: the commit that introduced them, and `83ab820` is what moved the code out
#: from under them. This one was committed already broken, in a commit made to
#: carry another session's work-in-flight, so "rotted by someone else" was the
#: right story for 4 of these 5 and the wrong one for the fifth.
#:
#: The block is taken from `Selection.says`, not `Selection.why` - the two hold
#: the same three lines' shape (`why` returns a tuple for the transcript), so a
#: needle written from the shape alone would match the wrong method. That is
#: why the comment above names the method.
_RAISES_ON_UNKNOWN = (
    "        one = self.offered(name)\n"
    "        if one is None:\n"
    "            return \"not part of this turn\\'s selection\"\n",
    "        one = self.offered(name)\n"
    "        if False:\n"
    "            return \"\"\n",
)

#: A tool the budget removed described as merely not-sent, with the reason that
#: explains it dropped out of the sentence. This is the 35% dilution: the tool
#: vanishes and the card says nothing.
_DROP_HIDDEN = (
    "            return f\"dropped: {one.reason}\"\n",
    "            return one.reason\n",
)

# --- the named-tool path: explaining a mechanism that did not run -----------

#: `forced` reported as a selection that scored 256 tools. The named-tool path
#: bypasses selection entirely; a record claiming otherwise tells a person the
#: request was matched when it was not.
_FORGED_SCORED = (
    "                       0, {}, {}, reasons,\n",
    "                       len(tools), {}, {}, reasons,\n",
)

#: A name the person asked for that is not in the registry reported as sent.
_FORGED_FORCED = (
    "        reasons = {forced: \"the person named it\"}\n",
    "        reasons = {forced: \"the person named it\"}\n"
    "        return _record(request, [forced], 0, {}, {}, reasons,\n"
    "                       int(WINDOW_TOKENS * SELECTION_SHARE), (), "
    "forced=forced)\n",
)

# --- the four hops ----------------------------------------------------------

#: Reading `self._sink` instead of the parameter `handle` was handed. **This
#: is the shape of a bug that shipped in the first version of this work** - and
#: reading it today shows the hazard cannot be reached, which is why the
#: mutation entry below is flagged `equivalent` rather than witnessed.
#:
#: The site is in `_run_turn` (line 1196), **not** in `handle`, so the question
#: is whether every path into it passes through `handle`'s assignment. Measured
#: rather than reasoned:
#:
#: 1. `self._sink = sink` is at `assistant.py:1024`, inside `handle` (961-1130),
#:    and is **unconditional** - an AST sweep for enclosing `if`/`for`/`while`/
#:    `try` bodies found none.
#: 2. `_run_turn` has **exactly two** callers in the tree, both in this file:
#:    `:1085` (direct, inside `handle`) and `:896` (inside `_keep_going`).
#:    Nothing outside `assistant.py` calls it - the other two tree-wide hits are
#:    a docstring and a comment.
#: 3. `_keep_going` is read at **one** place, `:1090`, inside `handle`, which
#:    is 66 lines after the assignment.
#: 4. `self._sink` has **one** writer in the whole package (`grep -rn '_sink *='`).
#:
#: So `self._sink is sink` holds on every path to line 1196, for every caller and
#: every input. `getattr(self, "_sink", None) is not None` is therefore the same
#: predicate as `sink is not None` here, and the fire reaches the same object.
#:
#: **And the comment above the site is stale in the same way.** It says "a
#: caller that passes a sink without setting that attribute would lose every
#: tool event silently" - but `handle` sets the attribute *from its own
#: parameter*, unconditionally, before anything it calls. There is no such
#: caller. That sentence is in `assistant.py` and is left alone here (another
#: session's active file); it is recorded in AGENTS.md instead.
_SINK_SHADOWED = (
    "            if sink is not None:\n"
    "                sink.fire(\"on_selection\", selection)\n",
    "            if getattr(self, \"_sink\", None) is not None:\n"
    "                self._sink.fire(\"on_selection\", selection)\n",
)

#: The event never fired at all. The card would carry the previous turn's
#: record - or none, on the first turn of a session.
_NO_SELECTION_EVENT = (
    "            if sink is not None:\n"
    "                sink.fire(\"on_selection\", selection)\n",
    "",
)

#: The record dropped at the worker-thread boundary. `None` renders no block,
#: which is the same thing the argument's default does, so nothing raises.
_RECORD_DROPPED_AT_THE_BOUNDARY = (
    "                      artifacts, self._selection)\n",
    "                      artifacts, None)\n",
)

#: The window forwarding without the record. `add_tool_call` is called by
#: exactly one place in the product and by three tests, all positionally with
#: five arguments, so an added keyword is invisible unless the hop is driven.
_WINDOW_DROPS_RECORD = (
    "        self._transcript.add_tool_call(name, arguments, result, ok, artifacts,\n"
    "                                      selection=selection)\n",
    "        self._transcript.add_tool_call(name, arguments, result, ok,\n"
    "                                      artifacts)\n",
)

_TRANSCRIPT_DROPS_RECORD = (
    "                                         artifacts=artifacts,\n"
    "                                         selection=selection)\n",
    "                                         artifacts=artifacts)\n",
)

#: The block built and then thrown on the floor - the shape of a feature that
#: was wired all the way and never rendered.
_WHY_NEVER_APPENDED = (
    "                inner.append(why)\n",
    "                why.unparent()\n",
)

#: The block outside the revealer. Six cards in a turn each grow six lines
#: before anyone has asked to see one, and the card's own header - which is
#: what is scanned - is what grew.
_WHY_OUTSIDE_REVEALER = (
    "            if why is not None:\n"
    "                inner.append(why)\n",
    "            if why is not None:\n"
    "                self.append(why)\n",
)


MUTATIONS = [
    # --- one function, two doors -------------------------------------------
    {
        "label": "the record describes a different set from the schemas sent",
        "file": _TS,
        "old": _DIFFERENT_SET[0],
        "new": _DIFFERENT_SET[1],
        "why": "The whole feature rests on these agreeing. A record whose "
               "`sent` is the whole registry is a panel confidently describing "
               "a selection nobody made, and it is indistinguishable on screen "
               "from a correct one.",
        "expect": ["test_it_sends_exactly_what_select_tools_sends",
                   "test_the_two_doors_agree_with_each_other"],
    },
    {
        "label": "the schemas are widened past what the selection decided",
        "file": _TS,
        "old": _SCHEMAS_ONLY[0],
        "new": _SCHEMAS_ONLY[1],
        "why": "The other direction of the same pair. The record would be "
               "right and the prompt wrong, which is worse: the panel and the "
               "model disagree about what was offered.",
        # Witness corrected 2026-10-16 from the run's own `NOT caught by`.
        # `test_select_tools_still_returns_registry_order` asserts exactly one
        # invariant - `sent == [n for n in order if n in set(sent)]` - which is
        # a statement about **order**. This mutation appends every tool to
        # `keep` while iterating the registry in its own order, so the widened
        # list comes back in precisely the order that invariant demands, only
        # longer. An ordering contract cannot see a membership change, and it
        # would be wrong to weaken it to try: the guarantee that the sent list
        # is in registry order is what several hundred comparisons elsewhere
        # rely on. The membership is witnessed where it is read back as
        # membership.
        "expect": ["test_it_sends_exactly_what_select_tools_sends"],
    },
    {
        "label": "ranked() reads the learned weights a second time",
        "file": _TS,
        "old": _RANKED_READS_AGAIN[0],
        "new": _RANKED_READS_AGAIN[1],
        "why": "This is the pre-change code, and it is why the weights are read "
               "once here and handed to `ranked()`. Two reads of a value that "
               "changes when a call is logged can straddle the write, and the "
               "card quotes a multiplier the turn was not sorted by.",
        "expect": ["test_the_weights_are_read_once_so_the_two_reads_cannot_"
                   "straddle"],
    },

    # --- the reason dict ---------------------------------------------------
    {
        "label": "no rule records why it kept a tool",
        "file": _TS,
        "old": _NO_REASONS_RECORDED[0],
        "new": _NO_REASONS_RECORDED[1],
        "why": "`_record` falls back to `REASON_SCORE`, so nothing raises and "
               "nothing is empty: the panel says \"matched the request\" for a "
               "tool that is always offered. Right-shaped and wrong, which is "
               "the failure the whole item exists to remove.",
        "expect": ["test_every_tool_sent_is_given_a_reason",
                   "test_the_earliest_rule_is_the_one_reported"],
    },
    {
        "label": "a cue removal is recorded but no longer subtracts",
        "file": _TS,
        "old": _REMOVAL_NOT_SUBTRACTED[0],
        "new": _REMOVAL_NOT_SUBTRACTED[1],
        "why": "Subtract and record are one operation for a reason. Without the "
               "subtraction the cue table stops doing its job - `log ?in` "
               "matching inside \"login\" again, which once protected ~850 "
               "tokens of a 1536 budget and dropped the correct tool.",
        # Witness corrected 2026-10-16, from the run's own report: it printed
        # "1 failed, 40 passed ... failed for another reason:
        # test_a_tool_a_cue_removed_is_reported_as_removed". So the mutation did
        # break a test, and it was not one of these two.
        #   - `test_it_sends_exactly_what_select_tools_sends` compares the record
        #     against `select_tools`, and **both doors run this line** - dropping
        #     the subtraction moves them together and they still agree. Two
        #     readers of one seam cannot witness the seam.
        #   - `test_the_login_cue_is_a_word_boundary_and_the_record_shows_it`
        #     asserts `browse` not in `record.sent`; for that request `browse` is
        #     in the sent set either way, so the cue was never what decided it and
        #     the test cannot see the cue removed.
        # The witness is the one that asks whether a removed tool is *reported* as
        # removed, which is the defect, and it fires.
        "expect": ["test_a_tool_a_cue_removed_is_reported_as_removed"],
    },
    {
        "label": "the last rule to fire explains a tool, not the earliest",
        "file": _TS,
        "old": _LAST_REASON_WINS[0],
        "new": _LAST_REASON_WINS[1],
        "why": "`get_datetime` on \"what is the time\" is kept by three rules "
               "at once. The one a person can act on is the earliest, because "
               "that is the one that would still hold on the next request.",
        "expect": ["test_the_earliest_rule_is_the_one_reported"],
    },
    {
        "label": "a tool already in use is explained as 'nothing to call'",
        "file": _TS,
        "old": _CONVERSATION_REASON_MASKED[0],
        "new": _CONVERSATION_REASON_MASKED[1],
        "why": "**This shipped.** The conversational early return gave "
               "`ask_user` and a tool already in use the same sentence. The set "
               "was right - `in_use` still survives the cut - and the sentence "
               "was wrong on the one card a person reads mid-task.",
        "expect": ["test_a_conversational_turn_still_gets_the_tool_it_is_"
                   "using"],
    },
    {
        "label": "a removal is recorded for a tool that was never a candidate",
        "file": _TS,
        "old": _REMOVAL_INVENTED[0],
        "new": _REMOVAL_INVENTED[1],
        "why": "Subtracting a name that was not there is a no-op, so recording "
               "it invents a candidate the selection never had - and the panel "
               "then explains a tool that was never under consideration.",
        # Witness corrected 2026-10-16, from the run's own report: it printed
        # "2 failed, 39 passed ... failed for another reason:
        # test_the_browser_rule_and_the_file_rule_are_both_stated,
        # test_a_tool_a_cue_removed_is_reported_as_removed". So again the
        # mutation did break tests, and neither was one of the two named here.
        #   - `test_the_considered_set_and_the_sent_set_are_consistent` reads the
        #     two sets the record already agrees about, and this mutation keeps
        #     them in agreement - it adds a removal without adding a send.
        #   - `test_it_sends_exactly_what_select_tools_sends` compares the record
        #     against `select_tools`, and both doors run this line, so they move
        #     together (the same reason as the entry above).
        # What it actually changes is what the record *says*: a name the
        # selection never held is now reported as considered and removed, so the
        # browser's own reason and the cue's own reason are both overwritten.
        "expect": ["test_the_browser_rule_and_the_file_rule_are_both_stated",
                   "test_a_tool_a_cue_removed_is_reported_as_removed"],
    },

    # --- the sentences -----------------------------------------------------
    {
        "label": "a card claims a place in the send order it did not have",
        "file": _TS,
        "old": _RANK_OFF_BY_ONE[0],
        "new": _RANK_OFF_BY_ONE[1],
        "why": "\"Sent 6 of 5\" for the last tool. A number about the send "
               "order is the one thing in this block a reader could check "
               "against the turn, so it has to be the real one.",
        "expect": ["test_a_sent_tool_reports_its_place_and_its_score"],
    },
    {
        "label": "the learning line is printed on every card",
        "file": _TS,
        "old": _LEARNING_LINE_ALWAYS[0],
        "new": _LEARNING_LINE_ALWAYS[1],
        "why": "\"Learning multiplied its score by 1.00\" on every card is a "
               "line that teaches a reader to skip the ones that matter - and "
               "1.00 is not a finding, it is the absence of one.",
        "expect": ["test_the_learning_line_is_absent_when_learning_did_"
                   "nothing"],
    },
    {
        "label": "the learned multiplier is never reported",
        "file": _TS,
        "old": _LEARNING_LINE_NEVER[0],
        "new": _LEARNING_LINE_NEVER[1],
        "why": "6a's chain is request, scores, multiplier. This removes the "
               "third link: the record still carries the number and the card "
               "still has a score, so nothing is missing that a reader can see "
               "is missing.",
        "expect": ["test_the_learned_multiplier_is_the_one_the_sort_used"],
    },
    {
        "label": "the budget is reported on turns where nothing was dropped",
        "file": _TS,
        "old": _BUDGET_LINE_ALWAYS[0],
        "new": _BUDGET_LINE_ALWAYS[1],
        "why": "A sentence about a decision this turn did not make. The two "
               "budget mutations are the same lie in opposite directions and "
               "both belong here, because a line that is always there and a "
               "line that is never there read identically on a card.",
        "expect": ["test_the_budget_line_is_absent_when_nothing_was_dropped"],
    },
    {
        "label": "the card states a score without saying what the score is about",
        "file": _TS,
        "old": _NO_REQUEST_LINE[0],
        "new": _NO_REQUEST_LINE[1],
        "why": "Without the request the numbers describe any turn at all. This "
               "is the chain read out of order, and it is the reason `why()` "
               "opens with what was asked.",
        "expect": ["test_they_open_with_the_request_that_was_asked"],
    },
    {
        "label": "a long request is clipped in the middle of a word",
        "file": _TS,
        "old": _CLIP_MID_WORD[0],
        "new": _CLIP_MID_WORD[1],
        "why": "A truncated half-word reads as a typo in something the person "
               "typed, and an emoji's ZWJ sequence is cut happily by any slice "
               "- the defect `markdown_lite.clip` exists to prevent in the "
               "argument grid beside it.",
        "expect": ["test_a_long_request_is_clipped_on_a_word"],
    },
    {
        "label": "asking about a tool the selection never had raises",
        "file": _TS,
        "old": _RAISES_ON_UNKNOWN[0],
        "new": _RAISES_ON_UNKNOWN[1],
        "why": "A card asks about a tool that ran, so a miss is a defect rather "
               "than an empty case - and raising out of a diagnostic takes the "
               "turn down with it.",
        "expect": ["test_an_unknown_tool_is_answered_not_raised"],
    },
    {
        "label": "a tool the budget removed is not said to have been removed",
        "file": _TS,
        "old": _DROP_HIDDEN[0],
        "new": _DROP_HIDDEN[1],
        "why": "The sentence then reads as a reason it was merely not sent, and "
               "the tool the cap removed vanishes from the panel - which is the "
               "35% dilution this whole item is about.",
        "expect": ["test_a_dropped_tool_names_the_budget_and_not_a_guess"],
    },

    # --- the named-tool path ------------------------------------------------
    {
        "label": "the named-tool path claims the request was scored",
        "file": _TS,
        "old": _FORGED_SCORED[0],
        "new": _FORGED_SCORED[1],
        "why": "`forced` bypasses selection entirely. Explaining a mechanism "
               "that did not run is the failure this record exists to be able to "
               "avoid, and the named-tool path is where it is easiest to do by "
               "accident.",
        "expect": ["test_a_tool_the_person_named_says_so_and_scored_nothing"],
    },
    {
        "label": "a named tool that does not exist is reported as sent",
        "file": _TS,
        "old": _FORGED_FORCED[0],
        "new": _FORGED_FORCED[1],
        "why": "The assistant's forced branch filters the registry the same "
               "way, so a record claiming otherwise says a tool was sent that "
               "cannot be run.",
        "expect": ["test_a_forced_tool_that_is_not_in_the_registry_sends_"
                   "nothing"],
    },

    # --- the four hops ------------------------------------------------------
    {
        "label": "the sink handle() was handed is shadowed by self._sink",
        "file": _ASSISTANT,
        "old": _SINK_SHADOWED[0],
        "new": _SINK_SHADOWED[1],
        "why": "**The shape of a bug that shipped in the first version of this "
               "work - which today cannot be reached, and the reason is measured "
               "rather than argued.** `sink` is `handle`'s parameter and it "
               "carries `on_tool_start` and `on_tool_finish` further down, so "
               "reading `self._sink` here *looks* like it could shadow the "
               "parameter and silently drop every tool event. It cannot: "
               "`handle` assigns `self._sink = sink` from its own parameter, "
               "**unconditionally**, 61 lines before this site, and this site is "
               "in `_run_turn` rather than in `handle`. See the note on "
               "`_SINK_SHADOWED` for the four measurements - the only two callers "
               "of `_run_turn` tree-wide, the single reader of `_keep_going`, and "
               "the single writer of `_sink` in the package - that make "
               "`self._sink is sink` hold on every path.",
        # Kept deliberately, as 12 other equivalents in this tree do: it names
        # the test this mutation would have had to break. Its passing is part
        # of the evidence for the equivalence rather than a witness for it, and
        # it cannot fire - a mutation that changes nothing fails no test.
        "expect": ["test_the_sink_a_caller_passes_is_the_one_used"],
        "equivalent": True,
    },
    {
        "label": "the selection event is never fired",
        "file": _ASSISTANT,
        "old": _NO_SELECTION_EVENT[0],
        "new": _NO_SELECTION_EVENT[1],
        "why": "Every card would carry no record, or on a long session the "
               "previous turn's - which is a confident answer to a different "
               "question.",
        "expect": ["test_the_assistant_fires_one_record_per_round",
                   "test_a_card_is_built_from_the_round_that_offered_the_tool"],
    },
    {
        "label": "the record is dropped crossing from the bridge thread",
        "file": _CONVERSATION,
        "old": _RECORD_DROPPED_AT_THE_BOUNDARY[0],
        "new": _RECORD_DROPPED_AT_THE_BOUNDARY[1],
        "why": "`None` renders no block, which is exactly what the argument's "
               "default does, so nothing raises and the feature is simply "
               "absent in the product while every unit test passes.",
        "expect": ["test_the_conversation_hands_the_record_to_the_window"],
    },
    {
        "label": "the window forwards the call without the record",
        "file": _WINDOW,
        "old": _WINDOW_DROPS_RECORD[0],
        "new": _WINDOW_DROPS_RECORD[1],
        "why": "`add_tool_call` has one call site in the product and three in "
               "tests, all positionally with five arguments, so an added keyword "
               "is invisible unless this hop is driven.",
        "expect": ["test_the_window_forwards_the_record_to_the_transcript"],
    },
    {
        "label": "the transcript builds the card without the record",
        "file": _WIDGETS,
        "old": _TRANSCRIPT_DROPS_RECORD[0],
        "new": _TRANSCRIPT_DROPS_RECORD[1],
        "why": "The last hop, and the one a screenshot would look fine through. "
               "Driven through the real `TranscriptView`, because asserting a "
               "kwarg was passed is a wiring assertion and the wiring is not "
               "the behaviour.",
        # Witness corrected 2026-10-16 from the run's own `NOT caught by`.
        # `test_the_card_renders_the_records_own_sentences` constructs
        #     blocks.ToolCallCard("get_datetime", {}, ..., selection=record)
        # with its own hand, so it exercises the card's *rendering* of a record
        # it was handed directly. The mutated line is `TranscriptView`'s call -
        # the one that decides what argument the card is built with - and this
        # test never goes through the view, so no card it builds can reach it.
        # The same witness stays named by the entry below, which mutates the
        # card itself and so is exactly what that test is for. The witness that
        # survives here drives `view.add_tool_call(...)`, and says why in its
        # own docstring: "**The other direction, or the guard above is half a
        # claim**: the same view, given a record, hands it through".
        "expect": ["test_a_record_none_builds_a_card_with_no_why"],
    },
    {
        "label": "the why block is built and never shown",
        "file": _BLOCKS,
        "old": _WHY_NEVER_APPENDED[0],
        "new": _WHY_NEVER_APPENDED[1],
        "why": "The shape of a feature wired all the way to the last line and "
               "never rendered - the only mutation here that cannot be caught "
               "by reading the diff, which is why it is in the set. `unparent` "
               "rather than a call to something undefined: a mutation that "
               "crashes on import is caught by the interpreter rather than by "
               "a witness, and reports a pass the code has not earned.",
        "expect": ["test_the_block_is_inside_the_revealer_not_the_header",
                   "test_the_card_renders_the_records_own_sentences"],
    },
    {
        "label": "the why block grows the card header instead of the revealer",
        "file": _BLOCKS,
        "old": _WHY_OUTSIDE_REVEALER[0],
        "new": _WHY_OUTSIDE_REVEALER[1],
        "why": "Six cards in a turn each grow six lines before anyone has asked "
               "to see one, and the header - which is what is scanned - is what "
               "grew.",
        "expect": ["test_the_block_is_inside_the_revealer_not_the_header"],
    },
]