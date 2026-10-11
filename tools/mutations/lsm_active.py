"""Mutation set for the absent-vs-empty `lsm_active` fix.

**`/sys/kernel/security/lsm_active` does not exist on this machine** - measured,
the directory holds `apparmor evm ima integrity lockdown lsm tpm0` and nothing
else. So the reader records no `active` key, and the renderer's
`not lsms.get("active")` was also true for that case. The sense therefore
printed *"no LSM is active: a configured LSM is not an enforcing one without a
policy attached"* on a machine with Landlock, AppArmor, IMA and EVM loaded -
which is the exact collapse `read_lsms`'s own docstring claims to have fixed,
reintroduced one function later.

The second half of this set is the one worth reading: adding the `runtime` key
made the record **non-empty on a machine that read nothing at all**, so the
container branch keyed on `not lsms` stopped firing and a container reported
`determined: True` with nothing behind it. An existing test caught it; the
mutation below is that exact regression, kept so it cannot come back quietly.
"""

from __future__ import annotations

TEST_FILE = "tests/test_sense_security.py"

_SEC = "usr/lib/shani-chronoa/shani_chronoa/senses/security.py"

MUTATIONS = [
    # ---- the collapse itself -------------------------------------------
    {
        "label": "an absent lsm_active is read as an empty one again",
        "file": _SEC,
        "old": '    if lsms.get("configured") and lsms.get("active") == []:',
        "new": '    if lsms.get("configured") and not lsms.get("active"):',
        "expect": ["test_an_absent_interface_is_not_reported_as_nothing_active",
                   "test_a_present_and_empty_interface_still_says_nothing_is_active"],
    },
    {
        "label": "the 'could not be read' branch is never reached",
        "file": _SEC,
        'old': '    if lsms.get("configured") and "active" not in lsms:',
        "new": '    if False:',
        "expect": ["test_an_absent_interface_is_not_reported_as_nothing_active",
                   "test_no_runtime_entries_says_nothing_rather_than_counting_zero"],
    },
    {
        "label": "the metadata count reads 0 for 'could not ask'",
        "file": _SEC,
        'old': '            "lsms_active": (len(lsms["active"]) if "active" in lsms else None),',
        "new": '            "lsms_active": len(lsms.get("active", [])),',
        "expect": ["test_the_metadata_count_is_none_not_zero"],
    },
    {
        "label": "the metadata reports the runtime count as the active count",
        "file": _SEC,
        "old": '            "lsms_active": (len(lsms["active"]) if "active" in lsms else None),',
        "new": '            "lsms_active": len(lsms.get("runtime", [])),',
        "expect": ["test_the_metadata_count_is_none_not_zero"],
    },

    # ---- the runtime list must not become a count ----------------------
    {
        "label": "every configured LSM is reported as instantiated",
        "file": _SEC,
        "old": '    record["runtime"] = sorted(name for name in record.get("configured") or []\n'
               "                               if (_SECURITY / name).exists())",
        "new": '    record["runtime"] = sorted(record.get("configured") or [])',
        "expect": ["test_the_runtime_list_names_only_what_exists",
                   "test_the_metadata_count_is_none_not_zero"],
    },
    {
        "label": "the runtime list counts an LSM with no interface as inactive",
        "file": _SEC,
        "old": '    record["runtime"] = sorted(name for name in record.get("configured") or []\n'
               "                               if (_SECURITY / name).exists())",
        "new": '    record["runtime"] = sorted(name for name in record.get("configured") or []\n'
               "                               if not (_SECURITY / name).exists())",
        "expect": ["test_the_runtime_list_names_only_what_exists"],
    },
    {
        "label": "the floor is presented as a count",
        "file": _SEC,
        "old": '                f"  {len(runtime)} of them have a runtime interface in "\n'
               '                f"securityfs: {\', \'.join(runtime)} - that is a floor, not a "\n'
               '                "count, because an LSM with no runtime state creates none "\n'
               '                "whether or not it is enforcing"',
        "new": '                f"  {len(runtime)} of them are enforcing: "\n'
               '                f"{\', \'.join(runtime)}"',
        "expect": ["test_the_runtime_list_is_a_floor_and_says_so"],
    },

    # ---- the container regression the `runtime` key introduced --------
    {
        "label": "a machine that read nothing is treated as having read something",
        "file": _SEC,
        "old": '    if not boot.get("efivars_present") and tpm is None and not (\n'
               '            lsms.get("configured") or "active" in lsms):',
        "new": '    if not boot.get("efivars_present") and not lsms and tpm is None:',
        # **The bug this key actually introduced.** `{"runtime": []}` is a
        # non-empty dict, so `not lsms` was False and every container stopped
        # being recognised as one. Asserted through the real `_run` by the
        # pre-existing container test, which is why it was caught at all - a
        # record-shape assertion would pass against both branches.
        "expect": ["test_a_container_reads_nothing_and_says_so"],
    },
]