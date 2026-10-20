"""Mutation set for a skill refusal naming the switch, not its key.

**A key is not a word any screen shows.** "Enable
'phone-control-enabled' in Settings" sends the person looking for a
switch called *Let Chronoa use your paired phone* - which is what Settings
calls it.

**These refusals are read by a person twice over**: the model relays them
in chat, and a panel prints them as the reason it has nothing to show. So
the wording is the person's, and it is the Settings row's.
"""

TEST_FILE = "tests/test_the_skills_name_the_switch_not_the_key.py"

_SKILLS = "usr/lib/shani-chronoa/shani_chronoa/skills"

MUTATIONS = [
    {
        "file": f"{_SKILLS}/bluetooth_gatt.py",
        "old": '        return False, (f"Reading a Bluetooth device is turned off. Nothing was read. "\n                       f"Turn on \\u201c{gate_label(_CONSENT_KEY)}\\u201d in Settings "\n                       f"to allow it.")\n',
        "new": '        return False, (f"Reading a Bluetooth device is turned off. Nothing was read. "\n                       f"Enable \'{_CONSENT_KEY}\' in Settings to allow it.")\n',
        # The raw key is back, on the panel that prints this refusal as
        # its reason and on every answer the model relays.
        "label": "the bluetooth skill's refusal names the key again",
        "expect": ["test_the_bluetooth_skill_names_its_switch"],
    },
    {
        "file": f"{_SKILLS}/phone.py",
        "old": '        return False, (f"using your phone is turned off - turn on "\n                       f"\\u201c{gate_label(_CONSENT_KEY)}\\u201d in Settings")\n',
        "new": '        return False, (f"using your phone is turned off (enable "\n                       f"\'{_CONSENT_KEY}\' in Settings)")\n',
        # The raw key is back, on every answer the model relays and on
        # the panel that prints this refusal as its reason.
        "label": "the phone skill's refusal names the key again",
        "expect": ["test_the_phone_skill_names_its_switch"],
    },
    {
        "file": f"{_SKILLS}/android_device.py",
        "old": '        return False, (f"using your phone is turned off - turn on "\n                       f"\\u201c{gate_label(_CONSENT_KEY)}\\u201d in Settings")\n',
        "new": '        return False, (f"using your phone is turned off (enable "\n                       f"\'{_CONSENT_KEY}\' in Settings)")\n',
        "label": "the android skill's refusal names the key again",
        "expect": ["test_the_android_skill_names_its_switch"],
    },
    {
        "file": f"{_SKILLS}/calendar_events.py",
        "old": '        return False, (f"reading your calendar is turned off - turn on "\n                       f"\\u201c{gate_label(_CONSENT_KEY)}\\u201d in Settings")\n',
        "new": '        return False, (f"reading your calendar is turned off (enable "\n                       f"\'{_CONSENT_KEY}\' in Settings)")\n',
        "label": "the calendar skill's refusal names the key again",
        "expect": ["test_the_calendar_skill_names_its_switch"],
    },
]
