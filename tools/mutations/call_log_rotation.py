"""Mutations for the call log's rotation and the reader that has to follow it.

**The failure this set exists to prevent is silent.** Rotation moves the oldest
records out of the active log. Everything that learns from this log - the
outcome model, the calibrations, the bandit, the lessons - reads it. A reader
that kept opening the active file alone would go on working perfectly while
seeing a shrinking slice of history, and nothing would report that records had
stopped arriving, only that fewer were there.

So the mutations are shaped around three ways that happens quietly:

1. **the reader stops following the archive** - the headline, and it produces a
   suite that is entirely green;
2. **the cap drops the wrong end.** Keeping the oldest and discarding the newest
   is the direction that looks like retention and behaves like data loss, because
   a reflex layer and a running assistant read the *tail*;
3. **the log is read as a directory of a fixed name**, which is what my own
   first version did - and it broke a real test with a real message ("no lessons
   yet") that pointed at the product rather than at the reader.

The rotation-order mutation is the one that loses data rather than hiding it:
a writer that appends and *then* rotates drops the record it had just written,
and the file is merely smaller, which no check in the tree would notice.
"""

TEST_FILE = 'tests/test_call_log_rotation.py'

_TT = 'usr/lib/shani-chronoa/shani_chronoa/tool_tracking.py'
_LEARN = 'usr/lib/shani-chronoa/shani_chronoa/learning.py'
_DREAM = 'usr/lib/shani-chronoa/shani_chronoa/dream.py'

MUTATIONS = [
    {
        'label': 'the reader follows the active log and ignores the archives',
        'why': '**the headline, and it is entirely silent.** Every consumer - the '
               'outcome model, the calibrations, the bandit, the lessons - would '
               'go on working while the log filled, and each would see a '
               'shrinking slice of history. Nothing reports that records stopped '
               'arriving; the report just gets quieter. This is the mutation the '
               'whole retention change could introduce, and it is the one the '
               'readers were wired for.',
        'expect': ['test_rotation_keeps_every_record',
                   'test_records_come_back_in_time_order',
                   'test_log_files_is_oldest_first_with_the_active_log_last'],
        'file': _LEARN,
        'old': '    sources = tool_tracking.log_files(Path(path) if path is not None\n'
               '                                      else Path(_log_path()))',
        'new': '    sources = [Path(path if path is not None else _log_path())]',
    },
    {
        'label': 'the tail reader follows the active log and ignores the archives',
        'why': 'the same loss on the hot path. `reorder()` reads this on every '
               'tool selection, so the learned weights would be computed from '
               'the newest chunk alone - and the module\'s own docstring says a '
               'fresh tracker answering "no durable history" is how that class of '
               'silent starvation happens.',
        'expect': ['test_the_tail_reader_sees_across_the_boundary',
                   'test_the_tail_reader_sees_across_the_boundary',
                   'test_a_missing_log_is_none_not_an_empty_list'],
        'file': _LEARN,
        'old': '    sources = list(reversed(tool_tracking.log_files(path)))',
        'new': '    sources = [path] if path.exists() else []',
    },
    {
        'label': 'rotation happens after the record is written',
        'why': '**the one that loses data rather than hiding it.** The record '
               'just appended goes into the file that is then renamed - so it '
               'survives - but the file it lands in is no longer the one the '
               'next write checks, and over a boundary the newest record can be '
               'left in an archive while the active log is reset. Rotating '
               'first is what makes "the active log is always the newest chunk" '
               'an invariant rather than a hope.',
        'expect': ['test_a_write_past_the_cap_rotates_first',
                   'test_rotation_does_not_split_a_record',
                   'test_records_come_back_in_time_order'],
        'file': _TT,
        'old': '        try:\n'
               '            if self.log_file.stat().st_size + len(line) > ACTIVE_MAX_BYTES:\n'
               '                rotate(self.log_file)\n'
               '        except OSError:\n'
               '            pass\n'
               '        fd = os.open(self.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)\n'
               '        with os.fdopen(fd, "a") as f:\n'
               '            f.write(line)',
        'new': '        fd = os.open(self.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)\n'
               '        with os.fdopen(fd, "a") as f:\n'
               '            f.write(line)\n'
               '        try:\n'
               '            if self.log_file.stat().st_size > ACTIVE_MAX_BYTES:\n'
               '                rotate(self.log_dir)\n'
               '        except OSError:\n'
               '            pass',
    },
    {
        'label': 'the oldest chunk is not unlinked before the shift',
        'why': 'EQUIVALENT, and the measurement is the reason rather than a '
               'shrug. **The shift loop\'s `os.replace` overwrites `.8`**, and '
               '`os.replace` is an atomic clobber, so the oldest generation is '
               'already gone by the time the explicit `unlink` would have run. '
               'Measured with the unlink deleted: twenty rotations give chunk '
               'counts `1 2 3 4 5 6 7 8 8 8 8 8 8 8 8 8 8 8 8 8` - byte-'
               'identical to the counts with it, and the directory still caps. '
               'Every witness in the file passes either way, which is the '
               'definition of an equivalent mutant.\n\n'
               'It is **kept** rather than deleted, because there is one input '
               'where it is not redundant: if `os.replace` onto `.8` fails - a '
               'read-only filesystem, a permissions problem, an unlinkable '
               'destination - then without the `unlink` the whole rotation '
               'aborts and the oldest chunk survives, while with it the '
               'destination is cleared first and the shift succeeds. That is '
               'defence in depth against an I/O error, the same shape as the '
               'bounds check in `_read_tail` that this repository already keeps '
               'for a reason it can name.\n\n'
               'The alternative was to delete it, and the reason that was not '
               'done is the `42/87/70` lesson: a mutation that survives tells '
               'you a fact about the code, and deleting the code would have '
               'thrown the fact away with the line that produced it.',
        'equivalent': True,
        'expect': [],
        'file': _TT,
        'old': '        try:\n'
               '            os.unlink(archive_path(active, ARCHIVE_KEEP))\n'
               '        except FileNotFoundError:\n'
               '            pass\n'
               '        except OSError as exc:\n'
               '            logger.warning("could not drop the oldest archive: %s", exc)',
        'new': '        pass',
    },
    {
        'label': 'the cap drops the newest chunk instead of the oldest',
        'why': '**the direction that looks like retention and is data loss.** A '
               'reflex layer and a running assistant read the tail, and a '
               'training run reads the body; discarding the newest is the one '
               'end where losing it makes every consumer worse as the log grows.',
        'expect': ['test_each_rotation_keeps_exactly_the_last_generations',
                   'test_log_files_is_oldest_first_with_the_active_log_last',
                   'test_a_gap_in_the_numbering_is_not_a_gap_in_the_records'],
        'file': _TT,
        'old': '        for n in range(ARCHIVE_KEEP - 1, 0, -1):\n'
               '            src = archive_path(active, n)\n'
               '            if src.exists():\n'
               '                os.replace(src, archive_path(active, n + 1))',
        'new': '        try:\n'
               '            os.unlink(archive_path(active, 1))\n'
               '        except OSError:\n'
               '            pass',
    },
    {
        'label': 'rotation takes no lock',
        'why': 'two processes each decide the log is full and each shift the '
               'chunks - one rename lands on top of the other and a chunk of '
               'the history is gone. Every skill call is its own process, so '
               'this is the ordinary case rather than an exotic one.',
        'expect': ['test_rotate_waits_for_a_lock_someone_else_holds'],
        'file': _TT,
        'old': '        fcntl.flock(fd, fcntl.LOCK_EX)',
        'new': '        pass',
    },
    {
        'label': 'the archived chunk is left at whatever mode it had',
        'why': 'the active log is created 0600 because it records what the '
               'person asked their machine to do, and an archive of it is the '
               'same records. A rotation that quietly widened the mode would be '
               'the first thing to change the file\'s reachability in its life.',
        'expect': ['test_the_archived_chunk_keeps_the_log_s_mode'],
        'file': _TT,
        'old': '        files.restrict_file(archive_path(active, 1))',
        'new': '        pass',
    },
    {
        'label': 'the size check that triggers rotation is removed',
        'why': 'the feature becomes a function nothing calls, and every test '
               'that calls `rotate()` directly stays green - the `refused_tools` '
               'and `Registry.register` shape.',
        'expect': ['test_a_write_past_the_cap_rotates_first',
                   'test_a_write_below_the_cap_does_not_rotate',
                   'test_the_cap_holds_far_more_than_a_month_of_use'],
        'file': _TT,
        'old': '            if self.log_file.stat().st_size + len(line) > ACTIVE_MAX_BYTES:\n'
               '                rotate(self.log_file)',
        'new': '            if False:\n'
               '                rotate(self.log_dir)',
    },
    {
        'label': 'the archives are found by globbing a fixed filename in a directory',
        'why': '**my own first version, and it broke a real test.** It took a '
               'directory and globbed `tool_calls.log*`, so any caller that '
               'pointed the log elsewhere had its records read as empty - and '
               'the failure read "no lessons yet", which points at the product '
               'rather than at the reader. Every shape-name filter lies.',
        'expect': ['test_a_renamed_logs_own_archive_is_found'],
        'file': _TT,
        'old': '        candidate = d / ("%s.%d" % (active.name, n))',
        'new': '        candidate = d / ("tool_calls.log.%d" % n)',
    },
    {
        'label': 'a gap in the chunk numbering stops the reader',
        'why': '`.2` and `.1` with no `.3` is the ordinary state after a quiet '
               'period - a rotation with nothing to rotate leaves the numbering '
               'sparse - and a reader that assumed contiguity would stop at the '
               'hole and report a history that is merely shorter.',
        'expect': ['test_a_gap_in_the_numbering_is_not_a_gap_in_the_records',
                   'test_the_number_of_chunks_is_bounded',
                   'test_a_gap_in_the_numbering_is_not_a_gap_in_the_records'],
        'file': _TT,
        'old': '    for n in range(ARCHIVE_KEEP, 0, -1):\n'
               '        candidate = d / ("%s.%d" % (active.name, n))\n'
               '        if candidate.exists():\n'
               '            out.append(candidate)',
        'new': '    for n in range(1, ARCHIVE_KEEP + 1):\n'
               '        candidate = d / ("%s.%d" % (active.name, n))\n'
               '        if candidate.exists():\n'
               '            out.append(candidate)',
    },
    {
        'label': 'the archives are listed newest first',
        'why': 'the order is what makes a plain concatenation chronological, and '
               'the module trains on examples in the order it reads them. '
               'Reversed, every time-ordered thing downstream - durations, '
               'recency, "what happened recently" - is answered backwards.',
        'expect': ['test_log_files_is_oldest_first_with_the_active_log_last',
                   'test_records_come_back_in_time_order'],
        'file': _TT,
        'old': '    if active.exists():\n        out.append(active)\n    return out',
        'new': '    if active.exists():\n        out.append(active)\n    return out[::-1]',
    },
    {
        'label': 'a log directory is rotated rather than refused',
        'why': '**A mistake I made while writing these tests, and a silent one.** '
               '`rotate()` was called with the *directory* at one point; the rename '
               'it performs would then move the whole directory to `<dir>.1` - no '
               'exception, no record kept, and a passing suite, because nothing was '
               'reading that path. The shipped tree has one call site and it is '
               'correct, which is not the same as the next caller being unable to '
               'get it wrong.',
        'expect': ['test_a_log_directory_is_refused_rather_than_renamed'],
        'file': _TT,
        'old': '    if active.is_dir():',
        'new': '    if False:',
    },
    {
        'label': 'a named path reads only that file again',
        'why': '**The defect that actually shipped, restored verbatim.** '
               '`_entries()` read an explicit path as exactly that file, on the '
               'principle that "a caller naming a file is naming a file" - and '
               'four callers pass one, three of them the ACTIVE log on their '
               'default branch (`score_predictions` writes `_entries(log or '
               '_log_path())`). Measured on a copy of this machine\'s own '
               '18.9 MB log through the real dispatch path, one `record_call()` '
               'after the first rotation: 1 record returned of 19,111, and '
               'nothing said 19,110 had gone. The inverse of this mutation was '
               'in the set until 2026-10-13 as `a caller naming one file gets '
               'the whole archive set`, believed to be a defect. It was the '
               'fix.',
        'expect': ['test_a_named_path_returns_the_archived_records_too',
                   'test_a_real_caller_that_names_a_path_still_sees_the_history',
                   'test_the_calibration_reader_gets_the_history_it_names'],
        'file': _LEARN,
        'old': '    sources = tool_tracking.log_files(Path(path) if path is not None\n'
               '                                      else Path(_log_path()))',
        'new': '    sources = ([Path(path)] if path is not None\n'
               '                else tool_tracking.log_files(Path(_log_path())))',
    },
    {
        'label': 'the consolidation pass reads only the active log',
        'why': '**The third reader, and the only one a source scan missed** '
               'because `dream._entries` is not `learning._entries` - it is a '
               'second copy of `_entries_of`, reading one file. Measured with '
               'three archived and three fresh records: `read_log` returned 6, '
               'this returned 3, and `dream()`\'s own reconciliation line '
               'rewrote the count to 3 rather than reporting the disagreement - '
               'so a consolidation pass announced a fraction of the history as '
               'the whole of it. The fix delegates instead of duplicating, which '
               'is the second copy of a parser this tree keeps being bitten by '
               '(`wpctl` three times, the finite check four).',
        'expect': ['test_the_dream_pass_reports_the_archived_tool',
                   'test_the_dream_entries_helper_follows_the_archive'],
        'file': _DREAM,
        'old': '    for source in tool_tracking.log_files(path):\n'
               '        out.extend(learning._entries_of(source))',
        'new': '    for source in [path]:\n'
               '        out.extend(learning._entries_of(source))',
    },
    {
        'label': 'the dream counts only the active log',
        'why': 'The same defect in the counter. `read_log` reported one '
               'generation\'s worth of records while `_entries` supplied all of '
               'them, so `unreadable` and `truncated` described a fraction of '
               'the history without saying so - and the reconciliation line '
               'made the disagreement invisible rather than reported.',
        'expect': ['test_the_dream_counts_the_archived_records_too',
                   'test_the_dream_and_the_counter_agree'],
        'file': _DREAM,
        'old': '    for chunk in reversed(tool_tracking.log_files(path)):',
        'new': '    for chunk in list(tool_tracking.log_files(path))[-1:]:',
    },
    {
        'label': 'the byte budget is never spent',
        'why': '**The over-fix guard for "read every chunk".** Walking the '
               'archives is only correct while the budget still bounds the '
               'read: `limit_bytes` exists so a consolidation pass cannot read '
               'a year of calls, and a log now split into nine files would '
               'otherwise be nine chances to ignore it.',
        'expect': ['test_the_budget_is_spent_on_the_newest_records'],
        'file': _DREAM,
        'old': '        tail_only = size > remaining',
        'new': '        tail_only = False',
    },
    {
        'label': 'the byte budget is spent on the oldest records first',
        'why': 'The other over-fix: a reader that walks the chunks oldest-first '
               'spends the budget on the archive and drops the recent calls - '
               'the reflex layer and a running assistant read the *newest* '
               'records, and a consolidation pass over a stale tail reports a '
               'machine\'s history as it was months ago.',
        'expect': ['test_the_budget_is_spent_on_the_newest_records'],
        'file': _DREAM,
        'old': '    for chunk in reversed(tool_tracking.log_files(path)):',
        'new': '    for chunk in tool_tracking.log_files(path):',
    },
]
