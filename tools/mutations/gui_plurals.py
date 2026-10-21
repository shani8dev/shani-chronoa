"""Mutation set for the GUI's "2 thing(s)".

**"24 fact(s) remembered" is a placeholder, not English.** It is what
code writes when it cannot decide between one and many, and a person
reading it sees the machine's working rather than the answer they came
for. The same shape was written out in **seventy-five places** across
the GUI.

The set is the ways the placeholder can come back, and the ways the
one helper can stop being right.
"""

TEST_FILE = "tests/test_no_gui_string_says_two_of_anything.py"

_WORDS = "usr/lib/shani-chronoa/shani_chronoa/words.py"
_GUI = "usr/lib/shani-chronoa/shani_chronoa/gui"

MUTATIONS = [
    {
        "file": f"{_GUI}/surfaces/diff.py",
        "old": "f\"{plural(len(changed), 'file')} changed - ",
        "new": "f\"{len(changed)} file(s) changed - ",
        # The placeholder is back, in a sentence a person reads.
        "label": "the Changes panel says '2 file(s)' again",
        "expect": ["test_the_gui_says_how_many_things_rather_than_two_of_anything"],
    },
    {
        "file": f"{_GUI}/widgets.py",
        "old": "parts = [f\"{plural(len(self._turns) - index, 'turn')}\"]",
        "new": "parts = [f\"{len(self._turns) - index} turn(s)\"]",
        "label": "the turn footer says '2 turn(s)' again",
        "expect": ["test_the_gui_says_how_many_things_rather_than_two_of_anything"],
    },
    {
        "file": _WORDS,
        "old": "    if count == 1:\n        shown = word\n",
        "new": "    if count == 1:\n        shown = word + \"s\"\n",
        # The helper stops agreeing with itself about one.
        "label": "the helper says '1 facts'",
        "expect": ["test_the_helper_says_one_and_many_correctly"],
    },
    {
        "file": _WORDS,
        "old": "    elif word.endswith(\"y\") and not word.endswith((\"ay\", \"ey\", \"iy\", \"oy\", \"uy\")):\n"
               "        shown = word[:-1] + \"ies\"\n",
        "new": "",
        # An irregular plural becomes a wrong one: "entrys".
        "label": "the helper says '2 entrys'",
        "expect": ["test_the_helper_says_one_and_many_correctly"],
    },
]
