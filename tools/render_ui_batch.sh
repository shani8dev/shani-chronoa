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

#: **Below this much non-background area, a capture is NOT RENDERED**
#: rather than as a pass. See `render_once` for the four recorded instances and
#: why a byte count cannot tell a picture from a page.
MIN_CONTENT_PCT="${MIN_CONTENT_PCT:-5}"

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

_keep_log() {
    # Copy the renderer's log next to its output. **Always, not only on failure** -
    # the failure is discovered by whoever reads the file afterwards, and a log
    # kept only for failures has to guess which those were.
    local log="$1" out="$2"
    [[ -s $log ]] || return 0
    cp "$log" "${out%.png}.log" 2>/dev/null || return 1
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
    # The floor is 5%: a blank transparent page and a blank white page both
    # measure 0, and the poorest thing that is really a picture measures ~20. `MIN_OPAQUE_PCT` is overridable
    # because a legitimate future target might be a thin popover - and an
    # overridable threshold is still a threshold, where "a file appeared" is not.
    # **Keep the log when the target fails.**
    #
    # `render_once` writes the renderer's own stderr to `$SANDBOX/<target>.log`
    # and the sandbox is deleted when the script exits - so the one artefact that
    # names the cause is thrown away on every failed attempt. Four consecutive
    # attempts to diagnose the blank renders produced no diagnosis, and the
    # reason is here: not that the traceback was hard to read, but that nobody
    # kept it. **A diagnostic nobody can read is not a diagnostic.**
    #
    # Copied beside the output rather than kept in the sandbox, so it survives the
    # sweep that produced it and can be looked at afterwards - which is the whole
    # difference between "the renderer printed nothing useful" and a sentence
    # naming a line.
    if ! _keep_log "$log" "$out"; then
        :
    fi
    [[ -s $out ]] || return 1

    # **A render at the wrong size is not a render of what was asked for.**
    #
    # **Before the alpha gate, not after.** The first version sat below it, which
    # meant it could never fire on a blank capture - and a blank capture is
    # exactly when the size matters most, because a window that came up at
    # 1024x768 when 1280x800 was asked for is the difference between a
    # picture of the wide layout and a picture of the narrow one. Both
    # signals have to reach the reader together.
    #
    # The broadway screen on this box is a hard 1024x768 - `broadwayd` takes no
    # size option - so a sweep that asks for 1280x800 gets a picture of 1024x768
    # and nothing anywhere says so. That went unnoticed through a whole review
    # because the PNG was valid, opaque, and the right file name, which is the
    # same trio the alpha gate above had to be built for. **It also sent the
    # `state:` branch's size handling into AGENTS.md as a bug in that branch:**
    # `state:` honours `CHRONOA_FORCE_SIZE` perfectly well and the screen
    # overrules it, which is why "asked 1280x800, got 1024x768" looked like a
    # branch that ignored its own override.
    #
    # **Warned, not failed.** Refusing would make the harness useless on a box
    # whose screen cannot be enlarged, and a check that can never pass stops
    # being read - the `-rs` shape this file already records. The ask is still
    # honoured wherever the display allows it; where it is not, the difference
    # is now stated rather than inferred from the file.
    local got_w got_h
    got_w="$(_png_width "$out")"; got_h="$(_png_height "$out")"
    if [[ -n $got_w && -n $got_h ]] && [[ "$got_w" != "$WIDTH" || "$got_h" != "$HEIGHT" ]]; then
        printf 'note: %s captured at %sx%s, not %sx%s (display is smaller)\n' \
            "$(basename "$out")" "$got_w" "$got_h" "$WIDTH" "$HEIGHT" >&2
    fi

    local opaque
    content="$(_content_percent "$out")"
    # **A probe that could not measure is not a pass.** If Pillow is missing or
    # the file is not a PNG, `_content_percent` prints nothing and this fails -
    # the opposite of the bug this replaces, where anything unreadable counted as
    # a render.
    [[ -n $content ]] || return 1
    (( content >= MIN_CONTENT_PCT )) || return 1

    # **And something has to be drawn in the middle.** A window-shaped border
    # around an empty window is real ink - measured 14% of the image against a
    # 5% floor - so the percentage above cannot see it. The condition is the
    # absence of interior ink rather than a second threshold, because a threshold
    # here would reject the real `shortcuts` window, which measures 4%.
    local interior
    interior="$(_interior_ink "$out")"
    [[ -n $interior ]] || return 1
    (( interior > 0 )) || return 1

    return 0
}

_png_size() {
    python3 - "$1" "$2" <<'PYSIZE' 2>/dev/null
import sys
try:
    from PIL import Image
except Exception:
    sys.exit(0)
try:
    im = Image.open(sys.argv[1])
except Exception:
    sys.exit(0)
# `im.size[int(...)]`, not `getattr(im.size, ...)`. `im.size` is a **tuple**,
# and `getattr(("1024","768"), "0", "")` is empty - so the first version
# measured nothing on every file, the `[[ -n $got_w ]]` guard was always
# false, and the note never printed. **A check that cannot measure passed
# silently**, which is the whole failure this gate was written to stop, and it
# did it in the same commit.
print(im.size[int(sys.argv[2])])
PYSIZE
}

_png_width() { _png_size "$1" 0; }
_png_height() { _png_size "$1" 1; }

_content_percent() {
    # How much of this PNG is **not background**, as an integer percentage.
    #
    # **Alpha was the wrong question, twice.** The first version counted
    # `alpha == 255` and refused every genuine render - the real `main` window
    # is alpha 25 across 80% of the image. Counting `alpha > 0` fixed that and
    # immediately passed `state:composer-empty`, which is a **pure white
    # rectangle**: alpha 255 everywhere, and no picture in it at all. Alpha says
    # a pixel exists. It says nothing about whether anything was drawn *into*
    # it.
    #
    # So the measure is contrast against the background: the most common colour
    # in the sample is the background by definition, and the question is how
    # much of the image is something else.
    #
    # **Two holes, both found by measuring a real target rather than a fixture.**
    # The `empty` window rendered at 31% and was a blank page:
    #
    # - **`[:3]` dropped alpha**, so its **62,269 fully transparent** pixels
    #   counted as content. A pixel that was never painted is not content and
    #   not background either, so they are now excluded from the population
    #   entirely - which is what makes "the background" mean something.
    # - **No tolerance**, so its `(243,243,243)` marks against a `(248,248,248)`
    #   background - a delta of **5**, imperceptible - counted as ink. The cue
    #   layer's own measurement puts antialiasing fringes at 3, so 8 is above the
    #   noise and far below any mark a person can see.
    #
    # An image that opened and is entirely unpainted **is** measurable and its
    # answer is 0, which is different from an image this could not read: that one
    # prints nothing and the gate fails it.
    #
    # **Sampled, not exhaustive**: every 8th pixel, 1/64 of the image, fast
    # enough to run per target. Background is a whole-image property and a
    # 1/64 sample estimates it closely enough to sit nowhere near the floor.
    python3 - "$1" <<'PYOPACITY' 2>/dev/null
import sys
from collections import Counter

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
TOLERANCE = 8          # see above: antialiasing fringes measure 3
colours = []
for y in range(0, h, step):
    for x in range(0, w, step):
        r, g, b, a = px[x, y]
        if a == 0:
            continue      # never painted: neither content nor background
        colours.append((r, g, b))
if not colours:
    print(0)              # readable, and empty - not the same as unreadable
    sys.exit(0)
background = Counter(colours).most_common(1)[0][0]
marks = sum(1 for c in colours
            if max(abs(c[i] - background[i]) for i in range(3)) > TOLERANCE)
print(int(100 * marks / len(colours)))
PYOPACITY
}


_interior_ink() {
    # **How much ink is inside a margin, as an integer percentage.**
    #
    # This exists because `_content_percent` alone cannot tell a **window** from
    # **a window-shaped border**: the rounded frame `CHRONOA_FORCE_SIZE` produces
    # around an empty main window is real high-contrast ink and measures 14% of
    # the image, so it clears the floor. The interior of that frame is empty, and
    # a real window's interior is not - orb, organ strip, transcript and composer
    # all sit well inside it.
    #
    # **The gate asks whether it is zero, not whether it beats a threshold.**
    # Measured, a percentage here is the wrong instrument: the real `shortcuts`
    # window has only **4%** interior ink and is still a real render, so a floor
    # would reject it and a floor that did not reject it would be tuned to this
    # one machine. "Something is drawn in the middle" needs no number at all.
    #
    # The 12% margin is the smallest that excludes a window's own frame and its
    # title bar on every size measured (460x640 through 1280x860) while leaving
    # the orb, which sits near the top, inside.
    python3 - "$1" <<'PYINTERIOR' 2>/dev/null
import sys
from collections import Counter

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
MARGIN = 0.12
TOLERANCE = 8
step = 8
colours = []
inner = []
for y in range(0, h, step):
    for x in range(0, w, step):
        r, g, b, a = px[x, y]
        if a == 0:
            continue
        colours.append((r, g, b))
        if MARGIN * w <= x <= (1 - MARGIN) * w and MARGIN * h <= y <= (1 - MARGIN) * h:
            inner.append((r, g, b))
if not colours or not inner:
    print(0)
    sys.exit(0)
background = Counter(colours).most_common(1)[0][0]
marks = sum(1 for c in inner
            if max(abs(c[i] - background[i]) for i in range(3)) > TOLERANCE)
print(int(100 * marks / len(inner)))
PYINTERIOR
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

    # **Every attempt is scored, the display is recycled *between* them, and the
    # best picture is kept whether it passed or not.**
    #
    # Three defects, all measured on this driver by running it, and the third is
    # the one that lies:
    #
    # 1. **The FAIL line said "no file written" while a file existed.** Rendered
    #    live: `FAIL  main  no file written`, with **23,374 bytes** sitting in the
    #    output directory. The picture was *rejected* - 4% content against a 5%
    #    floor - and "no file written" sends the next reader looking for a
    #    missing file rather than at a rejected one. **This is the absence-shaped
    #    wrong answer**, and the very gate that added the measure is what made the
    #    sentence wrong.
    # 2. **`recycle_display` ran after both attempts** - ten lines below a comment
    #    saying *"a second attempt against the same exhausted display cannot
    #    succeed"*. The paragraph described the fix and the code did the opposite.
    #    Same defect this driver already records for `rm -f`, in a different
    #    function: **a paragraph explaining why something is dangerous is not a
    #    control.**
    # 3. **Both attempts wrote to `$out` and nothing removed it between them**, so
    #    only one picture could survive and the survivor was whichever ran last -
    #    arbitrary, where `render_once`'s own return value already carries the
    #    score that would order them.
    #
    # The score is read for **every** attempt rather than only the passing ones,
    # because the rejected picture is the one a reader needs the figure for.
    # `best_pct` orders the attempts by that same `_content_percent` the gate
    # uses, so the artefact kept is the one with the most on it.
    local best_pct=-1 best_file="" attempt_pct="" last_pct="" note=""
    local n
    for n in 1 2; do
        local try="${out}.try${n}"
        rm -f "$try"
        render_once "$target" "$try" || true
        attempt_pct="$(_content_percent "$try")"
        if [[ -n $attempt_pct ]]; then
            last_pct="$attempt_pct"
            if (( attempt_pct > best_pct )); then
                best_pct="$attempt_pct"
                best_file="$try"
                (( n == 2 )) && note=", best of two attempts"
            fi
        fi
        # **Between** the attempts, which is what the old comment said and did
        # not do: a cold or exhausted daemon is the documented reason a first
        # frame is lost, so the retry runs against a display of its own.
        (( n < 2 )) && recycle_display
    done

    # The best picture becomes *the* artefact whether or not it clears the floor:
    # a rejected render is still the best evidence of what went wrong, and it is
    # what the FAIL line below quotes.
    [[ -n $best_file ]] && mv -f "$best_file" "$out"
    # **And the attempt that lost is removed**, so the output directory holds one
    # picture per target rather than two. The winner's own `.tryN.log` stays
    # beside it - a log named for the attempt that produced the picture is more
    # use than one named for the target - and `render_once`'s sandbox log is what
    # the NO NODE check below still reads.
    rm -f "${out}.try1" "${out}.try2"

    if (( best_pct >= MIN_CONTENT_PCT )); then
        printf '  PASS  %-28s %8s bytes (%s%% content%s)\n' \
            "$target" "$(stat -c%s "$out")" "$best_pct" "$note"
        return 0
    fi

    # **Say what was measured.** The old line said "no file written" whatever had
    # happened, which is false whenever a picture exists and was *rejected* - and
    # the gate that introduced the measurement is what made the sentence wrong.
    # Measured: `FAIL main no file written` beside a 23,374-byte file. A failure
    # line that sends the reader looking for a missing file rather than a
    # rejected one is the absence-shaped wrong answer this driver exists to catch,
    # and it was printing one of its own.
    if [[ -n $last_pct ]]; then
        printf '  FAIL  %-28s %8s bytes, %s%% content (floor %s%%)\n' \
            "$target" "$(stat -c%s "$out")" "$last_pct" "$MIN_CONTENT_PCT"
    else
        printf '  FAIL  %-28s no file written\n' "$target"
    fi
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