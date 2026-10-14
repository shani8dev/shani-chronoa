"""Mutation set for `gate_label` and the sense refusal that names the row.

**The defect.** `config.sense_allowed_reason()` is the sentence a person reads
when Chronoa declines something, and it ended with the gsettings key spelled out
- *"the web sense is turned off (enable 'web-sense-enabled')"* - while the
Settings row is titled **"Web search"**. The instruction pointed at a spelling no
screen uses, and a person following it hunted for a switch that does not exist.
Measured 2026-10-14: 47 hand-written refusals named a key the same way across 20
files, and **36 of the 53 sense consent keys had no friendly label anywhere**.

**Why this is not a one-line fix.** `capabilities.GATE_NAMES` already mapped the
57 *tool* keys to their Settings row labels - 87 entries, zero unlabelled - and
reached nothing a person reads when they are refused, because
`Capability.gate_label()` is a *method* and the 46 call sites hold only a key.
The 36 sense keys with no tool of their own were not in that table at all, and
the table that did carry them (`SENSE_TITLES`) was trapped in a GTK-importing
module while the refusal is reached from a headless subprocess.

So the set has four shapes, not one:

| | what it restores |
|---|---|
| the shipped defect | the key named instead of the row |
| the over-fix | a humanised key for everything, so tool keys lose their Settings wording |
| the 36-key gap | the second table ignored, so the senses with no tool go raw |
| the punctuation | the trailing full stop, which doubles against every caller's own |

Each half is asserted in both directions, because either alone is satisfiable by
a sentence that names both things at once.

**The three instruments that disagreed with the bytes.** The first version of the
`SENSE_TITLES` mutation was anchored on a line that occurred twice, so the runner
refused it; the second was applied to `common.py` while the test read
`capabilities.py`, and reported a clean pass over code that had not changed; the
third read `str.count` on an anchor spanning a line boundary. Every anchor here
is asserted `count == 1` before it is applied, and the run is on a snapshot -
never the shared tree, which another session is committing to.
"""

TEST_FILE = "tests/test_a_refusal_names_the_row_not_the_key.py"

_CFG = "usr/lib/shani-chronoa/shani_chronoa/config.py"
_CAP = "usr/lib/shani-chronoa/shani_chronoa/capabilities.py"
_COMMON = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/common.py"
_SENSES = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/senses.py"

MUTATIONS = [
    # -- the shipped defect, restored verbatim ------------------------------
    {
        "file": _CFG,
        "old": "                f\"the {sense} sense is turned off. \"\n"
               "                f\"Switch on \\u201c{gate_label(key)}\\u201d in Settings to use it\")",
        "new": "                f\"the {sense} sense is turned off. \"\n"
               "                f\"Switch on \\u201c{key}\\u201d in Settings to use it\")",
        "label": "**the shipped defect, restored**: the refusal names the raw "
                 "gsettings key, which no settings window spells that way",
        "expect": ["test_the_label_is_present_and_the_key_is_not",
                   "test_a_tool_key_takes_the_settings_rows_own_wording",
                   "test_a_sense_with_no_tool_takes_the_panels_title"],
    },

    # -- the over-fix, in both directions -----------------------------------
    {
        "file": _CAP,
        "old": "    if key in GATE_NAMES:\n"
               "        return GATE_NAMES[key]\n",
        "new": "    if False:\n"
               "        return GATE_NAMES[key]\n",
        "label": "**the over-fix**: the Settings row's own wording is never used, "
                 "so every tool key falls through to the sense panel's title",
        "expect": ["test_the_settings_row_wins",
                   "test_a_tool_key_takes_the_settings_rows_own_wording"],
    },
    {
        "file": _CAP,
        "old": "    if key.endswith(\"-sense-enabled\"):\n"
               "        return sense_title(key[:-len(\"-sense-enabled\")])\n",
        "new": "    if False:\n"
               "        return sense_title(key[:-len(\"-sense-enabled\")])\n",
        "label": "**the 36-key gap, restored**: the sense panel's table is never "
                 "consulted, so every sense with no tool of its own goes raw",
        "expect": ["test_a_sense_key_is_resolved_by_the_panel_table",
                   "test_a_sense_with_no_tool_takes_the_panels_title",
                   "test_the_label_is_present_and_the_key_is_not"],
    },

    # -- an invented label is worse than an honest key -----------------------
    {
        "file": _CAP,
        "old": "    if key.endswith(\"-sense-enabled\"):\n"
               "        return sense_title(key[:-len(\"-sense-enabled\")])\n"
               "    return key",
        "new": "    if key.endswith(\"-sense-enabled\"):\n"
               "        return sense_title(key[:-len(\"-sense-enabled\")])\n"
               "    return key.replace(\"-\", \" \").replace(\"_\", \" \").capitalize()",
        "label": "a key neither table carries is **humanised rather than named** - "
                 "an invented label for a row that may not exist",
        "expect": ["test_a_key_neither_table_carries_falls_back_to_itself"],
    },
    {
        "file": _CAP,
        "old": "    if key.endswith(\"-sense-enabled\"):\n"
               "        return sense_title(key[:-len(\"-sense-enabled\")])\n",
        "new": "    if True:\n"
               "        return sense_title(key[:-len(\"-sense-enabled\")])\n",
        "label": "the suffix test is dropped, so a **bare module name** resolves "
                 "as a key and `sense_title` answers a question it was not asked",
        "expect": ["test_the_sense_suffix_is_what_reaches_the_second_table"],
    },
    {
        "file": _CAP,
        "old": "def sense_title(name: str) -> str:\n"
               "    \"\"\"The person-facing title for sense `name`.\"\"\"\n"
               "    return SENSE_TITLES.get(name) or (name.replace(\"-\", \" \").replace(\"_\", \" \").capitalize())",
        "new": "def sense_title(name: str) -> str:\n"
               "    \"\"\"The person-facing title for sense `name`.\"\"\"\n"
               "    return name",
        "label": "the panel's own table is bypassed, so every sense shows as its "
                 "module id - the `hwmon`/`cgroup` defect this table was written for",
        # **Not `test_a_tool_key_takes_the_settings_rows_own_wording` or
        # `test_the_label_is_present_and_the_key_is_not`:** every tool key is
        # resolved by `GATE_NAMES`, which this mutation leaves alone, so those
        # two pass either way. Naming them was an over-claim, and the runner's
        # "NOT caught by" line is what said so.
        "expect": ["test_a_sense_with_no_tool_takes_the_panels_title",
                   "test_a_sense_key_is_resolved_by_the_panel_table"],
    },

    # -- the punctuation -----------------------------------------------------
    {
        "file": _CFG,
        "old": "                f\"Switch on \\u201c{gate_label(key)}\\u201d in Settings to use it\")",
        "new": "                f\"Switch on \\u201c{gate_label(key)}\\u201d in Settings to use it.\")",
        "label": "**the trailing full stop, restored**: thirty-odd callers append "
                 "their own, so this doubles it - *\"...to use it.. It reveals\"*",
        "expect": ["test_no_trailing_full_stop_and_no_doubled_one",
                   "test_a_caller_composing_after_it_still_reads_as_one_sentence"],
    },

    # -- the sentence names what declined, not only what to do ----------------
    {
        "file": _CFG,
        "old": "                f\"the {sense} sense is turned off. \"\n"
               "                f\"Switch on \\u201c{gate_label(key)}\\u201d in Settings to use it\")",
        "new": "                f\"It is turned off. \"\n"
               "                f\"Switch on \\u201c{gate_label(key)}\\u201d in Settings to use it\")",
        "label": "the sentence stops naming the sense, so a person is told what "
                 "to switch on and not what declined",
        "expect": ["test_the_sentence_names_the_sense_as_well_as_the_row"],
    },
    # -- the consolidation, which has no behavioural witness -------------------
    {
        "file": _COMMON,
        "old": "from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore\n",
        "new": "from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore\n"
               "from shani_chronoa.capabilities import SENSE_TITLES  # noqa: F401\n",
        "label": "**a re-export in the old home** - the first version of this "
                 "change kept one so every caller worked; it is one line from a "
                 "second definition",
        # **Not `test_exactly_one_definition_in_the_shipped_package`:** a
        # re-export is still one *definition*, so the count stays 1 and that
        # witness passes either way. The runner's "NOT caught by" line is what
        # said so; the two guards catch different things and the one named here
        # is the one that bites.
        "expect": ["test_the_old_home_does_not_re_export_the_table"],
    },
    {
        "file": _COMMON,
        "old": "MAX_WRAP_CHARS = 56\n",
        "new": "SENSE_TITLES = {\"hwmon\": \"Fans and temperatures\"}\n"
               "MAX_WRAP_CHARS = 56\n",
        "label": "**a second copy of the table in the GTK module** - the `wpctl` "
                 "three-copies shape, and silent while the two still agree",
        "expect": ["test_exactly_one_definition_in_the_shipped_package"],
    },
    {
        "file": _SENSES,
        "old": "from shani_chronoa.capabilities import sense_title  # noqa: E402\n",
        "new": "from shani_chronoa.gui.surfaces import common  # noqa: E402\n"
               "from shani_chronoa.gui.surfaces.common import sense_title  # noqa: E402\n",
        "label": "a sense panel binds its own `sense_title` instead of the moved "
                 "one, so the two can drift apart",
        "expect": ["test_the_two_gui_call_sites_use_the_moved_table"],
    },
]
