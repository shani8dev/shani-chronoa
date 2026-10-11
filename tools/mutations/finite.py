"""Mutation set for the one-finite-check consolidation.

Ported from a `/tmp` harness on 2026-10-11. Two of its witnesses were equivalent
on the first run and are corrected here; see the notes on each.
"""

TEST_FILE = "tests/test_one_finite_check.py"

_SYSFS = "usr/lib/shani-chronoa/shani_chronoa/sysfs.py"
_LOC = "usr/lib/shani-chronoa/shani_chronoa/senses/location.py"
_MEDIA = "usr/lib/shani-chronoa/shani_chronoa/skills/media_control.py"
_CPU = "usr/lib/shani-chronoa/shani_chronoa/senses/cpu.py"
_SOURCES = "usr/lib/shani-chronoa/shani_chronoa/triggers/sources.py"

MUTATIONS = [
    # -- the shared predicate ------------------------------------------------
    {
        "file": _SYSFS,
        "old": "    if isinstance(value, bool) or not isinstance(value, (int, float)):\n        return False\n",
        "new": "",
        "label": "the bool/type check removed (False and True become 0.0 and 1.0)",
        "expect": ["test_a_thing_that_is_not_a_number_is_not_one"],
    },
    {
        "file": _SYSFS,
        "old": "    return value == value and value not in (float(\"inf\"), float(\"-inf\"))\n",
        "new": "    return True\n",
        "label": "the predicate accepts everything (NaN and inf pass)",
        "expect": ["test_a_thing_that_is_not_a_number_is_not_one",
                   "test_a_nan_in_a_file_is_none",
                   "test_an_infinity_in_a_file_is_none"],
    },
    {
        "file": _SYSFS,
        "old": "    return value if finite(value) else None\n",
        "new": "    return value\n",
        "label": "read_float stops refusing a NaN it read off disk",
        "expect": ["test_a_nan_in_a_file_is_none"],
    },
    {
        "file": _SYSFS,
        "old": "    raw = read_text(path)\n"
               "    if not raw:\n        return None\n"
               "    try:\n        value = float(raw.split()[field])",
        "new": "    raw = read_text(path)\n"
               "    try:\n        value = float(raw.split()[field])",
        "label": "read_float no longer treats an empty file as unmeasurable",
        # **Not the empty-file test:** `\"\".split()` raises IndexError, which the
        # `except` already covers, so the guard is equivalent for that input. The
        # witness is the *unreadable* path, where read_text returns None and
        # `None.split()` is an AttributeError.
        "expect": ["test_uptime_is_none_when_the_file_cannot_be_read"],
    },
    {
        "file": _SYSFS,
        "old": "    try:\n        value = float(raw.split()[field])\n    except (ValueError, IndexError):\n        return None\n",
        "new": "    try:\n        value = float(raw)\n    except (ValueError, IndexError):\n        return None\n",
        "label": "the field argument dropped (/proc/uptime would not parse)",
        "expect": ["test_a_two_column_file_takes_the_first",
                   "test_the_real_proc_uptime",
                   "test_a_field_other_than_zero"],
    },

    # -- call sites growing their own copy back ------------------------------
    {
        "file": _LOC,
        "old": "    return float(value) if sysfs.finite(value) else None\n",
        "new": "    if isinstance(value, bool) or not isinstance(value, (int, float)):\n"
               "        return None\n"
               "    if value != value or value in (float(\"inf\"), float(\"-inf\")):\n"
               "        return None\n"
               "    return float(value)\n",
        "label": "location grows its own copy back",
        "expect": ["test_location_uses_the_shared_check"],
    },
    {
        "file": _MEDIA,
        "old": "    from shani_chronoa import sysfs\n\n    if not sysfs.finite(value):\n"
               "        return None, f\"Could not use {raw!r} as a number of seconds.\"",
        "new": "    if value != value or value in (float(\"inf\"), float(\"-inf\")):\n"
               "        return None, f\"Could not use {raw!r} as a number of seconds.\"",
        "label": "media_control grows its own copy back",
        "expect": ["test_media_control_uses_the_shared_check"],
    },
    {
        "file": _SOURCES,
        "old": "    if not sysfs.finite(expires_at):\n"
               "        return _unavailable(EVENT_EXPIRY, subject, f\"the deadline at {path} is not a real instant\")\n",
        "new": "    if expires_at != expires_at or expires_at in (float(\"inf\"), float(\"-inf\")):\n"
               "        return _unavailable(EVENT_EXPIRY, subject, f\"the deadline at {path} is not a real instant\")\n",
        "label": "a fourth copy reappears in triggers/sources.py",
        "expect": ["test_no_third_copy_exists_anywhere"],
    },

    # -- the extracted call site ---------------------------------------------
    {
        "file": _CPU,
        "old": "    return sysfs.read_float(_UPTIME, 0)\n",
        "new": "    raw = sysfs.read_text(_UPTIME)\n"
               "    if not raw:\n"
               "        return None\n"
               "    try:\n"
               "        return float(raw.split()[0])\n"
               "    except (ValueError, IndexError):\n"
               "        return None\n",
        "label": "cpu.read_uptime stops using the shared reader",
        # A source-level witness, because the value is the same either way.
        "expect": ["test_uptime_reads_through_the_shared_reader"],
    },
    {
        "file": _CPU,
        "old": "    return sysfs.read_float(_UPTIME, 0)\n",
        "new": "    return sysfs.read_float(_UPTIME, 1)\n",
        "label": "read_uptime takes the idle column (doubles the machine's age)",
        "expect": ["test_uptime_against_the_real_file"],
    },
]
