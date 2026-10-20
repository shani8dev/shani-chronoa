"""Mutation set for the rule builder saying what a person would say.

**The arguments were JSON in the middle of a form.** Most rules need none
at all, and the field sat where it read like a required step - a person
writing "when the battery is low, notify me" had to know what a JSON
object was. So the JSON went behind an "Advanced" toggle.

**And a rule that passes no arguments said so.** `json.dumps({})` is
`"{}"`, and `"{}"` is a true string - so every rule with no arguments at
all drew a line reading *"arguments: {}"*, which is noise and JSON on a
row a person reads.

The set is the four ways this can be wrong: the empty case can come
back, the row can print JSON again, the field can go back into the
plain form, and the arguments can become unreachable.
"""

TEST_FILE = "tests/test_the_rule_builder_says_what_a_person_would_say.py"

_TRIGGERS = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/triggers.py"

MUTATIONS = [
    {
        "file": _TRIGGERS,
        "old": "    if not arguments:\n        return \"\"\n",
        "new": "",
        # The empty case renders as "{}" again, so every rule that passes no
        # arguments draws a line reading "arguments: {}".
        "label": "a rule with no arguments renders them anyway",
        "expect": ["test_a_rule_with_no_arguments_says_nothing_about_them"],
    },
    {
        "file": _TRIGGERS,
        "old": "        lines = [_detail(rule)]\n",
        "new": "        lines = [_detail(rule)]\n"
               "        arguments = _arguments_text(rule)\n"
               "        if arguments:\n"
               "            lines.append(f\"arguments: {arguments}\")\n",
        # The row's body prints the raw arguments again - a JSON object on a
        # row a person reads.
        "label": "the rule row prints its arguments as JSON",
        "expect": ["test_the_row_body_has_no_json"],
    },
    {
        "file": _TRIGGERS,
        "old": "        box.append(args_toggle)\n        box.append(args_revealer)\n",
        "new": "        box.append(args_inner)\n",
        # The field is back in the plain form, so it reads like a required
        # step again and the person has to know what a JSON object is.
        "label": "the JSON field is in the plain form again",
        "expect": ["test_the_json_is_behind_an_advanced_toggle"],
    },
    {
        "file": _TRIGGERS,
        "old": "            if arguments:\n"
               "                suffix.append(common.details_button(\n"
               "                    arguments, \"The options this rule passes, as JSON\"))\n",
        "new": "",
        # The arguments are in the tooltip and nowhere else - and a tooltip
        # never arrives on a touch screen.
        "label": "the arguments are in no visible control",
        "expect": ["test_the_arguments_are_reachable_behind_a_details_button"],
    },
    {
        "file": _TRIGGERS,
        "old": '        args_text = Gtk.Label(label="with arguments", xalign=0.0)\n',
        "new": '        args_text = Gtk.Label(label="with arguments (JSON)", xalign=0.0)\n',
        # The label says "JSON" in the form again, which is the half of the
        # complaint that a person reads first.
        "label": "the field says JSON in its label again",
        "expect": ["test_the_json_field_is_not_labelled_as_json_in_the_form"],
    },
]
