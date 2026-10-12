"""Mutation set for "nothing is permanently deleted".

Every mutation here removes one of the things that make the delete recoverable,
and every one of them is the shape of failure that would not announce itself:

- a caller who cannot find a trash **removing the file anyway**, which turns a
  recoverable delete into a permanent one at exactly the moment the mechanism
  cannot be checked;
- the post-condition going back to `not lexists`, which a refusal satisfies - so
  the false `VERIFIED` returns in the most destructive skill in the project;
- the `.trashinfo` record being dropped from the check, which leaves a stale
  record from an earlier same-named delete able to certify a refusal;
- the trash's own directory no longer being required to have changed, which is
  the third leg of the same discriminator.

**Why a mutation set rather than the negative control the test file already
carries.** `TestTheOriginalCannotPassThis` restores the *whole* original skill
and requires the file to go red, which proves the file as a whole holds the
decision in place. These are the per-guard versions: each one removes a single
decision and names the witness that dies with it, so a guard that is never
exercised is visible as a survivor rather than hidden inside a 11-failure count.

**Each anchor occurs exactly once** in its target, which the runner asserts
before writing - the `str.replace` that silently mutated nothing has bitten this
repository four times, and two of those four were a mutation reported as a
survivor.
"""

_DEL = "usr/lib/shani-chronoa/shani_chronoa/skills/delete_file.py"
_FILES = "usr/lib/shani-chronoa/shani_chronoa/files.py"

TEST_FILE = "tests/test_nothing_is_permanently_deleted.py"

MUTATIONS = [
    {
        "file": _DEL,
        "old": '    moved, why = files.trash(path)\n    if not moved:\n',
        "new": '    import shutil as _s, os as _o\n'
               '    if path.is_dir():\n'
               '        _s.rmtree(path)\n'
               '    else:\n'
               '        _o.remove(path)\n'
               '    return f"Deleted {path}.", ""\n'
               '    moved, why = files.trash(path)\n'
               '    if not moved:\n',
        "label": "the file is removed permanently when the trash cannot be reached",
        "expect": [
            "test_no_gio_is_a_refusal_rather_than_a_silent_permanent_delete",
            "test_a_file_is_in_the_trash_and_can_come_back",
        ],
    },
    {
        "file": _DEL,
        "old": '    if not (info / f"{target.name}.trashinfo").is_file():\n',
        "new": '    if False:\n',
        "label": "the restore record is no longer part of the check, so a stale "
                 "one can certify a refusal",
        "expect": [
            "test_a_file_in_the_trash_with_no_record_is_not_verified",
        ],
    },
    {
        "file": _DEL,
        "old": '    if not (contents / target.name).exists():\n',
        "new": '    if False:\n',
        "label": "a restore record with no file beside it is accepted",
        "expect": [
            "test_a_record_with_no_file_beside_it_is_not_verified",
        ],
    },
    {
        "file": _FILES,
        "old": '    if os.path.lexists(path):\n'
               '        return (False, "gio reported success but the path is still there")\n'
               '    return (True, "")\n',
        "new": '    return (True, "")\n',
        "label": "files.trash reports success without checking the path actually left",
        "expect": [
            "test_files_trash_checks_the_path_really_left",
        ],
    },
    {
        "file": _FILES,
        "old": '    if shutil.which("gio") is None:\n'
               '        return (False, "gio is not installed")\n',
        "new": '',
        "label": "no check that gio exists, so a missing binary is a traceback "
                 "instead of a refusal",
        "expect": [
            "test_no_gio_is_a_refusal_rather_than_a_silent_permanent_delete",
        ],
    },
]