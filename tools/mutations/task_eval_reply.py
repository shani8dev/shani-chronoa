"""Mutation set for what the model said when it did not act.

`score` reported `no tool` for every case with no tool call, which discards the
only evidence there was: "I can't tell you the time" and "Let me check that for
you" are both `no tool`, and they are opposite failures with opposite fixes.

Two of these mutations are about the empty state specifically, and the reason it
exists is a measurement rather than a hunch. **Qwen3 is a thinking model**: with
`max_tokens: 80` the ordinary eval case "What time is it?" came back with
`content == ""`, the budget spent on `reasoning_content` before it spoke. An empty
string contains no marker, so every other branch passes it through - as an
*answer*. heretic reaches the same conclusion from the other side, counting an
empty response as a match "to avoid optimizing for them"; here it is its own
state, because "did not speak", "declined to speak" and "spoke plainly" are three
different facts about a run.

The pinned-list mutations are here because the property tests iterate the
*current* list and are therefore blind to a marker being deleted. Deleting
`"i'll look"` left the file green until the lists were pinned: adding a phrase is
free, and removing one makes a decline read as an answer.
"""

TEST_FILE = "tests/test_task_eval.py"

_EVAL = "tools/task_eval.py"

MUTATIONS = [
    {
        "file": _EVAL,
        "old": "    if not flat:\n        return REPLY_EMPTY\n",
        "new": "    if False:\n        return REPLY_EMPTY\n",
        "label": "an empty reply is scored as an answer (a thinking model that ran out of budget)",
        "expect": ["test_an_empty_reply_is_empty_and_not_an_answer"],
    },
    {
        "file": _EVAL,
        "old": '    return " ".join((text or "").lower().replace("*", "").replace("\\u2019", "\'").split())\n',
        "new": '    return " ".join((text or "").lower().split())\n',
        "label": "normalisation dropped, so markdown emphasis and a curly quote hide the marker",
        "expect": ["test_emphasis_does_not_hide_the_marker",
                   "test_a_typographic_apostrophe_does_not_hide_the_marker"],
    },
    {
        "file": _EVAL,
        "old": '    "i\'m sorry, but", "i am sorry, but", "unfortunately, i",\n',
        "new": '    "a marker no reply contains",\n',
        "label": "three refusal phrases replaced by one that matches nothing",
        "expect": ["test_every_refusal_marker_is_exercised_by_something"],
    },
    {
        "file": _EVAL,
        "old": "    \"i don't have access\", \"i do not have access\",\n",
        "new": "",
        "label": "a refusal phrase is deleted outright (invisible to a property that iterates the list)",
        "expect": ["test_the_refusal_list_has_not_lost_a_phrase"],
    },
    {
        "file": _EVAL,
        "old": '    "i\'ll pull", "i will pull",\n',
        "new": "",
        "label": "a narration phrase is deleted outright",
        "expect": ["test_the_narration_list_has_not_lost_a_phrase"],
    },
    {
        "file": _EVAL,
        "old": "    if any(marker in flat for marker in _NARRATION_MARKERS):\n"
               "        return REPLY_NARRATION\n",
        "new": "    if False:\n        return REPLY_NARRATION\n",
        "label": "narration is gone, so promising and never acting reads as answering",
        "expect": ["test_a_narration_is_not_a_refusal",
                   "test_every_narration_marker_is_exercised_by_something"],
    },
    {
        "file": _EVAL,
        "old": "    if any(marker in flat for marker in _REFUSAL_MARKERS):\n"
               "        return REPLY_REFUSAL\n"
               "    if any(marker in flat for marker in _NARRATION_MARKERS):\n"
               "        return REPLY_NARRATION\n",
        "new": "    if any(marker in flat for marker in _NARRATION_MARKERS):\n"
               "        return REPLY_NARRATION\n"
               "    if any(marker in flat for marker in _REFUSAL_MARKERS):\n"
               "        return REPLY_REFUSAL\n",
        "label": "narration is checked first, so a refusal wearing a wrapper reads as a promise",
        "expect": ["test_a_refusal_wins_over_a_narration_in_the_same_reply"],
    },
    {
        "file": _EVAL,
        "old": '            "reply": "" if error else classify_reply(text),\n',
        "new": '            "reply": "",\n',
        "label": "the harness stops recording what was said",
        "expect": ["test_run_case_keeps_what_the_model_said"],
    },
]