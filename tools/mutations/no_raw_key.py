"""Mutation set for a person-facing panel naming a gsettings key.

**A key is not a word any screen shows.** "Enable
'phone-control-enabled' in Settings" sends a person hunting for a switch
spelled a way no screen ever spells it - the row in front of them is
titled *"Let Chronoa use your paired phone"*, and that is the one owner
of the wording.

Measured 2026-10-20: nine of the 38 panels named a raw key on
screen, through five routes - a panel's own sentence, a skill's
refusal shown through a panel, a module's refusal, the anatomy's note,
and a table of switch names that had no rows to name them after.

The set is the ways the key can come back.
"""

TEST_FILE = "tests/test_no_panel_names_a_raw_key.py"

_SKILLS = "usr/lib/shani-chronoa/shani_chronoa/skills"
_SURFACES = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces"
_CAPS = "usr/lib/shani-chronoa/shani_chronoa/capabilities.py"

MUTATIONS = [
    {
        "file": f"{_SURFACES}/devices.py",
        "old": '    STATE_REFUSED: ("Open Privacy settings", "settings:privacy"),\n}\n\nBODY = {\n',
        "new": '    STATE_REFUSED: (\n        f"Turning it on affects what the assistant can do with your phone as "\n        f"well as this page, because \'{CONSENT_KEY}\' is off."\n    ),\n',
        # The raw key is back, in the one place the person reads.
        "label": "the Devices panel's message names the key again",
        "expect": ["test_no_panel_names_a_raw_key"],
    },
    {
        "file": f"{_SURFACES}/desktop.py",
        "old": '    return (f"{EVIDENCE_COULD_NOT_ASK} - the setting "\n            f"“{gate_label(SHORTCUT_KEY)}” says {said}. "\n',
        "new": '    return (f"{EVIDENCE_COULD_NOT_ASK} - the setting {SHORTCUT_KEY} says {said}. "\n',
        "label": "the Desktop panel's sentence names the key again",
        "expect": ["test_no_panel_names_a_raw_key"],
    },
    {
        "file": _CAPS,
        "old": '    "global-shortcut-enabled": "Talk to Chronoa with a keyboard shortcut",\n',
        "new": "",
        # The label is gone, so `gate_label` falls back to the key - and
        # every sentence that used it prints the key again.
        "label": "a switch name is dropped from the one table that holds them",
        "expect": ["test_no_panel_names_a_raw_key"],
    },
]
