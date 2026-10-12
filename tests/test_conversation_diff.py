"""What changed between two conversations.

The comparison is over **what the person asked**, because assistant text is not
comparable: the same question answered twice gives two different answers, so a
diff over that half reports every turn as new on every comparison. A panel that
says "everything is different" every time is a panel nobody opens twice.

Each case below has an answer that can be worked out by hand, because the
question "is this test right" is the one thing a diff test cannot answer about
itself.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import conversation_diff as diff_module  # noqa: E402


def _user(text):
    return {"role": "user", "content": text}


def _assistant(text):
    return {"role": "assistant", "content": text}


# -- normalisation ----------------------------------------------------------


def test_case_and_whitespace_do_not_make_a_new_question():
    """A diff that calls "What's the weather?" and "what's the weather ?"
    different teaches the reader to distrust it on the first comparison."""
    assert diff_module.normalise("What's  the   weather?") == \
        diff_module.normalise("what's the weather")


def test_trailing_punctuation_is_not_a_difference():
    assert diff_module.normalise("ship it.") == diff_module.normalise("ship it")
    assert diff_module.normalise("ship it,") == diff_module.normalise("ship it")


def test_a_wrapped_question_is_the_question_it_was_typed_as():
    assert diff_module.normalise("fix the build\nand rerun") == \
        diff_module.normalise("fix the build and rerun")


def test_normalising_something_that_is_not_text_is_empty_not_an_error():
    """`content` is whatever a skill wrote into the file. A dict there is a
    corrupt record, and the panel must survive it."""
    for value in (None, 42, {"a": 1}, ["x"], b"bytes"):
        assert diff_module.normalise(value) == "", value


def test_an_empty_question_is_dropped_rather_than_compared():
    """An empty user turn would otherwise be "a question both conversations
    asked", which is a claim about both of them."""
    assert diff_module.prompts([_user(""), _user("  "), _user("real")]) == ["real"]


# -- what is compared -------------------------------------------------------


def test_only_the_persons_questions_are_compared():
    """System, tool and assistant turns are excluded - the assistant half is
    the non-deterministic one, and a tool result is not a question."""
    messages = [
        {"role": "system", "content": "you are helpful"},
        _user("what changed"),
        _assistant("a lot"),
        {"role": "tool", "content": "file contents"},
    ]
    assert diff_module.prompts(messages) == ["what changed"]


def test_the_assistant_half_differing_does_not_make_the_conversation_differ():
    """**The reason this module compares prompts.** Two conversations that asked
    the same question and got different answers are the same conversation for
    the purpose of "have I asked this before"."""
    old = [_user("what changed"), _assistant("I changed three things.")]
    new = [_user("what changed"), _assistant("Three things changed.")]
    assert diff_module.diff_conversations(old, new).identical


def test_a_second_answer_is_not_the_same_conversation():
    """Counted, not compared as sets: asking one question twice is not the same
    as asking it once."""
    old = [_user("what changed")]
    new = [_user("what changed"), _user("what changed")]
    result = diff_module.diff_conversations(old, new)
    assert result.identical is False
    assert result.new_repeats == ("what changed",), result


# -- the diff itself --------------------------------------------------------


def test_questions_only_in_the_new_conversation_are_reported():
    old = [_user("fix the build")]
    new = [_user("fix the build"), _user("rerun the tests")]
    result = diff_module.diff_conversations(old, new)
    assert result.only_new == ("rerun the tests",), result
    assert result.only_old == (), result
    assert result.shared == ("fix the build",), result


def test_questions_only_in_the_old_conversation_are_reported():
    old = [_user("fix the build"), _user("rerun the tests")]
    new = [_user("fix the build")]
    result = diff_module.diff_conversations(old, new)
    assert result.only_old == ("rerun the tests",), result
    assert result.only_new == (), result


def test_two_identical_conversations_have_nothing_in_either_direction():
    messages = [_user("one"), _user("two")]
    result = diff_module.diff_conversations(messages, list(messages))
    assert result.identical
    assert result.shared == ("one", "two")
    assert result.old_count == result.new_count == 2


def test_two_empty_conversations_are_identical_and_not_mysterious():
    result = diff_module.diff_conversations([], [])
    assert result.identical
    assert result.old_count == result.new_count == 0


def test_an_empty_old_conversation_leaves_everything_new():
    result = diff_module.diff_conversations([], [_user("first question")])
    assert result.only_new == ("first question",), result
    assert result.only_old == ()


def test_disjoint_conversations_share_nothing():
    old = [_user("alpha")]
    new = [_user("beta")]
    result = diff_module.diff_conversations(old, new)
    assert result.shared == ()
    assert result.identical is False


def test_shared_questions_are_sorted_so_the_panel_is_stable():
    """A set of questions has no meaningful order, and an unstable panel looks
    broken between two otherwise identical runs."""
    first = [_user("zebra"), _user("apple")]
    second = [_user("apple"), _user("zebra")]
    assert diff_module.diff_conversations(
        first, second).shared == diff_module.diff_conversations(
            second, first).shared


def test_unique_questions_keep_the_order_they_were_asked_in():
    """Only the *shared* set is sorted. The order questions were asked in is
    the order of the work, and it survives - **on both sides.** The first
    version of this test checked `only_new` alone, so a mutation that sorted
    `only_old` passed: an asymmetry that is genuinely wrong, and invisible from
    one side.

    **The questions are deliberately not in alphabetical order.** The second
    version used "first, second, third", which *is* alphabetical - so sorting
    them changed nothing and the mutation passed a second time. A test whose
    data cannot distinguish the two behaviours is not a test of that behaviour.
    """
    asked = ["zebra", "apple", "mango"]
    for old, new in (([], [_user(t) for t in asked]),
                     ([_user(t) for t in asked], [])):
        result = diff_module.diff_conversations(old, new)
        side = result.only_new if new else result.only_old
        assert side == tuple(asked), side


def test_asking_the_same_questions_in_a_different_order_is_not_a_difference():
    """Order is preserved *within* a side, because the order of the questions
    is the order of the work - but a question asked earlier in one conversation
    and later in the other is still the same question. A panel that reported
    this as four changes would be reporting on a reordering, which is not what
    anyone opens this panel to find out."""
    old = [_user("alpha"), _user("beta")]
    new = [_user("beta"), _user("alpha")]
    result = diff_module.diff_conversations(old, new)
    assert result.only_old == () and result.only_new == (), result
    assert result.shared == ("alpha", "beta"), result.shared
    assert result.identical, (
        "the same two questions, only reordered, should compare as identical")


# -- repeats ----------------------------------------------------------------


def test_a_question_asked_three_times_is_reported_once_but_counted():
    old = [_user("why is this slow")] * 3
    result = diff_module.diff_conversations(old, [])
    assert result.old_repeats == ("why is this slow",), result
    assert result.old_count == 3, result


def test_repeats_are_ordered_by_how_often():
    old = [_user("a"), _user("a"), _user("b"), _user("b"), _user("b"), _user("c")]
    assert diff_module.repeats(diff_module.prompts(old)) == ["b", "a"]


def test_a_question_asked_once_is_not_a_repeat():
    assert diff_module.repeats(["only once"]) == []


def test_repeats_in_one_conversation_do_not_affect_the_other():
    old = [_user("a"), _user("a")]
    new = [_user("a")]
    result = diff_module.diff_conversations(old, new)
    assert result.old_repeats == ("a",)
    assert result.new_repeats == ()


# -- display ----------------------------------------------------------------


def test_a_short_question_is_shown_whole():
    assert diff_module.for_display("ship it") == "ship it"


def test_a_long_question_is_shortened_and_marked():
    """A row that ends mid-word with no mark reads as the whole question."""
    long = "x" * 300
    shown = diff_module.for_display(long)
    assert len(shown) < len(long)
    assert shown.endswith("…"), shown


def test_matching_never_uses_the_shortened_form():
    """Two questions sharing a long prefix are two different questions.
    Comparing truncated text would report them as one - a wrong answer rather
    than a rough one."""
    prefix = "please check the logs for " + ("detail " * 40)
    old = [_user(prefix + "the timeout")]
    new = [_user(prefix + "the crash")]
    result = diff_module.diff_conversations(old, new)
    assert result.shared == (), result
    assert len(result.only_old) == 1 and len(result.only_new) == 1


# -- robustness -------------------------------------------------------------


def test_records_that_are_not_dicts_are_skipped():
    """`load()` skips malformed lines already, but `diff_conversations` is
    callable with anything and must not raise on a stray list."""
    assert diff_module.prompts([None, "text", 7, _user("real")]) == ["real"]


def test_no_conversations_at_all_is_a_clean_empty_diff():
    assert diff_module.diff_conversations(None, None).identical


def test_malformed_messages_do_not_raise():
    messages = [{"role": "user"}, {"content": "no role"}, {"role": 7,
                                                           "content": "x"}]
    assert diff_module.diff_conversations(messages, []) .old_count == 0