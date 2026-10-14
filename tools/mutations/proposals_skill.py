"""Mutation set for the `review_proposals` skill - the other door onto the acceptor.

**Why a skill needs its own set when it calls a function another set already
covers.** `senses.memory.decide_candidate` is guarded by `sleep_proposals.py`.
This file is guarded here, and the two overlap on purpose: the skill is a
subprocess whose *return string is the only channel back*, so every one of these
is a mutation the acceptor's own tests cannot see. The acceptor returning
`("kept-unrecorded", "the write was refused")` correctly still produces "you
agreed to it and it is recorded as agreed, but it is not in memory" if and only
if *this* file keeps the two outcomes apart in its own words.

**The gates, in both directions.** `_consent()` is consulted here as well as in
the acceptor, because an entry-point gate that checks nothing is a promise
wearing a refusal's shape. But listing is behind the same gate as answering:
that is the one act of one key, and dropping it would put a sentence from last
night on screen for a person who turned remembering off.

**The refusal directions matter more than the successes.** Every "nothing was
changed" sentence in `_run` is load-bearing: a caller that reports a change that
did not happen is the false-promise shape this repository audits for, and it is
the failure that no test on the acceptor can produce.
"""

TEST_FILE = "tests/test_review_proposals_skill.py"

_SKILL = "usr/lib/shani-chronoa/shani_chronoa/skills/review_proposals.py"

# --- the gates -------------------------------------------------------------

_NO_ENTRY_CONSENT = (
    "    denied = _consent()\n"
    "    if denied:\n"
    "        return (f\"{denied}. Nothing was changed, and nothing is remembered - \"\n"
    "                f\"this is what Chronoa noticed last night and did not save.\")\n",
    "    denied = \"\"\n",
)

_NO_CONSENT_BODY = (
    "    from shani_chronoa.config import ChronoaConfig\n"
    "\n"
    "    return ChronoaConfig().sense_allowed_reason(\"memory\")\n",
    "    return \"\"\n",
)

#: **The half-gate**, which is the shape that is easier to ship than either
#: all-or-nothing: every answer still refuses, so the decision path is intact
#: and the refusal sentence is still what a press produces - and the listing is
#: not consulted, so a sentence from last night is quoted to somebody who
#: turned remembering off. The first version of this mutated
#: `_listing(rows)` into `_listing(rows[:1])`, which is a different defect
#: entirely; the runner caught it, reporting one unrelated failure.
_NO_LISTING = (
    "    denied = _consent()\n"
    "    if denied:\n",
    "    denied = _consent()\n"
    "    if denied and action != \"list\":\n",
)

# --- the answer table ------------------------------------------------------

_ANSWER_INVERTED = (
    "_ANSWER = {\"keep\": True, \"discard\": False}",
    "_ANSWER = {\"keep\": False, \"discard\": True}",
)

_ANSWER_DERIVED = (
    "    row = rows[wanted - 1]\n"
    "    outcome, reason = memory.decide_candidate(\n"
    "        row.session_id, row.index, _ANSWER[action])\n",
    "    row = rows[wanted - 1]\n"
    "    outcome, reason = memory.decide_candidate(\n"
    "        row.session_id, row.index, action == \"keep\")\n",
)

# --- the two answers that are different facts ------------------------------

_OUTCOMES_COLLAPSED = (
    "    if outcome == \"kept\":\n"
    "        return f\"Remembered: {row.text}\"\n"
    "    if outcome == \"kept-unrecorded\":\n"
    "        return (f\"You agreed to it and it is recorded as agreed, but it is not \"\n"
    "                f\"in memory: {reason}\")\n",
    "    if outcome.startswith(\"kept\"):\n"
    "        return f\"Remembered: {row.text}\"\n",
)

_UNRECORDED_IS_KEPT = (
    "    if outcome == \"kept-unrecorded\":\n"
    "        return (f\"You agreed to it and it is recorded as agreed, but it is not \"\n"
    "                f\"in memory: {reason}\")\n",
    "    if outcome == \"kept-unrecorded\":\n"
    "        return f\"Remembered: {row.text}\"\n",
)

_DECLINED_NOT_REPORTED = (
    "    if outcome == \"declined\":\n"
    "        return f\"Dropped: {row.text}. It will not be offered again.\"\n",
    "    if outcome == \"declined\":\n"
    "        return f\"Remembered: {row.text}\"\n",
)

_UNKNOWN_OUTCOME = (
    "    if outcome == \"already-decided\":\n"
    "        return f\"{row.text} was already {reason}.\"\n"
    "    return reason\n",
    "    if outcome == \"already-decided\":\n"
    "        return f\"{row.text} was already {reason}.\"\n"
    "    return f\"Remembered: {row.text}\"\n",
)

# --- the refusals ----------------------------------------------------------

_NO_NUMBER_CHECK = (
    "    raw = arguments.get(\"number\")\n"
    "    if raw is None:\n",
    "    raw = arguments.get(\"number\")\n"
    "    if False:\n",
)

_NO_RANGE_CHECK = (
    "    if not 1 <= wanted <= len(rows):\n",
    "    if False:\n",
)

_RANGE_TOLERATED = (
    "    if not 1 <= wanted <= len(rows):\n"
    "        return (f\"There is no proposal {wanted} - the list has \"\n"
    "                f\"{len(rows)}. Nothing was changed.\")\n"
    "\n"
    "    row = rows[wanted - 1]\n",
    "    if not 1 <= wanted <= len(rows):\n"
    "        return (f\"There is no proposal {wanted} - the list has \"\n"
    "                f\"{len(rows)}. Nothing was changed.\")\n"
    "\n"
    "    row = rows[min(max(wanted, 1), len(rows)) - 1]\n",
)

_BAD_ACTION_KEEPS = (
    "    if action not in _ANSWER:\n"
    "        return (f\"'{action}' is not one of list, keep or discard. Nothing was \"\n"
    "                f\"changed.\")\n",
    "    if action not in _ANSWER:\n"
    "        return f\"Remembered: {rows[0].text}\"\n",
)

_NO_TRY_AROUND_LISTING = (
    "    try:\n"
    "        rows = memory.pending_proposals()\n"
    "    except Exception as exc:  # noqa: BLE001 - the sense may not be importable\n"
    "        return f\"The sleep pass that proposes facts is not available: {exc}\"\n",
    "    rows = memory.pending_proposals()\n",
)

_EMPTY_IS_SILENT = (
    "    if not rows:\n"
    "        return (\n"
    "            \"Nothing is waiting. No conversation has left a sentence Chronoa \"\n"
    "            \"thought was worth keeping without asking first - which is the \"\n"
    "            \"point of asking.\"\n"
    "        )\n",
    "    if not rows:\n"
    "        return \"\"\n",
)

_NO_COUNT = (
    "    lines = [f\"{len(rows)} proposed fact(s), none of them remembered:\", \"\"]\n",
    "    lines = [\"\", \"\"]\n",
)

_NO_TURN = (
    "        where = f\"said in turn {row.source_index + 1}\" if row.source_role else \"\"\n",
    "        where = \"\"\n",
)

_NO_SESSION = (
    "        lines.append(f\"     ({row.session_id}, {where})\")\n",
    "        lines.append(f\"     ({where})\")\n",
)

_NO_TAIL = (
    "    lines.append(\n"
    "        \"Say which number to keep or to discard, or ask to keep all of them. \"\n"
    "        \"Nothing is stored until you say so.\"\n"
    "    )\n",
    "",
)

MUTATIONS = [
    # --- the gates, each with its over-fix -------------------------------
    {
        "label": "the tool asks nobody before acting",
        "file": _SKILL,
        "old": _NO_ENTRY_CONSENT[0],
        "new": _NO_ENTRY_CONSENT[1],
        "why": "`decide_candidate` checks again, so the *write* is still safe - "
               "which is why this mutation survives a green acceptor suite. "
               "What stops being true is the refusal itself: the person who "
               "turned memory off now gets an error string from the acceptor "
               "raised through a listing they were never meant to see.",
        "expect": ["test_consent_off_refuses_and_names_the_row_not_the_key"],
    },
    {
        "label": "_consent() is called and its answer thrown away",
        "file": _SKILL,
        "old": _NO_CONSENT_BODY[0],
        "new": _NO_CONSENT_BODY[1],
        "why": "The shape `test_no_declared_gate_is_advertised_without_a_check` "
               "exists for: the call reads as a gate in the source and is not one "
               "in the running program.",
        "expect": ["test_consent_off_refuses_and_names_the_row_not_the_key",
                   "test_the_listing_gate_is_the_same_one_as_the_decisions"],
    },
    {
        "label": "listing stays open with the gate closed",
        "file": _SKILL,
        "old": _NO_LISTING[0],
        "new": _NO_LISTING[1],
        "why": "The over-fix: the answers all refuse, so the *decision* path is "
               "still gated, and the sentence from last night is quoted to a "
               "person who said no. This is the exact half-gate that is easier "
               "to ship than either all-or-nothing.",
        "expect": ["test_the_listing_gate_is_the_same_one_as_the_decisions"],
    },

    # --- which answer the table gives ------------------------------------
    {
        "label": "keep means discard and discard means keep",
        "file": _SKILL,
        "old": _ANSWER_INVERTED[0],
        "new": _ANSWER_INVERTED[1],
        "why": "Every guard in the module still holds: the enum is still the "
               "enum, the number is still in range, the acceptor is still the "
               "shared one. Only the one word is reversed, so nothing reports a "
               "problem.",
        "expect": ["test_keeping_stores_the_fact_and_says_so",
                   "test_discarding_removes_it_and_does_not_remove_anything_"
                   "else"],
    },
    {
        "label": "the answer is derived from the action instead of looked up",
        "file": _SKILL,
        "old": _ANSWER_DERIVED[0],
        "new": _ANSWER_DERIVED[1],
        "equivalent": True,
        "why": "Equivalent for **every** input the schema admits: the enum is "
               "`[list, keep, discard]` and `action == \"keep\"` is true for "
               "exactly the one entry where the table says `True`. The table is "
               "what makes that true, and `guardrail` validates the enum before "
               "dispatch, so the two spellings cannot be told apart by any "
               "caller - not through the tool and not by driving `_run` "
               "directly, because a fourth value never reaches either. The "
               "table is kept as the place a fourth spelling would be rejected, "
               "which is the property the guardrail test above it exists for.",
    },

    # --- the two answers that are different facts ------------------------
    {
        "label": "kept and kept-unrecorded are reported as the same sentence",
        "file": _SKILL,
        "old": _OUTCOMES_COLLAPSED[0],
        "new": _OUTCOMES_COLLAPSED[1],
        "why": "`startswith` is the natural way to collapse a vocabulary and it "
               "is the one shape that silently turns \"you agreed and it is not "
               "in memory\" into \"remembered\". The return string is the only "
               "channel a subprocess has, so this is the whole of the "
               "distinction as far as a caller can tell.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_a_proposal_nothing_can_be_stored_is_not_reported_as_remembered",
                   "test_a_proposal_nothing_can_be_stored_is_not_reported_as_remembered"],
    },
    {
        "label": "a failed write is reported as remembered",
        "file": _SKILL,
        "old": _UNRECORDED_IS_KEPT[0],
        "new": _UNRECORDED_IS_KEPT[1],
        "why": "The over-fix for the one above: the branch still exists and "
               "still returns the *row's* text, so the only difference is the "
               "word - and a caller cannot act on the difference.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_a_proposal_nothing_can_be_stored_is_not_reported_as_remembered",
                   "test_a_proposal_nothing_can_be_stored_is_not_reported_as_remembered"],
    },
    {
        "label": "a discarded sentence is reported as remembered",
        "file": _SKILL,
        "old": _DECLINED_NOT_REPORTED[0],
        "new": _DECLINED_NOT_REPORTED[1],
        "why": "The person said no and is told they said yes, in a sentence "
               "identical to the one a successful Keep returns.",
        "expect": ["test_discarding_removes_it_and_does_not_remove_anything_"
                   "else"],
    },
    {
        "label": "an outcome the module does not know is reported as remembered",
        "file": _SKILL,
        "old": _UNKNOWN_OUTCOME[0],
        "new": _UNKNOWN_OUTCOME[1],
        "why": "The final `return reason` exists so an outcome added later is "
               "*explained* rather than read as success. A fallback that says "
               "Remembered makes the acceptor the only place the vocabulary is "
               "honoured, and a new outcome becomes a false promise the moment "
               "it is added.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_an_outcome_this_module_does_not_know_is_explained_not_read_as_success",
                   "test_an_outcome_this_module_does_not_know_is_explained_not_read_as_success"],
    },

    # --- the refusals -----------------------------------------------------
    {
        "label": "a missing number decides proposal one",
        "file": _SKILL,
        "old": _NO_NUMBER_CHECK[0],
        "new": _NO_NUMBER_CHECK[1],
        "why": "The refusal is a dead `if False`, so `wanted = int(None)` "
               "raises and the whole skill answers nothing - which is the "
               "shape `stop_service()` returning '' for a unit already stopped "
               "records as an absence-shaped wrong answer.",
        "expect": ["test_a_missing_number_asks_rather_than_deciding_the_first_"
                   "one"],
    },
    {
        "label": "a number outside the list is not checked",
        "file": _SKILL,
        "old": _NO_RANGE_CHECK[0],
        "new": _NO_RANGE_CHECK[1],
        "why": "Index 0 is the *last* proposal, so a person asking for number 9 "
               "of 3 is answered about number 3 and told it was kept.",
        "expect": ["test_a_number_outside_the_list_is_refused_with_the_count"],
    },
    {
        "label": "an out-of-range number is clamped to the nearest real one",
        "file": _SKILL,
        "old": _RANGE_TOLERATED[0],
        "new": _RANGE_TOLERATED[1],
        "equivalent": True,
        "why": "Equivalent **because of the guard immediately above it**: the "
               "clamped line is only reached when `1 <= wanted <= len(rows)`, so "
               "`min(max(wanted, 1), len(rows))` is `wanted` for every input that "
               "gets there. This is the over-fix the set was written to carry - "
               "the variant that passes a test which only checks the guard is "
               "present - and it is inert for exactly that reason. The guard's "
               "own mutation above is the one that bites.",
    },
    {
        "label": "an action outside the enum is treated as a decision",
        "file": _SKILL,
        "old": _BAD_ACTION_KEEPS[0],
        "new": _BAD_ACTION_KEEPS[1],
        "why": "The guardrail validates the enum before dispatch, so this is "
               "unreachable through the tool path - which is why the direct-"
               "`_run` test exists. A model that invents `\"store\"` reaches the "
               "skill by whatever route, and the answer is somebody's memory.",
        "expect": ["test_the_handler_also_refuses_a_bad_argument_when_reached_"
                   "directly"],
        # `test_a_bad_enum_or_type_is_refused_before_the_skill_runs` was named
        # here and the runner reported "NOT caught by": it goes through
        # `guardrail`, which refuses before the handler is entered, so it passes
        # with or without this branch. Corrected by name rather than by writing
        # a new test - the direct-drive witness above is the one that bites.
    },
    {
        "label": "a sleep pass that cannot be read takes the skill down with it",
        "file": _SKILL,
        "old": _NO_TRY_AROUND_LISTING[0],
        "new": _NO_TRY_AROUND_LISTING[1],
        "why": "An ImportError from the sense, or a store that is not a "
               "directory, arrives as a traceback instead of a sentence - and "
               "a traceback to a model is an unhandled failure it will retry, "
               "or route around.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_a_sleep_pass_that_cannot_be_read_is_a_sentence_not_a_traceback",
                   "test_a_sleep_pass_that_cannot_be_read_is_a_sentence_not_a_traceback"],
    },

    # --- what the listing says when it has something ----------------------
    {
        "label": "nothing waiting is answered with nothing",
        "file": _SKILL,
        "old": _EMPTY_IS_SILENT[0],
        "new": _EMPTY_IS_SILENT[1],
        "why": "An empty return string reads to a model as \"there was nothing "
               "in the tool output\", which is indistinguishable from the tool "
               "not having run. The sentence names the condition.",
        "expect": ["test_the_whole_list_empty_is_an_answer_not_a_silence"],
    },
    {
        "label": "the listing does not say how many are waiting",
        "file": _SKILL,
        "old": _NO_COUNT[0],
        "new": _NO_COUNT[1],
        "why": "\"which number do you want\" is unanswerable without a count, and "
               "the count is also the thing that proves the listing is bounded "
               "rather than truncated silently.",
        # `test_the_number_is_the_one_the_list_showed` was named here and the
        # runner reported "NOT caught by" - it parses the numbered rows, which
        # this mutation leaves alone. Corrected by name. The second witness is
        # the same property with two proposals in the fixture, because with one
        # row a header reading "" and one reading "1" are the same shape.
        "expect": ["test_the_skill_is_reachable_and_lists_what_sleep_noticed",
                   "test_the_listing_says_how_many_are_waiting"],
    },
    {
        "label": "the listing does not say which turn a sentence was in",
        "file": _SKILL,
        "old": _NO_TURN[0],
        "new": _NO_TURN[1],
        "why": "Agreeing needs the context the sentence was read out of. "
               "`source_index` is zero-based and this is the only place the +1 "
               "happens for this surface.",
        "expect": ["test_the_listing_says_where_each_sentence_came_from"],
    },
    {
        "label": "the listing does not say which conversation a sentence is from",
        "file": _SKILL,
        "old": _NO_SESSION[0],
        "new": _NO_SESSION[1],
        "why": "`session_id` is the argument `keep` and `discard` both take "
               "back, so a listing that omits it cannot be acted on without "
               "the person guessing - and the guess is a memory.",
        "expect": ["test_the_listing_says_where_each_sentence_came_from"],
    },
    {
        "label": "the listing does not say that nothing is stored until asked",
        "file": _SKILL,
        "old": _NO_TAIL[0],
        "new": _NO_TAIL[1],
        "why": "The sentence that makes the list safe to read aloud is the one "
               "that says what to do next. Without it a listing reads as a "
               "summary of what Chronoa knows.",
        "expect": ["test_the_listing_says_where_each_sentence_came_from"],
    },

    # --- the shared-acceptor promise, both directions -------------------
    {
        "label": "the skill grows its own decision",
        "file": _SKILL,
        "old": "    outcome, reason = memory.decide_candidate(\n",
        "new": "    memory.store_fact(memory._fact_from_candidate(row, row.session_id,\n"
               "                                          row.index), None)\n"
               "    outcome, reason = memory.decide_candidate(\n",
        "why": "Not a plausible edit so much as the documented one: a second "
               "door is the pair that drifts, and a skill that reaches into "
               "another module's private `_fact_from_candidate` is writing to "
               "durable memory without the digest being told.",
        "expect": ["test_the_skill_calls_the_shared_acceptor_rather_than_its_"
                   "own"],
    },
]