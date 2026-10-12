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
LOGS=/tmp/opencode/suite-chunk-logs
mkdir -p "$STATE" "$LOGS"

# `test_setup_button_is_reachable` aborts the interpreter (AGENTS.md: pre-existing,
# unexplained, and it passes alone). Run it separately, not in a chunk.
SOLO=tests/test_setup_button_is_reachable.py
list=/tmp/opencode/suite-files.txt
ls tests/test_*.py | grep -v "$SOLO" > "$list"
split -n "l/$CHUNKS" "$list" /tmp/opencode/chunk_

for manifest in /tmp/opencode/chunk_*; do
    name=$(basename "$manifest")
    log="$LOGS/$name.log"
    if [ -s "$log" ] && grep -qE 'passed|failed|error' "$log"; then
        echo "skip $name (already done)"
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
    setsid env XDG_STATE_HOME="$state" XDG_CONFIG_HOME="$state/config" \
        XDG_DATA_HOME="$state/data" PYTHONDONTWRITEBYTECODE=1 \
        python3 -m pytest $files -q --tb=no -rf > "$log" 2>&1 < /dev/null &
    echo "launched $name ($(wc -w < "$manifest") files) -> $log"
done

echo
echo "to read the whole run:"
echo "  grep -hE '^FAILED' $LOGS/*.log | sed 's/FAILED //; s/ - .*//' | sort -u"
echo "  grep -hoE '[0-9]+ passed[^|]*(\\\\| [0-9]+ failed)?[^|]*' $LOGS/*.log"
echo
echo "**Parallel shards make the GUI and live-audio tests flaky** - AGENTS.md"
echo "records it. Re-run any failing file serially before believing it:"
echo "  XDG_STATE_HOME=/tmp/opencode/solo python3 -m pytest <file> -q --tb=short"
exit 0
