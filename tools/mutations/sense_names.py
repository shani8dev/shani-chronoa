"""Mutation set for the sense rows a person reads.

**"audio", "boots", "coredumps" are the code's words.** The Senses
panel calls those senses "Audio devices", "Boot history" and "Crashed
programs" - the same switch, the same panel family, two spellings - and
a person who has never met the registry cannot tell what "coredumps" is.

**The id belongs in the tooltip**, where somebody who wants it can find
it - which is what the Senses panel already does.

The set is the ways the id can come back.
"""

TEST_FILE = "tests/test_no_panel_names_a_sense_by_its_id.py"

_PRIVACY = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/privacy.py"

MUTATIONS = [
    {
        "file": _PRIVACY,
        "old": '        row = _switch_row(sense_title(name), "" if key else\n',
        "new": '        row = _switch_row(sense_title(name), name if key else\n',
        # The id is back in the row a person reads, under a title that
        # already says what the sense is.
        "label": "the privacy panel subtitles a sense row with its id",
        "expect": ["test_no_panel_names_a_sense_by_its_registry_id[privacy]"],
    },
]
