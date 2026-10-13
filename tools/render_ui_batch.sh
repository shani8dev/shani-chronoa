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

# **A verbosity knob has to exist before `set -u` can be argued with.** This
# script dies on an unset name, not on a false one, so reading `$VERBOSE` to
# decide whether to explain a failed display is only safe if `VERBOSE` is always
# set. Adding the read first and the assignment second produced the identical
# fatal unbound-variable bug this file already documents twice - once for `$num`
# read before assignment, and once in this very edit, caught by running
# `bash -c 'set -u; [[ -n $VERBOSE ]]'` before trusting it. Declared here so the
# question has an answer on the first line that asks it.
VERBOSE="${VERBOSE:-}"

#: **Below this much opaque paint, a capture is reported as NOT RENDERED**
#: rather than as a pass. See `render_once` for the four recorded instances and
#: why a byte count cannot tell a picture from a page.
MIN_OPAQUE_PCT="${MIN_OPAQUE_PCT:-50}"

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

# **The schema, or every consent gate renders a lie.** Without it the app prints
# "GSettings schema org.shani.chronoa not found - using Python defaults" on every
# panel and falls back to its Python defaults, so the Skills panel rendered
# `unknown - this build's settings schema does not declare this key` beside a
# working toggle for `airplane_mode` - a key that *is* declared. Measured: the
# key reads its Python default with no schema dir and its real value `False`
# with one.
#
# So a render without this does not merely lose detail, it **states something
# false about the build**, which is the one thing a picture is for. Set up below,
# after the sandbox exists, and read from the tree where possible - a compiled
# schema that has gone stale against its XML is itself a documented failure mode,
# since every setting then silently takes its Python default.

# A render must not leave state behind, and it must not touch the developer's
# real one either - `XDG_STATE_HOME` alone is not enough, because a skill writing
# through `XDG_DATA_HOME` lands in the real `~/.local/share`. Measured: a test
# run appending to this machine's actual `tool_calls.log`.
SANDBOX="$(mktemp -d -t chronoa-render-XXXXXX)"
mkdir -p "$SANDBOX"/{data,state,config}
START_DISPLAY=""

# Needs the sandbox, so it is set up here rather than with the other checks
# above. Read from the tree where a compiled schema is tracked; compile into the
# sandbox rather than render with none, because no schema does not degrade the
# picture, it falsifies it.
SCHEMA_DIR="$REPO/usr/share/glib-2.0/schemas"
if [[ ! -f $SCHEMA_DIR/gschemas.compiled ]]; then
    mkdir -p "$SANDBOX/schemas"
    cp "$REPO"/usr/share/glib-2.0/schemas/*.xml "$SANDBOX/schemas/" 2>/dev/null
    glib-compile-schemas "$SANDBOX/schemas" 2>/dev/null \
        && SCHEMA_DIR="$SANDBOX/schemas"
fi
[[ -f $SCHEMA_DIR/gschemas.compiled ]] || {
    echo "no compiled schema - every consent gate would render as 'unknown'," >&2
    echo "which is a false statement about the build rather than a missing detail" >&2
    exit 1
}

cleanup() {
    # Only ever kill the daemon *we* started, by the pid we started it with.
    # `pkill -f gtk4-broadwayd` would match the pattern against its own command
    # line and, worse, would kill a broadway another session is using.
    [[ -n $START_DISPLAY ]] && kill "$START_DISPLAY" 2>/dev/null
    rm -rf "$SANDBOX"
}
trap cleanup EXIT

# Replace the broadway daemon with a fresh one on a different display.
#
# Only ever touches the pid this script started, for the reason in `cleanup()`:
# `pkill -f gtk4-broadwayd` would match its own command line and would kill a
# display another session is using.
recycle_display() {
    # **The first version built a socket path from `$num` one line before
    # assigning it.** Under `set -u` that is a fatal error, not a warning, so the
    # first render that failed and reached its retry took the whole sweep down
    # with it - 23 of 34 panels, then nothing, and the summary never printed. It
    # shipped in 512d4f2 and only running it found it, which is this repository's
    # own rule about the cost of reading a diff instead of running the thing.
    local old_num="${DISP#:}"
    [[ -n $START_DISPLAY ]] && kill "$START_DISPLAY" 2>/dev/null
    START_DISPLAY=""
    # **Wait for the port to clear before asking for a display.**
    #
    # `kill` is asynchronous: the socket file goes immediately but the listening
    # socket does not, so `acquire_display` could reach the number it had just
    # released, see its port still held, skip it - and repeat that for a handful
    # of neighbouring numbers while the kernel finished tearing the old one down.
    # Measured as an intermittent *"no free broadway display in :90-:120"* on a
    # pool with **0 daemons, 0 ports held and three fresh starts working**, which
    # is the shape of a race rather than a shortage.
    [[ $old_num =~ ^[0-9]+$ ]] && wait_port_free "$old_num"
    acquire_display
}

# Start a broadway daemon on the first number in :90-:120 that **actually comes
# up**, and set DISP and START_DISPLAY.
#
# **This asks the daemon rather than predicting.** The previous version picked a
# number and returned it, and the sweep then failed with *"broadway :109 did not
# come up: Unable to listen to port 8189"* - a number whose socket file was gone
# but whose **port was still held** by a live daemon. So neither the socket nor
# any port formula is a reliable predictor: a daemon can be alive, port-bound and
# socket-less, which is exactly what this script's own reclaim produces when it
# unlinks the socket of a daemon that has not finished binding.
#
# The pool is only :90-:120, so "try the next one" is the whole strategy.
#: Where broadway's port for display N is: `8080 + N`. Measured on this box by
#: starting :116 and :118 by hand (ports 8196, 8198) and by four observed
#: "Address already in use" failures. It is used only to decide whether a number
#: is *already taken*; whether a daemon actually starts is still decided by
#: `start_display` waiting for its socket, so a wrong base costs a wasted
#: attempt and not a wrong render.
BROADWAY_PORT_BASE=8080

# Wait for `<display>`\'s listening socket to actually go away.
#
# `kill` is asynchronous, so the socket *file* can be gone while the port is
# still bound, and the reclaim that treats that as "free" is what poisons a
# number. `ss -tln` shows the port; this waits for it. Returns 0 once the port
# is clear, 1 if it is still held after ~5s - and **a caller that gets 1 must
# leave the socket file alone**, because a number whose port is still bound is
# correctly skipped by `acquire_display` and does not need removing.
wait_port_free() {
    local num="$1" waited=0 port=$((BROADWAY_PORT_BASE + $1))
    while ss -tln 2>/dev/null | grep -qE ":${port}[[:space:]]"; do
        sleep 0.1
        waited=$((waited + 1))
        [[ $waited -gt 50 ]] && return 1
    done
    return 0
}

acquire_display() {
    local n sock port
    for n in $(seq 90 120); do
        sock="/run/user/$(id -u)/broadway$((n + 1)).socket"
        # **A stale socket blocks its number forever if only its presence is
        # checked.** `gtk4-broadwayd` removes its socket on a clean exit but not
        # when it is killed, and this script's own cleanup kills by pid - so an
        # interrupted run leaves a socket file with nothing listening on it.
        # `[[ -e ]]` sees that file and skips the number, and a handful of
        # interrupted runs made every number unavailable, so the script reported
        # "no free broadway display" - which names a resource shortage and is
        # actually a pile of dead files.
        # **A display number is in use if EITHER its socket is bound OR its
        # port is listening, and it is only reclaimed when neither is.**
        #
        # The first version consulted the socket alone, and that was
        # self-defeating in a way worth writing down. `ss -xl` lists a unix
        # socket *by its filesystem path*, so a live daemon whose socket file
        # has been unlinked is invisible to it - and unlinking one is exactly
        # what the reclaim did. So a single reclaim turned a working display
        # into a **permanently poisoned** number: no socket to detect, port
        # still bound, every future start refused with *"Address already in
        # use"*. Measured: four numbers (:94, :96, :109, :112) were unusable and
        # invisible at the same time, which is why a sweep that had six genuinely
        # free numbers reported *"no free broadway display in :90-:120"*.
        #
        # So the port is the load-bearing check, not the socket. `8080 + N` is
        # measured, not remembered: confirmed by starting :116 and :118 by hand
        # (ports 8196 and 8198) and by four observed failures (:94 -> 8174,
        # :96 -> 8176, :109 -> 8189, :112 -> 8192).
        local port=$((BROADWAY_PORT_BASE + n))
        if ss -tln 2>/dev/null | grep -qE ":${port}[[:space:]]"; then
            continue                          # the port is held: not ours to take
        fi
        if [[ -S $sock ]]; then
            # Port free and socket file present: nothing is serving it, so the
            # file is left by a daemon that was killed rather than one that
            # exited. Safe to remove *now that the port has been checked*.
            rm -f "$sock"
        fi
        if start_display "$n" >/dev/null 2>&1; then
            DISP=":$n"
            return 0
        fi
        # Not free after all - the daemon printed why and its socket never
        # appeared. Reap it and move on rather than abandoning the sweep.
        # **This `rm` was the second half of a bug the comment above already
        # describes having fixed once.** `start_display` gives a daemon 4s; on a
        # loaded box that is not always enough, and a daemon that arrives at 4.1s
        # had its socket file deleted here *while it was alive*. From that moment
        # the number is invisible to the socket check and held by the port check,
        # so `acquire_display` skips it forever - the exact "permanently poisoned
        # number" this file already recorded for `:94 :96 :109 :112`.
        #
        # Measured: a 17-target sweep got **one** page (`setup:brain`, 30,265
        # bytes) and then `no free broadway display in :90-:120` for every
        # remaining one, on a pool that was **0 daemons and 0 ports held** the
        # moment the sweep exited. One slow start had cascaded into all 31
        # numbers, because each failed attempt poisoned the next.
        #
        # So the socket is removed only once the port is genuinely free. If it
        # never frees, the file stays and the port check keeps the number out on
        # its own - a live daemon is a thing to wait for, not a file to delete.
        [[ -n $START_DISPLAY ]] && kill "$START_DISPLAY" 2>/dev/null
        START_DISPLAY=""
        wait_port_free "$n" && rm -f "$sock"
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
        # 4s, not 10. A number that cannot be taken costs this much before the
        # loop moves on, and with a 31-wide pool a slow number used to be able to
        # cost minutes of a sweep that had plenty of room.
        [[ $waited -gt 20 ]] && {
            [[ -n ${VERBOSE:-} ]] && {
                echo "broadway :$num did not come up:" >&2
                sed 's/^/    /' "$SANDBOX/broadway.log" >&2
            }
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
            GSETTINGS_SCHEMA_DIR="$9" \
            PYTHONDONTWRITEBYTECODE=1 \
            timeout 300 python3 "$3" "$4" "$5" "$6" "$7" >"$8" 2>&1
    ' _ "$DISP" "$SANDBOX" "$RENDERER" "$target" "$out" "$WIDTH" "$HEIGHT" "$log" \
        "$SCHEMA_DIR" \
        >/dev/null 2>&1

    # **A file appearing is not a render.**
    #
    # `[[ -s $out ]]` was the whole of the PASS test, and a blank PNG is 20,000
    # odd bytes - so every check passed on a capture of nothing. Four such
    # instances are recorded in AGENTS.md: the 96.2%-transparent main window, the
    # 129,834-byte `cloud-keys` false pass, `composer-empty` at 0.3% opaque, and
    # `main` itself blanking at HEAD while the sweep said "1 rendered, 0 not".
    # **The alpha number is in the file the whole time and nothing reads it.**
    #
    # The floor is 50% and it is not a guess: every genuine render measured in
    # this repository is 100% opaque and every recorded false pass is 7.2% or
    # worse, so nothing sits near the line. `MIN_OPAQUE_PCT` is overridable
    # because a legitimate future target might be a thin popover - and an
    # overridable threshold is still a threshold, where "a file appeared" is not.
    [[ -s $out ]] || return 1
    local opaque
    opaque="$(_opaque_percent "$out")"
    # **A probe that could not measure is not a pass.** If Pillow is missing or
    # the file is not a PNG, `_opaque_percent` prints nothing and this fails -
    # the opposite of the bug this replaces, where anything unreadable counted as
    # a render.
    [[ -n $opaque ]] || return 1
    (( opaque >= MIN_OPAQUE_PCT ))
}

_opaque_percent() {
    # How much of this PNG is actually painted, as an integer percentage.
    # **Sampled, not exhaustive**: every 8th pixel, which is 1/64 of the image
    # and fast enough to run per target. A blank page is blank at every stride,
    # so sampling costs this check nothing and a real render is uniformly opaque,
    # so there is nothing to miss.
    python3 - "$1" <<'PYOPACITY' 2>/dev/null
import sys
try:
    from PIL import Image
except Exception:
    sys.exit(0)
try:
    im = Image.open(sys.argv[1]).convert("RGBA")
except Exception:
    sys.exit(0)
px = im.load()
w, h = im.size
if w < 1 or h < 1:
    sys.exit(0)
step = 8
total = opaque = 0
for y in range(0, h, step):
    for x in range(0, w, step):
        total += 1
        if px[x, y][3] == 255:
            opaque += 1
print(int(100 * opaque / total))
PYOPACITY
}

render() {
    local target="$1"
    local out="$2"

    # **The directory a sweep writes into has to exist.** Only `$OUTDIR` itself
    # was created, so every grouped target - `panels/`, `pages/`, `states/`,
    # `groups/`, `setup-cloud/` - wrote into a directory that did not exist.
    # `Gdk.Texture.save_to_png` then returns without writing and without
    # raising, `[[ -s $out ]]` is false, and the sweep reports **every target as
    # failed while naming all of them** - which reads exactly like a broken
    # product and is not one. Measured: `state:composer-text` renders 141,795
    # bytes run by hand and reports FAIL through this script.
    mkdir -p "$(dirname "$out")"

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

    # **Recycle the display before the second attempt.**
    #
    # A sweep runs every render against one broadway daemon, and a daemon that
    # has served a few dozen surfaces stops giving new ones: measured, a 6-state
    # sweep passed and a 12-state sweep stopped after two, with every later
    # target reporting "no file written" and nothing in its log. The count is
    # not fixed, which is exactly why it read as flakiness rather than as a
    # server that had run out of room - and a second attempt against the *same*
    # exhausted display cannot succeed, so the retry was reporting a real fault
    # as a page defect.
    recycle_display

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
        GSETTINGS_SCHEMA_DIR="$SCHEMA_DIR" \
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

# Every *registered page* of every window - the main window's five, Settings'
# seven sections and the wizard's seventeen - read from the page registry rather
# than typed, for the same reason the panels are: a hand-kept list of what a
# registry already knows is a list that rots.
#
# `pages.show()` is the same entry point `--show-page=` uses, so what is
# photographed is what that flag opens. A page id that has drifted from its
# window fails as REFUSED rather than producing a picture of whatever page
# happened to be up - which is the property that makes a sweep worth trusting.
page_targets() {
    env -u DISPLAY -u WAYLAND_DISPLAY \
        GSETTINGS_SCHEMA_DIR="$SCHEMA_DIR" \
        PYTHONDONTWRITEBYTECODE=1 \
        python3 - "$REPO" <<'PY' 2>/dev/null
import sys
sys.path.insert(0, sys.argv[1] + "/usr/lib/shani-chronoa")
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw          # noqa: F401
Adw.init()
from shani_chronoa import pages
for window in sorted(pages.windows()):
    pages._ensure_declared(window)
    for pid in sorted(pages.page_ids(window)):
        print("page:" + window + ":" + pid)
PY
}

# Every *interaction state* - one row of a mode chip selected, the composer with
# text in it, one organ light lit. Read from the strip's own tables rather than
# typed, for the reason the panels are: a hand-kept list of what a class already
# declares is a list that rots.
#
# These are the states a first paint cannot show. `panel:` and `page:` photograph
# a window as it first appears, which is a state nobody spends time in - which
# row of a chip is highlighted, or whether the composer grows, are decided by
# state the initial paint never passes through.
state_targets() {
    # `--list` on the renderer itself, run as a subprocess. **It has to be a
    # subprocess and not an import**: render_ui.py calls `app.run()` at module
    # scope, so importing it *is* a render, and an import-based enumerator
    # returns nothing while reporting "0 targets" - a result indistinguishable
    # from an empty registry.
    python3 "$RENDERER" --list states 2>/dev/null
}

# Every settings group, by its own visible title.
#
# **The seven registered sections are not the window.** The window builds ~25
# `Adw.PreferencesGroup`s and the section chips only reveal families of them, so
# a section-routed sweep photographs a fraction of the settings surface - and
# does so without saying so. These come from the built window's own group titles,
# which is the same "read it from the code" rule as everywhere else here.
group_targets() {
    env -u DISPLAY -u WAYLAND_DISPLAY \
        GSETTINGS_SCHEMA_DIR="$SCHEMA_DIR" \
        PYTHONDONTWRITEBYTECODE=1 \
        python3 - "$REPO" <<'PY' 2>/dev/null
import sys, re
from pathlib import Path
root = Path(sys.argv[1]) / "usr/lib/shani-chronoa/shani_chronoa/settings_window"
titles, seen = [], set()
for path in sorted(root.glob("*.py")):
    src = path.read_text()
    for m in re.finditer(r'group\(\s*(?:title=)?\s*"([^"]{3,60})"', src):
        t = m.group(1)
        if t not in seen:
            seen.add(t)
            titles.append(t)
    for m in re.finditer(r'^\s*(?:[A-Z_]+)\s*=\s*"([^"]{6,60})"\s*$', src, re.M):
        pass
for t in titles:
    print("group:" + t)
PY
}

acquire_display || exit 1
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
        states)
            while read -r t; do
                [[ -n $t ]] || continue
                name="${t#state:}"
                if render "$t" "$OUTDIR/states/$name.png"; then
                    ok=$((ok + 1))
                else
                    bad=$((bad + 1)); failed+=("$t")
                fi
            done < <(state_targets)
            ;;
        groups)
            while read -r t; do
                [[ -n $t ]] || continue
                name="$(echo "${t#group:}" | tr ' /' '--')"
                if render "$t" "$OUTDIR/groups/$name.png"; then
                    ok=$((ok + 1))
                else
                    bad=$((bad + 1)); failed+=("$t")
                fi
            done < <(group_targets)
            ;;
        pages)
            # `<window>-<page>` so the three windows do not collide on a bare
            # page name - `voice` is both a Settings section and a wizard step.
            while read -r p; do
                [[ -n $p ]] || continue
                name="$(echo "${p#page:}" | tr ':' '-')"
                if render "$p" "$OUTDIR/pages/$name.png"; then
                    ok=$((ok + 1))
                else
                    bad=$((bad + 1)); failed+=("$p")
                fi
            done < <(page_targets)
            ;;
        setup-cloud)
            # **The same seventeen wizard pages, in the other branch.** The mode
            # is chosen on the `mode` page and stored, so the cloud branch is not
            # reachable by rendering - it has to be *set*. Pre-seeding the
            # sandbox's keyfile is the whole difference, and it is why the local
            # and cloud sweeps have to be two runs rather than one: a sweep that
            # only ever shows one branch has not looked at the branch.
            #
            # The group name is the schema's `path`, not its `id` - the app's
            # own writer uses `[org/shani/chronoa]` and `gsettings` derives it
            # from the path, so the id spelling is what an unread setting looks
            # like, and a render that silently defaulted it would photograph the
            # local branch and call it cloud.
            mkdir -p "$SANDBOX/config/glib-2.0"
            printf '[org/shani/chronoa]\nsetup-mode=cloud\n' \
                >"$SANDBOX/config/glib-2.0/settings.keyfile"
            chmod 600 "$SANDBOX/config/glib-2.0/settings.keyfile"
            while read -r p; do
                [[ -n $p ]] || continue
                [[ $p == page:setup:* ]] || continue
                name="$(echo "${p#page:}" | tr ':' '-')"
                if render "$p" "$OUTDIR/setup-cloud/$name.png"; then
                    ok=$((ok + 1))
                else
                    bad=$((bad + 1)); failed+=("$p")
                fi
            done < <(page_targets)
            ;;
        all)
            for p in main empty help shortcuts about tool-activity settings \
                     quick-ask browser body; do
                if render "$p" "$OUTDIR/$p.png"; then ok=$((ok + 1)); else bad=$((bad + 1)); failed+=("$p"); fi
            done
            while read -r p; do
                [[ -n $p ]] || continue
                name="${p#panel:}"
                if render "$p" "$OUTDIR/panels/$name.png"; then ok=$((ok + 1)); else bad=$((bad + 1)); failed+=("$p"); fi
            done < <(panel_targets)
            while read -r p; do
                [[ -n $p ]] || continue
                name="$(echo "${p#page:}" | tr ':' '-')"
                if render "$p" "$OUTDIR/pages/$name.png"; then ok=$((ok + 1)); else bad=$((bad + 1)); failed+=("$p"); fi
            done < <(page_targets)
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