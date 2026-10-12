#!/usr/bin/env bash
# Render every page of every Chronoa window to PNG, on a private display.
#
# **Why this exists rather than a loop typed at a prompt.** A render harness
# written into a scratch directory is lost with the session, and this repository
# records that cost more than once - `AGENTS.md`'s "Rendering the UI to a PNG"
# section is itself a list of attempts that failed for recorded reasons, and
# `AUDIT-HISTORY.md` has the same complaint about probes. The renderer itself is
# `render_ui.py` at the repository root; this is the part that was hand-typed
# every time: own a display, sweep every target, and report honestly.
#
# **A private display, always.** `render_ui.py` on the real session display
# takes focus - `AGENTS.md` records a filter field rendering holding "ho",
# because somebody's keystrokes landed in the render window. Broadway is a
# headless GTK display that draws the same widgets and takes no input, so
# nothing typed can land in it.
#
# **`DISPLAY` must be unset in the child.** A live X session wins over
# `GDK_BACKEND=broadway`, so an inherited `DISPLAY` silently produces a render
# on the real screen - the one thing this script exists to prevent. Measured:
# every Adw call failing with `GDK_IS_DISPLAY (display) failed` while the
# process ran to completion and wrote nothing.
#
# **The exit code is not the verdict; the PNG is.** GTK asserts in
# `gsk_renderer_dispose` during teardown after the texture is written, so a
# successful render very often exits non-zero with a core dump. Reporting
# `$?` would call every working page a failure. This script prints the byte
# count and greps for the file.
#
# Usage:
#   tools/render_ui_batch.sh [OUTDIR] [WIDTH] [HEIGHT] [TARGET ...]
#
#   OUTDIR    where the PNGs go          (default /tmp/chronoa-ui)
#   WIDTH     pixels                     (default 1280)
#   HEIGHT    pixels                     (default 860)
#   TARGET    any of: all panels wizard settings help tool-activity
#             or an explicit target, e.g. `main` `empty` `panel:privacy`
#             `page:settings:senses`    (default: all panels)
#
# Note on width: broadway caps a window at 1024x768 and *scales* anything
# larger, so a picture taken above that is not a measurement of layout. For a
# real-width layout assertion use a probe, not a render. The width is a
# parameter anyway, because the thing to look at is the composition.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
RENDERER="$REPO/render_ui.py"

OUTDIR="${1:-/tmp/chronoa-ui}"
WIDTH="${2:-1280}"
HEIGHT="${3:-860}"
shift 3 2>/dev/null || shift $#
TARGETS=("$@")

[[ -f $RENDERER ]] || { echo "no renderer at $RENDERER" >&2; exit 1; }
[[ -n ${TARGETS[*]:-} ]] || TARGETS=(panels)

# A render must not leave state behind, and it must not touch the developer's
# real one either - `XDG_STATE_HOME` alone is not enough, because a skill writing
# through `XDG_DATA_HOME` lands in the real `~/.local/share`. Measured: a test
# run appending to this machine's actual `tool_calls.log`.
SANDBOX="$(mktemp -d -t chronoa-render-XXXXXX)"
mkdir -p "$SANDBOX"/{data,state,config}
START_DISPLAY=""

cleanup() {
    # Only ever kill the daemon *we* started, by the pid we started it with.
    # `pkill -f gtk4-broadwayd` would match the pattern against its own command
    # line and, worse, would kill a broadway another session is using.
    [[ -n $START_DISPLAY ]] && kill "$START_DISPLAY" 2>/dev/null
    rm -rf "$SANDBOX"
}
trap cleanup EXIT

pick_display() {
    local n
    for n in $(seq 90 120); do
        [[ -e "/run/user/$(id -u)/broadway$((n + 1)).socket" ]] && continue
        printf '%s' "$n"
        return 0
    done
    echo "no free broadway display in :90-:120" >&2
    return 1
}

start_display() {
    local num="$1"
    # `setsid` or the daemon dies with the shell that launched it - measured,
    # and the failure looks like "broadway does not work".
    setsid gtk4-broadwayd ":$num" >"$SANDBOX/broadway.log" 2>&1 &
    START_DISPLAY=$!
    local waited=0
    while [[ ! -e "/run/user/$(id -u)/broadway$((num + 1)).socket" ]]; do
        sleep 0.2
        waited=$((waited + 1))
        [[ $waited -gt 50 ]] && {
            echo "broadway :$num did not come up:" >&2
            sed 's/^/    /' "$SANDBOX/broadway.log" >&2
            return 1
        }
    done
    echo ":$num"
}

render_once() {
    local target="$1"
    local out="$2"
    local log="$SANDBOX/$(echo "$target" | tr '/:' '__').log"
    # Run through a *second* shell with its stderr discarded. A subshell is not
    # enough: the "Aborted (core dumped)" line is the parent shell reporting the
    # child's wait status, so it survives `2>/dev/null` on the subshell itself.
    # Giving the command its own `bash -c` puts the report inside the boundary
    # that discards it.
    bash -c '
        env -u DISPLAY -u WAYLAND_DISPLAY \
            GDK_BACKEND=broadway BROADWAY_DISPLAY="$1" \
            XDG_DATA_HOME="$2/data" XDG_STATE_HOME="$2/state" \
            XDG_CONFIG_HOME="$2/config" \
            PYTHONDONTWRITEBYTECODE=1 \
            timeout 300 python3 "$3" "$4" "$5" "$6" "$7" >"$8" 2>&1
    ' _ "$DISP" "$SANDBOX" "$RENDERER" "$target" "$out" "$WIDTH" "$HEIGHT" "$log" \
        >/dev/null 2>&1
    [[ -s $out ]]
}

render() {
    local target="$1"
    local out="$2"

    if render_once "$target" "$out"; then
        printf '  PASS  %-28s %8s bytes\n' "$target" "$(stat -c%s "$out")"
        return 0
    fi

    # One retry. A cold display genuinely can lose the first frame - the
    # renderer settles for 1500 ms on everything but the main window - but a
    # retry that turned a permanent NO NODE into a reported PASS would be a
    # check that cannot fail, so the retry only ever *adds* a second chance and
    # a second failure still reports.
    if render_once "$target" "$out"; then
        printf '  PASS  %-28s %8s bytes (second attempt)\n' "$target" "$(stat -c%s "$out")"
        return 0
    fi

    printf '  FAIL  %-28s no file written\n' "$target"
    local log="$SANDBOX/$(echo "$target" | tr '/:' '__').log"
    if grep -q "NO NODE" "$log" 2>/dev/null; then
        # Name the cause rather than reporting a bare failure. This one is
        # recorded in AGENTS.md and is not a defect in the page: a
        # `WidgetPaintable` taken over a window whose child is a `NavigationView`
        # yields a node of None even though the window is mapped and allocated.
        printf '          NO NODE - the documented render fault, not a broken page:\n'
        printf '          WidgetPaintable over an ApplicationWindow whose child is\n'
        printf '          a NavigationView returns None. See AGENTS.md, "Rendering the\n'
        printf '          UI to a PNG".\n'
    else
        grep -viE 'gsettings|Gdk-CRITICAL|GLib-GIO|^$' "$log" 2>/dev/null \
            | tail -3 | sed 's/^/          /'
    fi
    return 1
}

# The panel list is read from the registry rather than typed here. A hand-kept
# list of what a directory already knows is a list that rots, and `AGENTS.md`
# records exactly that failure several times over.
panel_targets() {
    env -u DISPLAY -u WAYLAND_DISPLAY \
        PYTHONDONTWRITEBYTECODE=1 \
        python3 - "$REPO" <<'PY' 2>/dev/null
import sys
sys.path.insert(0, sys.argv[1] + "/usr/lib/shani-chronoa")
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw          # noqa: F401  - Adw.init() needs them loaded
Adw.init()
from shani_chronoa.gui import surfaces
for name in surfaces.SURFACE_IDS:
    print("panel:" + name)
PY
}

DISP="$(start_display "$(pick_display)")" || exit 1
echo "chronoa render: display $DISP, ${WIDTH}x${HEIGHT}, -> $OUTDIR"
echo "                 $SANDBOX is scratch; nothing here touches your real XDG dirs"
mkdir -p "$OUTDIR"

ok=0; bad=0; failed=()
for t in "${TARGETS[@]}"; do
    case "$t" in
        panels)
            while read -r p; do
                [[ -n $p ]] || continue
                name="${p#panel:}"
                if render "$p" "$OUTDIR/panels/$name.png"; then
                    ok=$((ok + 1))
                else
                    bad=$((bad + 1)); failed+=("$p")
                fi
            done < <(panel_targets)
            ;;
        all)
            for p in main empty help tool-activity settings; do
                if render "$p" "$OUTDIR/$p.png"; then ok=$((ok + 1)); else bad=$((bad + 1)); failed+=("$p"); fi
            done
            while read -r p; do
                [[ -n $p ]] || continue
                name="${p#panel:}"
                if render "$p" "$OUTDIR/panels/$name.png"; then ok=$((ok + 1)); else bad=$((bad + 1)); failed+=("$p"); fi
            done < <(panel_targets)
            ;;
        *)
            name="${t#panel:}"
            if render "$t" "$OUTDIR/$name.png"; then ok=$((ok + 1)); else bad=$((bad + 1)); failed+=("$t"); fi
            ;;
    esac
done

echo
echo "  $ok rendered, $bad not."
if [[ $bad -gt 0 ]]; then
    # Named rather than counted. A bare number reads as a pass rate and hides
    # which page is the broken one, which is the whole reason to render.
    printf '  not rendered: %s\n' "${failed[*]}"
    echo "  A page that does not render is not necessarily a broken page - see"
    echo "  AGENTS.md 'Rendering the UI to a PNG' for the ones that never did."
fi
exit $(( bad > 0 ? 1 : 0 ))