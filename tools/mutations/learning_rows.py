"""Mutation set for the Learning panel's rows saying what a person would say.

**The panel used to be titled in the learning layer's own vocabulary** -
"A distilled router", "The outcome model", "Does the bandit beat chance"
- and a row title in a panel anyone can open is not the place for a
module's name. The titles are plain words now, and the internal name is
on the hover.

The set is the three ways that can come apart: a title that says the
internal name again, a row that has lost its mapping, and the two names
kept in two lists.
"""

TEST_FILE = "tests/test_the_learning_rows_are_plain_words.py"

_LEARNING = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/learning.py"

MUTATIONS = [
    {
        "file": _LEARNING,
        "old": '    ("Which skill to use", "`distill.load_router()`"),\n',
        "new": '    ("A distilled router", "`distill.load_router()`"),\n',
        # The row title is the learning layer's own name again, so a person
        # reading "What it has learned" is asked to know what a distilled
        # router is.
        "label": "a row title says the internal name again",
        "expect": ["test_no_row_title_carries_an_internal_name"],
    },
    {
        "file": _LEARNING,
        "old": '    ("Whether a call will work", "the outcome model"),\n',
        "new": '    ("Whether a call will work", ""),\n',
        # The row has lost its mapping: the plain title is there and
        # nothing says what it is called in the code.
        "label": "a row does not say what it is called in the code",
        "expect": ["test_every_row_carries_what_it_is_called_in_the_code",
                   "test_the_internal_name_names_something"],
    },
    {
        "file": _LEARNING,
        "old": "ROW_TITLES = tuple(title for title, _in_rows in ROWS)\n",
        "new": 'ROW_TITLES = (\n'
               '    "Facts remembered",\n'
               '    "Which tools worked",\n'
               '    "What answered what",\n'
               '    "Finding something you said before",\n'
               '    "Which skill to use",\n'
               '    "The outcome model",\n'
               ')\n',
        # The plain name and the internal name are two lists now, and they
        # already disagree - which is the shape this repository keeps
        # paying for: they agree on the day they are written and drift the
        # week after.
        #
        # **The literal is deliberately not the derived tuple.** A copy of
        # the same six strings would be a mutation that changes nothing, and
        # a mutation that cannot fail is not a mutation.
        "label": "the titles are a second hand-kept list",
        "expect": ["test_the_plain_name_and_the_internal_name_are_one_table"],
    },
    {
        "file": _LEARNING,
        "old": "        _add_row(have, _title, _sentence, tooltip=_internal)\n",
        "new": "        _add_row(have, _title, _sentence)\n",
        # The hover is not set, so the developer has no way to map a plain
        # row title back to the function it came from.
        "label": "the hover no longer says what the row is called",
        "expect": ["test_every_row_carries_what_it_is_called_in_the_code"],
    },
]
