"""Mutation set for `skills/file_locks.py`.

Each mutation restores a defect the tests are supposed to catch. The four
parsing mutations are the ones this module's docstring names explicitly -
device read as decimal, inode read as hex, inode-only matching, and WRITE/READ
swapped - because those are the ways a lock table gets confidently wrong.

`tools/mutate.py` refuses a mutation whose anchor does not occur exactly once,
so a rotated anchor is reported as an error rather than as a survivor.

**Witnesses are bare test-function names.** Every name here was first written as
`TestClass::test_name`, and the harness reported all thirteen as *survivors*
while their target tests were plainly failing - it matches a witness as a
**prefix** of the node id (`tests/test_file_locks.py::TestTheParse::test_x`),
and `TestTheParse::test_x` is not a prefix of that. The tests were right
throughout; the names were not addressable.

**The last mutation is an equal-result mutant, and it is documented rather
than hidden.** Reading a holder's fd table once per *lock* instead of once per
*holder* returns the same paths, so it is only observable in how many times
`/proc/<pid>/fd` is walked - which is the property the module's docstring gives
as the reason for the grouping. `test_one_fd_walk_answers_every_lock_that_
holder_carries` counts the walks, so the mutation is caught; without that test
it survives, and the reason it was a survivor the first time is that a test of
the *answer* cannot see the *cost*.
"""

from __future__ import annotations

TEST_FILE = "tests/test_file_locks.py"

SKILL = "usr/lib/shani-chronoa/shani_chronoa/skills/file_locks.py"

MUTATIONS = [
    # ---- the parse -------------------------------------------------------
    {
        "label": "the device is read as decimal instead of hex",
        "file": SKILL,
        "old": ["            major=int(dev[0], 16),",
                "            minor=int(dev[1], 16),"],
        "new": ["            major=int(dev[0]),",
                "            minor=int(dev[1]),"],
        "expect": ["test_the_device_is_hex_and_the_inode_is_decimal",
                   "test_the_zero_major_of_a_zero_device_line"],
    },
    {
        "label": "the inode is read as hex instead of decimal",
        "file": SKILL,
        "old": "            inode=int(dev[2]),",
        "new": "            inode=int(dev[2], 16),",
        "expect": ["test_the_device_is_hex_and_the_inode_is_decimal"],
    },
    {
        "label": "a lock is matched on the inode alone",
        "file": SKILL,
        "old": "    locks = [lock for lock in _locks()\n"
               "             if (lock.major, lock.minor, lock.inode) == key]",
        "new": "    locks = [lock for lock in _locks() if lock.inode == key[2]]",
        "expect": ["test_a_lock_on_a_different_inode_is_not_this_file",
                   "test_the_same_inode_on_a_different_device_is_not_this_file"],
    },
    {
        "label": "WRITE and READ are swapped",
        "file": SKILL,
        "old": '    writes = [lock for lock in locks if lock.access == "WRITE"]',
        "new": '    writes = [lock for lock in locks if lock.access == "READ"]',
        "expect": ["test_the_counts_are_write_first_and_name_the_split"],
    },
    {
        "label": "a line that does not parse raises instead of being skipped",
        "file": SKILL,
        "old": "    except (ValueError, IndexError):\n        return None",
        "new": "    except (ValueError, IndexError):\n        raise",
        "expect": ["test_a_line_that_is_not_a_lock_is_skipped_not_raised_over",
                   "test_one_bad_line_does_not_hide_the_others"],
    },
    {
        "label": "an unreadable lock table is reported as no locks at all",
        "file": SKILL,
        "old": "    except OSError:\n        return []\n    out: \"List[Lock]\" = []",
        "new": "    except OSError:\n        return None\n    out: \"List[Lock]\" = []",
        "expect": ["test_a_table_that_cannot_be_read_is_no_locks"],
    },

    # ---- the whole-file read lock that blocks a writer -------------------
    {
        "label": "only WRITE locks count as blocking again",
        "file": SKILL,
        "old": '    return lock.access == "WRITE" or _range(lock) == "the whole file"',
        "new": '    return lock.access == "WRITE"',
        "expect": ["test_a_whole_file_read_lock_counts_as_blocking",
                   "test_the_survey_lists_a_whole_file_read_lock_as_blocking"],
    },
    {
        "label": "a ranged read lock is called a save-blocker too",
        "file": SKILL,
        "old": '    return lock.access == "WRITE" or _range(lock) == "the whole file"',
        "new": '    return True',
        "expect": ["test_a_ranged_read_lock_is_not_called_a_save_blocker"],
    },

    # ---- the answer a person reads ---------------------------------------
    {
        "label": "an unreadable fd table is dropped instead of being named",
        "file": SKILL,
        "old": '                where = "(the holder\'s fd table is not readable)"',
        "new": '                where = "?"',
        "expect": ["test_an_unreadable_holder_keeps_pid_and_name_and_says_why"],
    },
    {
        "label": "an unlocked file is reported as simply unlocked",
        "file": SKILL,
        "old": '        return (f"No process holds a lock on {path}. The file is not "\n'
               '                "locked - if a write to it still fails, the cause is "\n'
               '                "elsewhere: permissions, or a read-only filesystem.")',
        "new": '        return f"No process holds a lock on {path}."',
        "expect": ["test_an_unlocked_file_says_where_to_look_instead"],
    },
    {
        "label": "the empty machine is reported as a failure to read the table",
        "file": SKILL,
        "old": '        return ("No process holds a lock anywhere on this machine. That "\n'
               '                "is the ordinary state of a quiet machine, not a "\n'
               '                "failure to read the lock table.")',
        "new": '        return "The lock table could not be read."',
        "expect": ["test_the_empty_machine_says_so_without_calling_it_a_failure"],
    },
    {
        "label": "the byte range is dropped from the blocking rows",
        "file": SKILL,
        "old": '                         f"pid {lock.pid}  {_range(lock)}  {where}")',
        "new": '                         f"pid {lock.pid}  {where}")',
        "expect": ["test_a_write_lock_is_listed_with_its_holder_and_range"],
    },

    # ---- the fd table ----------------------------------------------------
    {
        "label": "the fd table is read once per lock instead of once per holder",
        "file": SKILL,
        "old": "    for pid, group in by_pid.items():\n"
               "        paths = _fd_paths(pid)\n"
               "        for lock in group:\n"
               "            out[lock.index] = paths.get((lock.major, lock.minor,"
               " lock.inode))",
        "new": "    for lock in locks:\n"
               "        paths = _fd_paths(lock.pid)\n"
               "        out[lock.index] = paths.get((lock.major, lock.minor,"
               " lock.inode))",
        "expect": ["test_one_fd_walk_answers_every_lock_that_holder_carries"],
    },
]