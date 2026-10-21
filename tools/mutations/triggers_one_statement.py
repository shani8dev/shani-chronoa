"""Mutation set for the Triggers page's one-statement-per-page shape.

**Nothing armed is said once.** The page used to say it four times: in
the status row, in a note for each kind of rule, and in the empty
state's own title - and the two notes named a kind of rule in the
engine's word ("percept"), which is a word nobody outside this tree
knows.

The set is the ways the page can start saying it again, and the way it
can stop saying which kind is empty when that is the thing it has to
say.
"""

TEST_FILE = "tests/test_surface_triggers.py"

_PANEL = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/triggers.py"

MUTATIONS = [
    {
        "file": _PANEL,
        "old": "        if self._row_widgets or self._problems:\n"
               "            for kind in empty_kinds:\n"
               "                self._rules_box.append(_note(f\"No {kind.lower()} armed.\"))\n",
        "new": "        for kind in empty_kinds:\n"
               "            self._rules_box.append(_note(f\"No {kind.lower()} armed.\"))\n",
        # The note is back for the case it was suppressing: the page says
        # which kind is empty even when nothing is armed at all.
        "label": "the page says which kind is empty even with nothing armed",
        "expect": ["test_the_page_does_not_say_which_kind_is_empty_when_nothing_is"],
    },
    {
        "file": _PANEL,
        "old": "            self.status_recorder.set(common.STATUS_OK)\n",
        "new": "            self._status_slot.append(self.status_recorder.row(\n"
               "                common.STATUS_OK,\n"
               "                \"No rules armed\"))\n",
        # The status row is back, so the page says "no rules armed" twice:
        # once in the row and once in the empty state's own title.
        "label": "the page says 'no rules armed' twice again",
        "expect": ["test_the_page_says_it_once"],
    },
    {
        "file": _PANEL,
        "old": "    (\"Sense rules\", \"trigger_rule_store\", RuleStore),\n",
        "new": "    (\"Percept rules\", \"trigger_rule_store\", RuleStore),\n",
        # The engine's word is on the page again, in the heading and in
        # the note that is derived from it.
        "label": "the page names the kind 'percept rules' again",
        "expect": ["test_the_empty_kind_is_named_the_way_the_page_names_it"],
    },
    {
        "file": _PANEL,
        "old": "        if self._row_widgets or self._problems:\n"
               "            for kind in empty_kinds:\n",
        "new": "        if False:\n"
               "            for kind in empty_kinds:\n",
        # The over-fix: the note never appears, so a page with one kind of
        # rule says nothing about the other kind being empty.
        "label": "the page never says which kind is empty",
        "expect": ["test_the_empty_kind_is_named_the_way_the_page_names_it"],
    },
]
