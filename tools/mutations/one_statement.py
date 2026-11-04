"""Mutation set for one statement per page.

**A page that says "off" twice in two different words is harder to read
than a page that says it once.** (The Phone, Phone link and Android panels
this set began with moved to Shani Link on 2026-11-04, and their mutations
with them.)

The set is the two ways the page can go back to saying it twice.
"""

TEST_FILE = "tests/test_one_statement_per_page.py"

_EXPORT = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/export.py"
_MY_FILES = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/my_files.py"
_CALENDAR = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/calendar.py"

MUTATIONS = [
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
