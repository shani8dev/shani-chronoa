#!/usr/bin/env bash
# Run each matching test file in its own pytest process, with a time cap each.
#
#   tools/run_tests_per_file.sh REGEX [SECONDS]      e.g. 'window|composer|organ' 180
#
# **Why per file.** One pytest over 38 files ran 25 minutes and printed nothing:
# one file hung, the pipe was cut by `timeout`, and the run gave no name for the
# hang and no result for the 37 files that were fine. `pytest-timeout` is not
# installed. One process per file with its own cap names the file that hangs
# (`TIMEOUT/NO OUTPUT`) and keeps every other result.
#
# State goes to a private XDG tree per run (see `run_suite_in_chunks.sh` for why a
# shared one turns passing files red). Results: one line per file in
# $OUT (default /tmp/chronoa-per-file.txt); non-passing lines are printed at the end.
set -u
cd "$(dirname "$0")/.." || exit 1
PATTERN=${1:?usage: tools/run_tests_per_file.sh REGEX [SECONDS]}
CAP=${2:-180}
OUT=${OUT:-/tmp/chronoa-per-file.txt}
# **The state directory must not start with a prefix the suite reclaims.**
# `tests/conftest.py`'s session fixture deletes every `/tmp` directory whose
# name begins with one of `_OWNED_TMP_PREFIXES` - and "chronoa-" is the first
# of them. So a state directory called `/tmp/chronoa-per-file-state.*` was
# removed by the suite *while this script was running it*, which is the same
# incident the fixture's own comment records for the live compiled schema:
# the reclaimer is allowed to remove what the suite made, and what the suite
# is still using is not that.
STATE=$(mktemp -d /tmp/per-file-state.XXXXXX)
: > "$OUT"
files=$(ls tests | grep -E '^test_.*\.py$' | grep -E "$PATTERN")
for f in $files; do
  # **The output is captured, not written to a file.** A file in the state
  # directory is at the mercy of the suite's reclaimer (see above), and a
  # verdict that can be deleted mid-run is not a verdict.
  out=$(XDG_STATE_HOME="$STATE/st" XDG_DATA_HOME="$STATE/da" XDG_CONFIG_HOME="$STATE/cf" \
      PYTHONDONTWRITEBYTECODE=1 timeout "$CAP" \
      python3 -m pytest "tests/$f" -q -p no:randomly -W ignore --tb=line 2>&1)
  rc=$?
  # **The summary line, not the last line.**
  #
  # pytest prints its garbage-collection warnings *after* the counts, so
  # `tail -1` reads a warning rather than a verdict. And a file whose verdict
  # cannot be read is neither in the failure list nor in the pass list - the
  # absence presented as a pass, which is the failure this runner exists to
  # avoid. The selector is the same one `tools/mutate.py` reads its own
  # verdicts with: a line that begins with a count.
  r=$(printf '%s\n' "$out" | grep -E '^[[:space:]]*[0-9]+ (passed|failed|error|errors|skipped)' | tail -1)
  if [ -z "$r" ]; then
    # **No summary is a verdict of its own**, and it has to be visible: a
    # timed-out file and a file that never ran look identical otherwise.
    r="NO SUMMARY (rc=$rc)"
  fi
  echo "$f | $r" >> "$OUT"
done
rm -rf "$STATE"
echo "files: $(echo "$files" | wc -w)  results: $OUT"
# **The pass filter must be able to say "failed".** A file whose verdict is
# "NO SUMMARY" is not a pass and must not be reported as one.
grep -vE " passed in | passed, [0-9]+ (skipped|deselected)[^|]* in |^[^|]*\| [0-9]+ skipped in " "$OUT"
exit 0
