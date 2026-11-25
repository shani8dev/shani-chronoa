#!/usr/bin/env bash
# Run every render-marked test in its own pytest process.
#
#   tools/run_render_tests.sh [SECONDS]
#
# **Why per test, not per file.** `tests/test_setup_button_is_reachable.py`
# maps a real ChronoaApplication under `app.run()` in every test. Two or more
# of those in one process crash in GTK/Pango while snapshotting a symbolic
# paintable (`pango_glyph_string_extents_range`, SIGSEGV or SIGABRT), which ends
# the pytest process and turns every queued test into an error. Running one
# node per process keeps the real window, the real button and the real press
# covered; it is a process boundary, not a skip.
#
# The default batch deselects these (`pytest.ini`'s `-m "not slow and not
# render"`) and this runner is the documented door back to them. It prints the
# node count and every verdict, and exits non-zero on a crash, a missing
# summary or a failure - a file appearing or a command starting is not a pass.
set -u
cd "$(dirname "$0")/.." || exit 1
CAP=${1:-180}
OUT=${OUT:-/tmp/chronoa-render-tests.txt}
# A private state tree, but NOT under a prefix tests/conftest.py reclaims.
STATE=$(mktemp -d /tmp/render-tests-state.XXXXXX)
nodes=$(PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --collect-only -q \
    -m render -p no:cacheprovider 2>/dev/null | grep -E '^tests/.*::' || true)
if [ -z "$nodes" ]; then
  echo "NO RENDER TESTS COLLECTED - the marker or its runner is broken" >&2
  rm -rf "$STATE"
  exit 2
fi
count=$(printf '%s\n' "$nodes" | wc -w)
echo "render tests: $count (one process each, cap ${CAP}s each)"
: > "$OUT"
bad=0
for node in $nodes; do
  out=$(XDG_STATE_HOME="$STATE/st" XDG_DATA_HOME="$STATE/da" \
      XDG_CONFIG_HOME="$STATE/cf" PYTHONDONTWRITEBYTECODE=1 \
      timeout "$CAP" python3 -m pytest "$node" -q -p no:cacheprovider \
      -m render --tb=line 2>&1)
  rc=$?
  verdict=$(printf '%s\n' "$out" | grep -E \
      '^[[:space:]]*[0-9]+ (passed|failed|error|errors|skipped)' | tail -1)
  if [ -z "$verdict" ]; then
    verdict="NO SUMMARY (rc=$rc)"
  fi
  case "$verdict" in
    *failed*|*error*|"NO SUMMARY"*) bad=$((bad + 1)) ;;
  esac
  echo "$node | $verdict" >> "$OUT"
done
rm -rf "$STATE"
echo "results: $OUT"
cat "$OUT"
if [ "$bad" -ne 0 ]; then
  echo "$bad render test(s) did not pass" >&2
  exit 1
fi
echo "$count render test(s) passed in separate processes"
