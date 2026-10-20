"""Mutation set for the turn footer's door to the changes it lists.

**The footer said how many files a turn wrote and offered to put them
back, and there was no way to look at them from there.** The door
exists - a tool card carries "Show full diff" - but it is on a *card*,
and the footer is the line a person reads *after* the turn.

The set is the four ways that can break: the button not drawn, the
button drawn when there is nothing to open, and the button that does not
open.
"""

TEST_FILE = "tests/test_the_turn_footer_has_a_door_to_the_changes.py"

_WIDGETS = "usr/lib/shani-chronoa/shani_chronoa/gui/widgets.py"

MUTATIONS = [
    {
        "file": _WIDGETS,
        "old": "        show_diff = getattr(self, \"_on_show_diff\", None)\n"
               "        if changed and callable(show_diff):\n",
        "new": "        show_diff = getattr(self, \"_on_show_diff\", None)\n"
               "        if False and changed and callable(show_diff):\n",
        # The shipped behaviour: the footer lists the files and offers to
        # put them back, and looking at them is a card away.
        "label": "the footer has no door to the changes (the shipped behaviour)",
        "expect": ["test_the_button_is_there_when_the_turn_changed_files"],
    },
    {
        "file": _WIDGETS,
        "old": "        show_diff = getattr(self, \"_on_show_diff\", None)\n"
               "        if changed and callable(show_diff):\n",
        "new": "        show_diff = getattr(self, \"_on_show_diff\", None)\n"
               "        if callable(show_diff):\n",
        # The over-fix's opposite: a button on every turn, opening a panel
        # that lists nothing, because that turn changed nothing.
        "label": "the button is drawn on a turn that changed nothing",
        "expect": ["test_no_button_when_the_turn_changed_nothing"],
    },
    {
        "file": _WIDGETS,
        "old": "        show_diff = getattr(self, \"_on_show_diff\", None)\n"
               "        if changed and callable(show_diff):\n",
        "new": "        show_diff = getattr(self, \"_on_show_diff\", None)\n"
               "        if changed:\n",
        # A button that cannot work: there is no handler, so the press
        # would raise or do nothing.
        "label": "the button is drawn with nothing to open",
        "expect": ["test_no_button_when_there_is_no_panel_to_open"],
    },
    {
        "file": _WIDGETS,
        "old": "            button.connect(\"clicked\", lambda *_: show_diff())\n",
        "new": "            button.connect(\"clicked\", lambda *_: None)\n",
        # The button is there, says the right thing, and does nothing -
        # which is the shape this file keeps finding: a control that
        # reports nothing when pressed.
        "label": "the button is drawn and does nothing when pressed",
        "expect": ["test_pressing_it_opens_the_panel"],
    },
]
