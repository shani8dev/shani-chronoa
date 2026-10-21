#!/usr/bin/env bash
# Run the test suite in chunks, one log and one state directory each.
#
# Why this exists rather than `pytest tests/`: AGENTS.md records three ways a
# single run loses its output - a forked child holding a pipe open so `tail` never
# sees EOF, SIGTERM twice killing the process group, and a `Fatal Python error:
# Aborted` inside `test_setup_button_is_reachable.py` costing everything after
# it. It says "write the output to a file, not down a pipe, and run it in chunks"
# and this is that, as a script rather than advice.
#
# Four bugs the /tmp versions of this script carried, all invisible without
# reading the log sizes:
#
#   1. `setsid bash -c "... $(cat ...)"` - escaping made the command substitution
#      never run, so pytest launched with NO file list at all.
#   2. `setsid python3 -m pytest ... > "$log" 2>&1 < /dev/null > /dev/null 2>&1`
#      - two stdout redirects; the second wins, so every log stayed 0 bytes while
#      the suite ran into /dev/null.
#   3. **Every chunk shared one `XDG_STATE_HOME`.** Eight pytest processes writing
#      one state directory is how about twenty tests went red in a sweep and every
#      one passed on its own - the same defect `tests/test_file_index.py`'s
#      autouse fixture exists for, made at the harness level.
#   4. It lived in /tmp, so a restart lost it. Twice. Hence here.
#
# A mutation run refuses the source-reading suites, correctly; wait for it.
set -u
cd "$(dirname "$0")/.." || exit 1

CHUNKS=${CHUNKS:-8}
STATE=/tmp/opencode/suite-chunks
# Overridable so the staleness rule below can be tested with a fabricated log
# rather than asserted about in prose. The default is unchanged.
LOGS=${LOGS:-/tmp/opencode/suite-chunk-logs}
mkdir -p "$STATE" "$LOGS"

# `test_setup_button_is_reachable` aborts the interpreter (AGENTS.md: pre-existing,
# unexplained, and it passes alone). Run it separately, not in a chunk.
SOLO=tests/test_setup_button_is_reachable.py
list=/tmp/opencode/suite-files.txt
ls tests/test_*.py | grep -v "$SOLO" > "$list"
# **The previous run's manifests are removed first.** `split` writes
# `chunk_aa`..`chunk_<NN>` and leaves any higher-numbered file from a previous
# run in place, so the loop below globs *those* too. Measured: with `CHUNKS=8`
# run and then `CHUNKS=2`, the loop still iterated **four** manifests - two of
# them the older, larger split - and would have run a file list that no longer
# matched the requested chunking. A chunking parameter that does not change the
# chunks is a parameter that lies.
rm -f /tmp/opencode/chunk_*
split -n "l/$CHUNKS" "$list" /tmp/opencode/chunk_

# **A log is "already done" only if it is newer than the code it measured.**
#
# This used to be `[ -s "$log" ] && grep -qE 'passed|failed|error' "$log"` -
# pure existence. Measured today: the runner reported all eight chunks "already
# done" and every log was from **01:00-01:09**, against a tree whose last
# commit was **02:54**. So a green tally was reported for a suite that had not
# run since the strip redesign, the plan-mode change and three commits' worth
# of everything else.
#
# **That is the stale-label failure this repository records as worse than a
# missing label**, with the label attached to a *measurement*: a chunk skipped
# on the strength of a log from a different tree reads as a chunk that passed.
# **The newest source file is resolved once, as a path, and then every log is
# compared against that one file rather than against the log *directory*.**
#
# The first version asked `find ... -newer "$LOGS"`, and `$LOGS` is a
# directory: its mtime is when a log was last *created*, so two logs of very
# different ages are judged by whichever was written last, and a directory that
# happens to have been touched after the last code change makes every log look
# current however old it is. That is the defect the whole check exists to
# remove, reproduced inside the fix. `-nt` asks the question that is actually
# being asked - *is this log newer than the newest code* - one log at a time.
newest_source=$(find usr/lib/shani-chronoa/shani_chronoa tests \
                    usr/share/glib-2.0/schemas -type f -name '*.py' -o \
                    -type f -name 'gschemas.compiled' 2>/dev/null \
                | xargs -r ls -t 2>/dev/null | head -1)

for manifest in /tmp/opencode/chunk_*; do
    name=$(basename "$manifest")
    log="$LOGS/$name.log"
    if [ -s "$log" ] && grep -qE 'passed|failed|error' "$log"; then
        if [ -z "$newest_source" ]; then
            echo "skip $name (already done - no source files found to compare)"
        elif [ "$log" -nt "$newest_source" ]; then
            echo "skip $name (already done - newer than $newest_source)"
        else
            # **The path, not the basename.** A name a reader cannot resolve is
            # a name nobody acts on, and `tests/test_suite_chunks_are_not_stale.py`
            # checks that every path printed here exists in the tree - so an
            # earlier version that printed `cultivation.py` rather than
            # `usr/lib/shani-chronoa/shani_chronoa/cultivation.py` failed its
            # own test, which is the right way round.
            echo "STALE $name - re-running: $newest_source is newer"
        fi
        [ -n "$newest_source" ] && [ "$log" -nt "$newest_source" ] || continue
    fi
    # **`DRY_RUN=1` prints the decision and launches nothing.** The staleness
    # rule is the part worth testing, and a test that launches eight parallel
    # pytest shards is a test that manufactures exactly the D-Bus contention
    # this repository spends whole entries diagnosing.
    if [ -n "${DRY_RUN:-}" ]; then
        echo "dry-run $name -> ${log}"
        continue
    fi
    state="$STATE/$name"
    mkdir -p "$state"
    files=$(tr '\n' ' ' < "$manifest")
    # **`PYTHONDONTWRITEBYTECODE=1`, because its absence is a red test four runs
    # later.** A chunk that imports the package writes `.pyc` into
    # `usr/lib/shani-chronoa/**/__pycache__`, and `test_packaging.py`'s two
    # bytecode guards then fail - on a tree where nothing is actually wrong.
    # `tests/conftest.py:38` sets the same variable at *import* time, which covers
    # everything imported after conftest but is too late for the interpreter's own
    # compilation of what pytest has already collected. Setting it in the
    # environment here closes the whole path rather than relying on ordering.
    # What it does not cover is a standalone `tools/*.py` run, which never imports
    # conftest at all - that one is a habit, and AGENTS.md records it.
    #
    # This was added once already and then **lost to a working-tree restore from
    # HEAD** - the edit was uncommitted when the tree was destroyed, so the very
    # trap it closes reopened. Landing it as a commit is what makes it durable.
    # **`-rfE`, not `-rf`.** `-rf` reports *failures* only, and a raising
    # fixture turns every test that uses it into an ERROR - so the summary
    # counted "2 errors" and named none of them. Measured on this tree: two
    # errors in a chunk whose log said "3 failed, 1309 passed, 2 errors", and
    # the only trace of them was the "EE" in the progress dots. `-rE` prints
    # the ERROR lines, so a chunk's errors are readable without re-running it.
    setsid env XDG_STATE_HOME="$state" XDG_CONFIG_HOME="$state/config" \
        XDG_DATA_HOME="$state/data" PYTHONDONTWRITEBYTECODE=1 \
        python3 -m pytest $files -q --tb=no -rfE > "$log" 2>&1 < /dev/null &
    echo "launched $name ($(wc -w < "$manifest") files) -> $log"
done

echo
echo "to read the whole run:"
echo "  grep -hE '^(FAILED|ERROR)' $LOGS/*.log | sed -E 's/^(FAILED|ERROR) //; s/ - .*//' | sort -u"
echo "  grep -hoE '[0-9]+ passed[^|]*(\\\\| [0-9]+ failed)?[^|]*' $LOGS/*.log"
echo
echo "**Parallel shards make the GUI and live-audio tests flaky** - AGENTS.md"
echo "records it. Re-run any failing file serially before believing it:"
echo "  XDG_STATE_HOME=/tmp/opencode/solo python3 -m pytest <file> -q --tb=short"
exit 0
