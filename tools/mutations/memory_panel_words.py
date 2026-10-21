"""Mutation set for the page that says what Chronoa remembers.

**The group heading was the registry's id** - "audio", "boots",
"coredumps" - and a person who has never met the registry cannot tell
what "coredumps" is. The Senses panel calls each sense something, and
the same table is used here.

**And the page's name said "Memory"**, which names a store rather than
an answer, and **the count called the readings facts** - the page holds
the audio sense's reading of the devices as much as it holds anything
anybody told Chronoa.

The set is the ways each of those can come back.
"""

TEST_FILE = "tests/test_surface_memory.py"

_MEMORY = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/memory.py"

MUTATIONS = [
    {
        "file": _MEMORY,
        "old": 'TITLE = "What it remembers"\n',
        "new": 'TITLE = "Memory"\n',
        # The name is the question the page is opened for.
        "label": "the memory page is called Memory again",
        "expect": ["test_it_exports_the_three_names_the_registry_reads"],
    },
    {
        "file": _MEMORY,
        "old": "            group = common.group(capabilities.sense_title(sense),\n",
        "new": "            group = common.group(sense,\n",
        # The heading is the code's word again.
        "label": "the group heading is the sense's registry id",
        "expect": ["test_a_group_is_headed_by_the_sense_the_person_knows"],
    },
    {
        "file": _MEMORY,
        "old": "f\"{plural(len(percepts), 'thing')} remembered\",",
        "new": "f\"{plural(len(percepts), 'fact')} remembered\",",
        # The count is right but the word is the registry's, and the page
        # holds readings as well as facts.
        "label": "the memory page counts facts when it holds readings too",
        "expect": ["test_the_count_says_how_many_things_are_remembered"],
    },
]
