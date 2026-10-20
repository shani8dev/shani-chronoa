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
_DEVICES = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/devices.py"
_MY_FILES = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/my_files.py"
_ANDROID = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/android.py"
_CALENDAR = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/calendar.py"

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
    {
        "file": _ANDROID,
        "old": '        if self.why and gate_label("phone-control-enabled") in self.why:\n            return generic or self.headline\n',
        "new": '',
        # The refused gate's own reason is the banner's sentence, so
        # printing it again is a second copy of it.
        "label": "the Android panel repeats the banner's reason",
        "expect": ["test_no_panel_repeats_a_switch_its_banner_already_named[android]"],
    },
    {
        "file": _MY_FILES,
        "old": '''        if not self._consent():
            # **The banner is this page's one statement about the gate.**''',
        "new": '''        if not self._consent():
            self._present(_consent_refusal(self._app))
            return
        if False:
            # **The banner is this page's one statement about the gate.**''',
        # The refusal is back, so the page says the gate three times.
        "label": "the My files panel's refusal is back",
        "expect": ["test_no_panel_repeats_a_switch_its_banner_already_named[my_files]"],
    },
    {
        "file": _CALENDAR,
        "old": 'CONSENT_DETAIL = (\n    "Nothing was read here. Who you meet, and where, is yours to keep or "\n    "share; turn it on and this page fills itself."\n)\n',
        "new": 'CONSENT_DETAIL = (\n    f"\\u201c{gate_label(CONSENT_KEY)}\\u201d is off, so nothing was read here. "\n    "Who you meet, and where, is yours to keep or share; turn it on and this "\n    "page fills itself."\n)\n',
        # The switch's name is back in the sentence the banner also
        # says, so the page says it twice.
        "label": "the Calendar panel's detail names the switch again",
        "expect": ["test_no_panel_repeats_a_switch_its_banner_already_named[calendar]"],
    },
]
