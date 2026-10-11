"""Mutation set for the conversation store's semantic floor.

`harness-study/HARVEST-2/E-memory-mcp-special.md` records mem0's "threshold gate
before combining" as a Chronoa gap - *"a correctness guard Chronoa's hybrid search
lacks (reciprocal rank has no such floor)"*. The claim is wrong: the floor has
existed all along and sits in the candidate path, exactly where mem0's does. What
Chronoa lacked was a test, so a floor nobody could delete was also a floor nobody
had.

These mutations are the three ways a floor quietly stops being a floor - removed,
opened all the way, closed all the way - plus the one that moves it out of the
candidate path and into the sort, which is the placement mem0's note is actually
about.
"""

TEST_FILE = "tests/test_search_meaning_floor.py"

_STORE = "usr/lib/shani-chronoa/shani_chronoa/conversation_store.py"

MUTATIONS = [
    {
        "file": _STORE,
        "old": "        if score >= MEANING_FLOOR:\n",
        "new": "        if True:\n",
        # The guard removed. A message semantically about something else that
        # happens to contain every keyword now reaches the fusion and is labelled
        # "words and meaning" - the sentence the floor exists to prevent.
        "label": "the semantic floor is removed",
        # `test_the_floor_is_applied_to_the_candidates_before_the_fusion` is
        # deliberately NOT a witness here, and the reason is worth recording: that
        # test sets the floor to 0.0 to show the label changes, and a guard
        # replaced with `if True` makes the floor moot - so the mutated code
        # behaves exactly as the unmutated one does under that substitution. It
        # cannot tell them apart, which makes it a witness that cannot fail.
        "expect": ["test_a_message_matching_every_keyword_is_not_labelled_a_meaning_match",
                   "test_an_unrelated_message_is_not_returned_at_all"],
    },
    {
        "file": _STORE,
        "old": "MEANING_FLOOR = 0.55\n",
        "new": "MEANING_FLOOR = 0.0\n",
        # The same defect reached through the constant rather than the comparison.
        "label": "the floor is opened all the way (0.55 -> 0.0)",
        "expect": ["test_a_message_matching_every_keyword_is_not_labelled_a_meaning_match",
                   "test_the_floor_is_a_number_strictly_between_zero_and_one"],
    },
    {
        "file": _STORE,
        "old": "MEANING_FLOOR = 0.55\n",
        "new": "MEANING_FLOOR = 1.0\n",
        # The floor closed all the way: cosine similarity is only 1.0 for an
        # identical vector, so the meaning half of hybrid search stops finding
        # anything at all and the panel silently becomes keyword-only.
        "label": "the floor is closed all the way (0.55 -> 1.0)",
        # Neither of the other two candidates is a witness, and both for the same
        # reason - a witness that passes for both the fixed and the broken code is
        # the "a control that cannot fail" trap:
        #   `test_a_close_message...` - NEAR is the query verbatim, so similarity
        #       is exactly 1.0 and `>= 1.0` still admits it.
        #   `test_the_keyword_only_message_is_past_the_floor_but_below_it` - it
        #       asserts 0.38 < floor, which is true for 1.0 as well.
        "expect": ["test_the_floor_is_a_number_strictly_between_zero_and_one"],
    },
    {
        "file": _STORE,
        "old": "        if score >= MEANING_FLOOR:\n            scored.append((rid, score))\n",
        "new": "        scored.append((rid, score))\n",
        # Removes the guard *and* the condition that keeps it in the candidate
        # path. Paired with the first mutation this is what "applied after the
        # fusion instead of before" would look like if it were built that way.
        "label": "no candidate is ever filtered",
        "expect": ["test_a_message_matching_every_keyword_is_not_labelled_a_meaning_match"],
    },
]
