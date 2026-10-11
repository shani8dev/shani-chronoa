"""Mutation set for the `security` sense's AppArmor half.

Separate from `process_cost.py` because **a set names one test file**: the two
halves of the matrix pass target different sources and different tests, and
folding them together would let a witness in one half report a pass the other
never earned.

The first two mutations are the mistake the whole measurement was taken to avoid.
**`aa-status --json` unprivileged prints `[]`** - measured, rc=4, with stderr
*"You do not have enough privilege to read the profile set."* - so an empty
list is what an *unreadable* answer looks like on this platform. Both mutations
below turn "undetermined" into a confident number, which is the same failure
with a different digit in it.
"""

from __future__ import annotations

TEST_FILE = "tests/test_sense_security_apparmor.py"

_SEC = "usr/lib/shani-chronoa/shani_chronoa/senses/security.py"

MUTATIONS = [
    {
        "label": "the loaded set is reported as the installed count",
        "file": _SEC,
        "old": '    record["loaded"] = None\n',
        "new": '    record["loaded"] = record.get("installed", 0)\n',
        "expect": ["test_loaded_is_none_and_never_a_count"],
    },
    {
        "label": "the loaded set is reported as zero rather than unknown",
        "file": _SEC,
        "old": '    record["loaded"] = None\n',
        "new": '    record["loaded"] = 0\n',
        "expect": ["test_loaded_is_none_and_never_a_count"],
    },
    {
        "label": "an unreadable module parameter is read as disabled",
        "file": _SEC,
        "old": '    if enabled is not None:\n        record["enabled"] = enabled.strip()',
        "new": '    record["enabled"] = (enabled or "N").strip()',
        "expect": ["test_an_unreadable_parameter_is_absent_not_false"],
    },
    {
        "label": "the abi directory is counted as a profile",
        "file": _SEC,
        "old": '                if entry.is_file() and not entry.name.startswith("abi"))',
        "new": "                if entry.is_file())",
        "expect": ["test_loaded_is_none_and_never_a_count"],
    },
    {
        "label": "a disabled module is reported as enabled",
        "file": _SEC,
        "old": '        if enabled == "Y":\n            lines.append("AppArmor: module enabled")',
        "new": '        if enabled is not None:\n            lines.append("AppArmor: module enabled")',
        # **The rendered sentence, not the record.** Widening `== "Y"` to
        # `is not None` makes a *disabled* module announce itself as enabled -
        # the exact direction the module docstring warns about, since a listed
        # LSM with no policy attached does nothing. The first version of this
        # mutation pointed at `test_a_disabled_module_is_reported_as_disabled`,
        # which reads the record and is therefore satisfied by a reader that is
        # still correct; it survived until a test drove the real `_run`.
        "expect": ["test_a_disabled_module_says_disabled_and_says_what_it_means"],
    },
]