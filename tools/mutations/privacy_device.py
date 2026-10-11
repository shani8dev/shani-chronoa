"""Mutation set for `set_privacy`'s `device` argument - the dead control.

`device` was advertised in `set_privacy`'s schema since the skill was written,
and **no handler ever read it**. `_set_camera` acted on *every* camera whatever
was passed, so "turn off my external webcam and leave the built-in one alone"
disabled both and said the right thing had happened.

It was found by `test_no_dead_schema_parameters` - a registry-wide audit for
advertised parameters the handler never reads - which is the part worth keeping:
one dead control in 257 skills, on a privacy action, in the direction that does
the opposite of protecting.

The first mutation below is the original defect restored verbatim, and
`test_it_reaches_the_handler_from_the_dispatch_path` is its witness - a test of
`_select` alone would have stayed green for the same reason the parameter went
unread in the first place.
"""

from __future__ import annotations

TEST_FILE = "tests/test_skill_privacy.py"

_SKILL = "usr/lib/shani-chronoa/shani_chronoa/skills/privacy.py"

MUTATIONS = [
    # ---- the original defect -------------------------------------------
    {
        "label": "the call site drops the device argument again (the original bug)",
        "file": _SKILL,
        "old": '        result = _set_camera(action == "enable_camera",\n'
               '                             arguments.get("device"))',
        "new": '        result = _set_camera(action == "enable_camera")',
        # **A helper-level test cannot catch this**, which is exactly why the
        # parameter went unread for the life of the skill: `_select` was
        # correct and nothing called it with the argument.
        "expect": ["test_it_reaches_the_handler_from_the_dispatch_path"],
    },
    {
        "label": "a name that matches nothing acts on everything",
        "file": _SKILL,
        "old": '    if not matched:\n'
               '        known = ", ".join(f"{c[\'name\']} ({c[\'path\']})" for c in cameras)\n'
               '        return [], (f"No camera matches {wanted!r}. This machine has: {known}. "\n'
               '                    "Nothing was changed.")',
        "new": '    if not matched:\n'
               '        return cameras, ""',
        "expect": ["test_a_name_that_matches_nothing_refuses_and_changes_nothing",
                   "test_a_wrong_device_from_the_dispatch_path_refuses"],
    },
    {
        "label": "an unmatched name silently does nothing",
        "file": _SKILL,
        "old": '    if not matched:\n'
               '        known = ", ".join(f"{c[\'name\']} ({c[\'path\']})" for c in cameras)\n'
               '        return [], (f"No camera matches {wanted!r}. This machine has: {known}. "\n'
               '                    "Nothing was changed.")',
        "new": '    if not matched:\n'
               '        return [], ""',
        # The silent empty action: nothing changed, reported as success.
        "expect": ["test_a_name_that_matches_nothing_refuses_and_changes_nothing",
                   "test_a_wrong_device_from_the_dispatch_path_refuses"],
    },
    {
        "label": "the substring match is dropped, so 'logitech' never selects",
        "file": _SKILL,
        "old": '               or lowered in c["name"].lower()]',
        "new": '               or c["name"].lower() == lowered]',
        "expect": ["test_a_substring_of_the_driver_name_selects"],
    },
    {
        "label": "the node and path spellings are dropped",
        "file": _SKILL,
        "old": '    matched = [c for c in cameras\n'
               '               if c["node"] == wanted or c["path"] == wanted',
        "new": '    matched = [c for c in cameras\n'
               '               if False or False',
        "expect": ["test_the_node_name_selects",
                   "test_the_device_path_selects"],
    },
    {
        "label": "an empty string is read as a name that matches nothing",
        "file": _SKILL,
        "old": '    if not wanted:\n'
               '        return cameras, ""',
        "new": '    if device is None:\n'
               '        return cameras, ""',
        # **A documented equivalent.** `"" in "any camera name"` is True for
        # every camera, so the mutant's "not None" check hands an empty string
        # to the matcher and gets every camera back - the same outcome as the
        # omission, arrived at by an accident of substring semantics.
        #
        # It is kept rather than deleted because the accident is real: the
        # omission holds only as long as an empty substring matches everything,
        # which is one edit away from silently selecting nothing. The two tests
        # that name it (`device=""` and `device="   "`) pin the *behaviour*, and
        # `test_a_whitespace_value_is_the_omission` is the one a future reader
        # should not remove to make this green.
        "expect": [],
    },
    {
        "label": "the omission stops meaning every camera",
        "file": _SKILL,
        "old": '    if not device:\n'
               '        return cameras, ""',
        "new": '    if not device:\n'
               '        return [], ""',
        "expect": ["test_omitting_the_device_still_means_every_camera"],
    },
]
