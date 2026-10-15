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
STATE=$(mktemp -d /tmp/chronoa-per-file-state.XXXXXX)
: > "$OUT"
files=$(ls tests | grep -E '^test_.*\.py$' | grep -E "$PATTERN")
for f in $files; do
  r=$(XDG_STATE_HOME="$STATE/st" XDG_DATA_HOME="$STATE/da" XDG_CONFIG_HOME="$STATE/cf" \
      PYTHONDONTWRITEBYTECODE=1 timeout "$CAP" \
      python3 -m pytest "tests/$f" -q -p no:randomly -W ignore --tb=line 2>&1 | tail -1)
  echo "$f | ${r:-TIMEOUT/NO OUTPUT}" >> "$OUT"
done
rm -rf "$STATE"
echo "files: $(echo "$files" | wc -w)  results: $OUT"
grep -vE " passed in | passed, [0-9]+ (skipped|deselected)[^|]* in |^[^|]*\| [0-9]+ skipped in " "$OUT"
exit 0
