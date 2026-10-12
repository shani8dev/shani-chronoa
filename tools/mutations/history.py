"""Mutation set for the durable-history read behind `weights_from_tracker`.

The defect: `weights_from_tracker` read the tracker's *ring*, which is
this-process memory and starts empty on every launch. So the learned tool
multipliers were `{}` until a hundred calls had accumulated in the current
process - measured, a fresh tracker over a directory holding 150 recorded calls
answered `get_calls() == []` and `weights_from_tracker() == {}`, while
`learning.load()` read all 150 from the same file. Every restart threw the
whole history away for the hot-path advisory, which is the outcome
`tool_select._tracker()`'s own docstring says it exists to prevent, reached by a
route it did not consider.

The ring was deliberately left alone: `settings_window/activity` renders those
rows marked `live=True` - "this process ran it" - so hydrating them would make
that panel claim a process executed calls it never saw. Two readers, two
histories.
"""

TEST_FILE = "tests/test_sleep_and_learning.py"

_LEARNING = "usr/lib/shani-chronoa/shani_chronoa/learning.py"

MUTATIONS = [
    {
        "file": _LEARNING,
        "old": "    records = _durable_records(tracker)\n",
        "new": "    records = None\n",
        # The original defect, restored: fall back to the ring, which is empty
        # at the start of every process.
        "label": "the restart discards the history again (the original bug)",
        "expect": ["test_a_restart_keeps_the_learned_weights",
                   "test_the_ring_stays_process_local",
                   "test_a_history_larger_than_the_limit_comes_from_the_end"],
    },
    {
        "file": _LEARNING,
        "old": "    lines = chunk.decode(\"utf-8\", \"replace\").splitlines()[-limit:]\n",
        "new": "    lines = chunk.decode(\"utf-8\", \"replace\").splitlines()[1:][-limit:]\n",
        # Found by running the tests, not by reading: the first version of
        # `_read_tail` partitioned at the first newline unconditionally, so it
        # dropped a valid record whenever the whole file fitted in the byte
        # budget. A one-record log read back zero records and every weight
        # vanished - the exact shape of the bug being fixed, reintroduced in its
        # own reader.
        "label": "the tail reader drops the first line even when it read the whole file",
        "expect": ["test_a_corrupt_line_is_skipped_rather_than_fatal",
                   "test_a_half_written_last_line_does_not_break_the_read",
                   "test_an_unchanged_log_is_not_reparsed"],
    },
    {
        "file": _LEARNING,
        "old": "    cached = _HISTORY_CACHE.get(key)\n    if cached is not None and cached[0] == (stat.st_size, stat.st_mtime):\n        return cached[1]\n",
        "new": "    cached = None\n",
        # The cache is the only thing that makes this affordable on a hot path.
        # Measured on the real 19 MB log: 16.3 ms to parse the tail, ~0.1 ms for
        # the cached read. Without it every tool selection re-parses.
        "label": "the per-revision cache never hits",
        "expect": ["test_an_unchanged_log_is_not_reparsed"],
    },
    {
        "file": _LEARNING,
        "old": "                if chunk.startswith(b\"\\n\"):\n                    chunk = chunk[1:]\n                else:\n                    _, _, chunk = chunk.partition(b\"\\n\")\n",
        "new": "                chunk = chunk[1:]\n",
        # **Equivalent, and kept rather than deleted.** Dropping the seek's first
        # byte and dropping the partial line it landed in are the same answer for
        # every input: a record missing its opening `{` does not parse, and the
        # reader already skips unparseable lines. Measured by running it - the
        # full class passes - and not argued, because the obvious-looking
        # difference ("one byte vs one line") is exactly the kind that reads as a
        # defect and is not one.
        #
        # The boundary it *would* differ on is a seek landing exactly on a record
        # start, where `partition` drops that whole record and `[1:]` corrupts
        # and then skips it. Both lose the same call.
        "label": "a partial first line is not dropped, only its first byte",
        "equivalent": True,
        "equivalent": True,
        "why": "a record with its first byte removed does not parse, and the "
               "reader already skips unparseable lines - so this reaches the "
               "same set of records by a different route.",
        "expect": [],
    },
    {
        "file": _LEARNING,
        "old": "    try:\n        path = tracker.log_file\n    except Exception:\n        return None\n",
        "new": "    try:\n        path = tracker.log_file\n    except Exception:\n        return []\n",
        # `None` and `[]` are different answers and the difference is the whole
        # design: None means "no durable history", so the ring is the history it
        # has; `[]` means "an empty history", which is a statement that nothing
        # has ever worked. Returning the second for the first is how a tracker
        # pointed at a directory that never wrote comes to be described as a
        # machine where every tool is unproven.
        "label": "no log is reported as an empty history rather than no history",
        "expect": ["test_a_tracker_that_cannot_name_its_log_falls_back_to_the_ring"],
    },
]
