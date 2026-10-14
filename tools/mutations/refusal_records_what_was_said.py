"""Mutations for "a refusal records what was actually said".

**The defect.** `ApprovalRequest` promised two things the shipped app did not
keep: `reject_line` said declining *"tells Chronoa why if you add a reason"*, and
`escape_line` said closing *"counts as no, never as yes"*. Measured through the
real window, the real presenter and the real `DECISION_TIMEOUT_SECONDS`:

| what the person did | `reasons()` | `rules()` |
|---|---|---|
| clicked "No, don't allow" | `{}` | `[('delete_file', path, 'deny_session')]` |
| **typed a reason, pressed Enter** | **`{}`** | `[('delete_file', path, 'deny_session')]` |
| **walked away / timed out** | `{}` | **`deny_session`** |

Two facts, and they are the same shape: **an answer the person did not give was
recorded as one they did** - once as a reason that was thrown away, once as a
session-wide denial nobody made.

The fix splits `NO_ANSWER` out of `DENY_SESSION`: `parse_reply()` returns it for
*nothing*, `Reply.was_answered` is the predicate, `decide()` writes no rule for it,
and `permits()` still refuses and says *not answered* rather than claiming a
denial.

Every mutation here has an over-fix as its partner, because the split invites both:

| shipped defect | the over-fix beside it |
|---|---|
| a dismissal wrote `deny_session` | `is_refusal`/`was_answered` drop `NO_ANSWER`, so silence stops refusing |
| a reason was dropped at the parser | a click records its own button label as the reason |
| `permits()` faked a denial for it | `permits()` lets a `NO_ANSWER` rule **through** |
| - | the feedback branch stops recording a refusal, because no words arrived |

## Three things this set is careful about

**Every mutation is the shipped defect or its over-fix, restored verbatim.** A
mutation that is only a plausible reading of the code is a mutation of something
nobody shipped.

**`is_refusal` and `was_answered` have no production caller.** Measured: `grep -rn
'is_refusal\\|was_answered' usr/` outside `permissions.py` returns nothing, and
inside it neither property is read. The refusal is made safe by `decide()`
returning `None` and by `permits()` failing closed - both of which are mutated
here - and these two properties are the *readable* form of that rule, tested
directly rather than reached through a caller. **That is recorded rather than
claimed as wiring**, the same way `planmode.refused_tools()` and
`model_registry.verify_digest` are.

**One witness is deliberately left off the reason mutation.** `parse_reply()`
keeps `message or head`; dropping `head` keeps everything *after* the separator,
so `test_a_reason_after_the_separator_still_round_trips` passes either way -
`encode_rejection()` is what the presenter uses, and it puts the reason on the
right of the separator. Naming it would be the over-claim this repository records
for `asleep_state`.

## Running it

```
python3 tools/mutate.py refusal_records_what_was_said
```

On a **snapshot**, never the shared tree: `ROOT` is the working directory when it
holds `tools/mutations`, so running from inside a copy makes the copy what gets
mutated. A mutation run that rewrites product source here has shipped another
session's defect three times.

The real-window witnesses need a display. Without one they **skip**, and the
runner says so on the survivor line rather than reading the skip as a pass.
"""

from __future__ import annotations

_PERM = "usr/lib/shani-chronoa/shani_chronoa/permissions.py"

TEST_FILE = "tests/test_a_refusal_records_what_was_actually_said.py"

MUTATIONS = [
    # -- the shipped defects, restored verbatim ------------------------------
    {
        "file": _PERM,
        "old": """    if reply.reply == Decision.NO_ANSWER:""",
        "new": """    if False:""",
        "label": "**the shipped defect, restored**: a prompt nobody answered "
                 "writes a session-wide denial, so a two-minute absence costs "
                 "the tool for the rest of the session",
        "expect": [
            "test_nothing_at_all_records_nothing",
            "test_a_non_string_answer_records_nothing",
            "test_the_next_attempt_after_a_dismissal_asks_again",
            "test_a_dismissal_refuses_and_records_nothing",
        ],
    },
    {
        "file": _PERM,
        "old": """        if not isinstance(answer, str) or not answer.strip():
            return Reply(Decision.NO_ANSWER, "")""",
        "new": """        if not isinstance(answer, str) or not answer.strip():
            return Reply(Decision.DENY_SESSION, "")""",
        "label": "the parser reads nothing as a session denial again, which is "
                 "what it did before the split - the same defect by a second "
                 "route, since `decide()` would still be reachable with a "
                 "presenter that returns the wrong type",
        "expect": [
            "test_nothing_at_all_is_no_answer",
            "test_nothing_at_all_records_nothing",
            "test_a_non_string_answer_records_nothing",
            "test_the_next_attempt_after_a_dismissal_asks_again",
            "test_a_dismissal_refuses_and_records_nothing",
        ],
    },
    {
        "file": _PERM,
        "old": """    return Reply(Decision.DENY_SESSION, message or head)""",
        "new": """    return Reply(Decision.DENY_SESSION, message)""",
        "label": "**the shipped defect, restored**: a reason typed or spoken "
                 "instead of tapped is dropped, which is why `reasons()` was "
                 "`{}` after every real refusal - the prompt promised a reason "
                 "and the composer was the only place to give one",
        # **Not `test_a_reason_after_the_separator_still_round_trips`:** it goes
        # through `encode_rejection()`, which puts the reason *after* the
        # separator, so `message` is already it and the test passes either way.
        # Naming it would be an over-claim; the runner's "NOT caught by" line is
        # what this note is standing in for.
        "expect": [
            "test_words_that_are_not_an_option_are_a_refusal_carrying_them",
            "test_a_typed_reason_is_recorded_as_a_reason",
            "test_the_reason_reaches_the_model_on_the_next_attempt",
            "test_a_typed_reason_reaches_the_prompt_and_then_the_model",
        ],
    },
    # -- the over-fix beside each --------------------------------------------
    {
        "file": _PERM,
        "old": """    if head == DENY_CHOICE:
        # A button. The reason is only what came after the separator, and an
        # empty one is the honest answer for a click - "No, don't allow" must
        # not become a reason saying "No, don't allow".
        return Reply(Decision.DENY_SESSION, message)""",
        "new": """    if head == DENY_CHOICE:
        return Reply(Decision.DENY_SESSION, message or head)""",
        "label": "over-fix: the fallback is applied to the option that **is** an "
                 "answer, so a plain click records \"No, don't allow\" as what "
                 "the user said",
        "expect": [
            "test_a_click_on_no_records_no_reason_of_its_own",
            "test_a_click_is_refused_and_recorded",
        ],
    },
    {
        "file": _PERM,
        "old": """        return self.reply in (Decision.DENY_ONCE, Decision.DENY_SESSION,
                              Decision.CANCEL, Decision.NO_ANSWER)""",
        "new": """        return self.reply in (Decision.DENY_ONCE, Decision.DENY_SESSION,
                              Decision.CANCEL)""",
        "label": "over-fix: `NO_ANSWER` is no longer a refusal, so silence stops "
                 "refusing the call - the safe direction made conditional on "
                 "whether somebody said no",
        # **Not `test_a_dismissal_still_refuses_the_tool_call`:** the refusal a
        # caller acts on is `decide()` returning `None`, not this property - which
        # has no production caller at all. See the module docstring.
        "expect": ["test_nothing_at_all_is_no_answer"],
    },
    {
        "file": _PERM,
        "old": """        return self.reply != Decision.NO_ANSWER""",
        "new": """        return True""",
        "label": "over-fix: everything counts as answered, so a silence is "
                 "remembered again - which is the defect wearing the safe "
                 "direction's clothes",
        "expect": ["test_nothing_at_all_is_no_answer"],
    },
    {
        "file": _PERM,
        "old": """    if decision == Decision.NO_ANSWER:""",
        "new": """    if False:""",
        "label": "over-fix: a `NO_ANSWER` rule is **allowed**, because the safe "
                 "direction is made to depend on who wrote the rule",
        "expect": [
            "test_a_hand_recorded_no_answer_still_refuses_and_says_what_it_is",
        ],
    },
    {
        "file": _PERM,
        "old": """            f"Permission for {action} on {resource} was not answered, so it is "
            f"not allowed this time and nothing was recorded against {action}. "
            "Ask the user what to do instead.\"""",
        "new": """            f"Permission denied for the rest of this session: a rule for "
            f"{action} on {resource} says deny_session. Do something else "
            "instead, or ask the user what to do instead.\"""",
        "label": "over-fix: an unanswerable prompt is reported to the model as a "
                 "denial the user gave - the defect itself, phrased about a rule "
                 "nobody made",
        "expect": [
            "test_a_hand_recorded_no_answer_still_refuses_and_says_what_it_is",
        ],
    },
    # -- the prompt's own promises -------------------------------------------
    {
        "file": _PERM,
        "old": """        return ("Closing this question without answering counts as no, never as "
                "yes - and nothing is remembered, so the next time asks again.")""",
        "new": """        return ("Closing this question without answering counts as no, never as "
                "yes.")""",
        "label": "the prompt drops the half that was false: it still says "
                 "closing is no, and no longer says that nothing is remembered",
        "expect": ["test_it_says_a_dismissal_is_not_remembered"],
    },
    {
        "file": _PERM,
        "old": """        return ("Saying no tells Chronoa why if you add a reason, so it can do "
                "something else instead - type or say it instead of tapping an "
                "answer. It does not end the conversation.")""",
        "new": """        return ("Saying no tells Chronoa why if you add a reason, so it can do "
                "something else instead. It does not end the conversation.")""",
        "label": "the prompt keeps promising a reason and stops saying **where** "
                 "to give one, which is what made the promise unreachable - the "
                 "question row is buttons and a button cannot carry a sentence",
        "expect": ["test_it_names_where_to_type_it"],
    },
    # -- the boundary the split invites, which must not move -----------------
    {
        "file": _PERM,
        "old": """            add_rule(action, pattern, Decision.DENY_SESSION, session_only=True)
            logger.info("Permission for %s %s refused (feedback dismissed)",""",
        "new": """            logger.info("Permission for %s %s refused (feedback dismissed)",""",
        "label": "over-reach: an empty feedback box stops recording the refusal, "
                 "because the rule this change draws is *no option chosen* "
                 "records nothing - and an option **was** chosen",
        "expect": [
            "test_a_chosen_option_is_remembered_even_when_its_text_never_arrives",
        ],
    },
]