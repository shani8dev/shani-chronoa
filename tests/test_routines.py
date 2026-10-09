"""A routine's phrase runs its saved request, through the normal turn.

Siri and Google Assistant both have this ("Good morning" -> weather, calendar,
news). Here the phrase is replaced by the saved request before the model sees
anything, so the routine can do no more than that request typed out.
"""

import asyncio

from shani_chronoa import routines
from shani_chronoa.assistant import Assistant
from shani_chronoa.skills import routines as skill

MORNING = "Tell me today's weather, what's on my calendar today and the top news headlines."


class _Echo:
    def __init__(self):
        self.asked = []

    async def chat_message(self, messages, tools=None):
        self.asked.append(next(m["content"] for m in reversed(messages) if m["role"] == "user"))
        return {"role": "assistant", "content": "ok"}


def _turn(text, tmp_path):
    llm = _Echo()
    asyncio.run(Assistant(llm, session_path=tmp_path / "s.jsonl").handle(text))
    return llm.asked[0]


def test_saving_and_saying_a_routine(tmp_path):
    said = skill._run({"action": "save", "name": "Good Morning", "request": MORNING,
                       "phrases": ["morning briefing"]})
    assert said.startswith("Saved") and "Nothing has run" in said
    for phrase in ("good morning", "Good morning!", "run my good morning routine", "morning briefing"):
        assert _turn(phrase, tmp_path).endswith(MORNING), phrase


def test_a_sentence_that_merely_contains_the_phrase_is_left_alone(tmp_path):
    skill._run({"action": "save", "name": "good morning", "request": MORNING})
    asked = _turn("is it a good morning for a walk?", tmp_path)
    assert MORNING not in asked and "walk" in asked


def test_without_routines_nothing_changes(tmp_path):
    """Control: no routine saved, the utterance reaches the model as said."""
    assert routines.expand("good morning") is None
    assert _turn("good morning", tmp_path).endswith("good morning")


def test_list_and_delete(tmp_path):
    skill._run({"action": "save", "name": "leaving work", "request": "Turn off the office lights and set DND off."})
    assert "leaving work" in skill._run({"action": "list"})
    assert skill._run({"action": "delete", "name": "leaving work"}).startswith("Deleted")
    assert routines.expand("leaving work") is None


def test_bad_arguments_are_sentences_not_errors():
    assert "needs the routine's name" in skill._run({"action": "save", "request": MORNING})
    assert "needs the request" in skill._run({"action": "save", "name": "x"})
    assert "must be one of" in skill._run({"action": "explode"})


def test_the_post_condition_reads_the_store_back_not_the_dict_the_handler_built(tmp_path):
    """A writer's failure mode is reporting success for a store it never wrote.

    So the check re-reads the file. Asserted in both directions - a routine that
    is there verifies, and one that is not does not - because the second
    direction is the one that makes the first meaningful. A post-condition
    hardcoded to `(True, ...)` passes the happy path and is why
    `test_postcondition_coverage.py`'s presence check is not enough on its own;
    mutation-checked (dropping the "not in stored" branch leaves this green
    without the delete case below).
    """
    skill._run({"action": "save", "name": "good morning", "request": MORNING})
    saved = skill.POST_CONDITION(
        {"action": "save", "name": "Good Morning", "request": MORNING}
    )
    assert saved is not None and saved[0] is True, (
        f"a routine that is in the store did not verify: {saved}"
    )

    skill._run({"action": "delete", "name": "good morning"})
    gone = skill.POST_CONDITION({"action": "save", "name": "good morning", "request": MORNING})
    assert gone is not None and gone[0] is False, (
        f"a routine that was deleted still verified as saved: {gone}"
    )


def test_a_delete_post_condition_follows_the_delete(tmp_path):
    """The other action the post-condition claims to check."""
    skill._run({"action": "save", "name": "leaving work", "request": MORNING})
    skill._run({"action": "delete", "name": "leaving work"})
    verdict = skill.POST_CONDITION({"action": "delete", "name": "leaving work"})
    assert verdict is not None and verdict[0] is True, verdict
    # And the failing direction: reporting a delete that did not happen.
    skill._run({"action": "save", "name": "leaving work", "request": MORNING})
    still = skill.POST_CONDITION({"action": "delete", "name": "leaving work"})
    assert still is not None and still[0] is False, (
        f"a routine that is still saved verified as deleted: {still}"
    )


def test_list_has_nothing_to_verify_and_says_none(tmp_path):
    """Nothing checkable, not a manufactured pass."""
    assert skill.POST_CONDITION({"action": "list"}) is None


def test_a_request_saved_as_something_else_is_not_verified(tmp_path):
    """The mismatch case, which is what a partial write looks like.

    A store that took the name but not the request is the plausible
    half-failure, and a post-condition that only checked for the name would call
    it a success.
    """
    routines.save_all({"good morning": {"request": "something else entirely", "phrases": []}})
    verdict = skill.POST_CONDITION(
        {"action": "save", "name": "good morning", "request": MORNING}
    )
    assert verdict is not None and verdict[0] is False, (
        f"a routine stored with a different request verified as correct: {verdict}"
    )
