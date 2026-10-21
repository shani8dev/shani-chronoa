"""Mutation set for the page that answers "what can this thing do?".

**The panel that lists the 260 capabilities was called "Skills"**, and the
panel that shows the body's organs was called "What Chronoa can do" - so
the name promised an answer the page did not give, and the answer itself
had no name.

**And its rows stopped quoting the model**: a row used to carry the
skill's own description - prose written for a model with the tool schema
in front of it - where a person needs to know *what it touches*.

**And a row with a question offers it** - the browsable page needs the
same door the palette has.

The set is the ways each of those can come back.
"""

TEST_FILE = "tests/test_what_chronoa_can_do.py"

_SKILLS = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/skills.py"
_INVENTORY = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/inventory.py"

MUTATIONS = [
    {
        "file": _SKILLS,
        "old": 'TITLE = "What Chronoa can do"\n',
        "new": 'TITLE = "Skills"\n',
        # The name is the answer to the question a newcomer arrives with, and
        # this is the page that gives it.
        "label": "the capabilities page is called Skills again",
        "expect": ["test_the_capabilities_page_is_called_what_chronoa_can_do"],
    },
    {
        "file": _INVENTORY,
        "old": 'TITLE = "The anatomy"\n',
        "new": 'TITLE = "What Chronoa can do"\n',
        # Two panels with the same title are two answers a reader cannot tell
        # apart - and this one shows the anatomy, not the capabilities.
        "label": "the anatomy page claims the capabilities' name",
        "expect": ["test_the_anatomy_page_says_it_is_the_anatomy",
                   "test_no_two_panels_answer_the_same_question"],
    },
    {
        "file": _SKILLS,
        "old": "        effect = capabilities.effect_of(name, description)\n"
               "        lines = [effect]\n",
        "new": "        lines = [description or \"(no description)\"]\n",
        # The model-facing prose is back in the row, and the one thing a
        # list cannot say - what it touches - is gone.
        "label": "the row quotes the model instead of saying what it touches",
        "expect": ["test_every_row_says_what_it_touches",
                   "test_no_row_quotes_the_description_the_model_is_handed"],
    },
    {
        "file": _SKILLS,
        "old": "        if prompt:\n",
        "new": "        if False:\n",
        # The browsable page has no way to try a thing, so a person who has
        # found one has to guess the wording of it.
        "label": "no row offers a question to try",
        "expect": ["test_a_row_with_a_question_offers_it",
                   "test_pressing_it_puts_the_question_in_the_composer"],
    },
    {
        "file": _SKILLS,
        "old": "            setter(prompt)\n",
        "new": "            setter(\"\")\n",
        # The button is there and puts nothing in the box: a control that
        # looks live and does nothing.
        "label": "the press puts nothing in the composer",
        "expect": ["test_pressing_it_puts_the_question_in_the_composer"],
    },
    {
        "file": _INVENTORY,
        "old": "        return _join(parts)\n",
        "new": "        return \" \".join(parts)\n",
        # The organ's own text does not end in a period, so joining the
        # parts with a space ran the two sentences together: "the log of it
        # Visible as the skin light while it happens".
        "label": "the anatomy row runs its two sentences together",
        "expect": ["test_the_anatomy_row_reads_as_two_sentences"],
    },
]
