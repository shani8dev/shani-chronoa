"""Mutation set for one statement per page.

**A page that says "off" twice in two different words is harder to read
than a page that says it once.** The two panels this set covers each had
a consent banner naming a switch, and something else on the same page
named it again: the Phone panel's status row, and the Phone link
panel's empty state.

The set is the two ways the page can go back to saying it twice.
"""

TEST_FILE = "tests/test_one_statement_per_page.py"

_PHONE = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/phone.py"
_PHONE_LINK = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/phone_link.py"
_EXPORT = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/export.py"

MUTATIONS = [
    {
        "file": _PHONE,
        "old": "            self.status_recorder.set(common.STATUS_OFF)\n",
        "new": "            self.set_status(common.STATUS_OFF, \"Using your phone is turned off\",\n"
               "                            \"Nothing was asked of your phone. In Settings, under \"\n"
               "                            f\"Privacy: \\\"{GATE_NAMES[CONTROL_KEY]}\\\".\")\n",
        # The status row names the switch the banner above already named, and
        # repeats its sentence word for word - so the page says "off" twice.
        "label": "the Phone panel's status row says it again",
        "expect": ["test_the_phone_panel_does_not_say_it_twice"],
    },
    {
        "file": _PHONE_LINK,
        "old": "                return nothing_state(\n"
               "                    \"phone-symbolic\", \"No phone found yet\",\n"
               "                    \"Nothing has been looked for yet.\")\n",
        "new": "                return nothing_state(\n"
               "                    \"phone-symbolic\", \"Talking to phones is turned off\",\n"
               "                    f\"Turn on “{common.consent_name(CONSENT_KEY)}” above first.\")\n",
        # The empty state points back at the banner that is already on screen
        # saying the same thing, with a button to press.
        "label": "the Phone link panel's empty state says it again",
        "expect": ["test_the_phone_link_panel_does_not_say_it_twice"],
    },
    {
        "file": _PHONE,
        "old": "            self.status_recorder.set(common.STATUS_OFF)\n",
        "new": "            self.status_recorder.set(common.STATUS_OFF)\n"
               "            self.status_box.append(self.status_recorder.row(\n"
               "                common.STATUS_OFF, \"Using your phone is turned off\",\n"
               "                \"Nothing was asked of your phone.\"))\n",
        # The row is back - but without naming the switch, so the page says
        # "off" twice in two different words rather than naming it twice.
        # The witness is the *sentence* check, not the name check: the row
        # repeats what the banner said, which is the same defect in
        # different words.
        "label": "the Phone panel's row is back beside the banner",
        "expect": ["test_no_panel_repeats_a_sentence_its_banner_already_said[phone]"],
    },
    {
        "file": _EXPORT,
        "old": '                 else "Nothing to export yet")))\n',
        "new": '                 else "Nothing to export yet"),\n'
               '                reason))\n',
        # The Export panel said its reason three times: in the banner, in
        # the status row's subtitle, and in the group's description below.
        "label": "the Export panel's status row repeats the reason",
        "expect": ["test_no_panel_repeats_a_sentence_its_banner_already_said[export]"],
    },
    {
        "file": _EXPORT,
        "old": '                      if not reason else ""),\n',
        "new": '                      if not reason else reason[0].upper() + reason[1:] + "."),\n',
        # The group's description is the third copy of the same reason.
        "label": "the Export panel's group repeats the reason",
        "expect": ["test_no_panel_repeats_a_sentence_its_banner_already_said[export]"],
    },
]
