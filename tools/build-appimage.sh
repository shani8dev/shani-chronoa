#!/usr/bin/env bash
# Build a Chronoa AppImage.
#
# What this bundles, and why each one is here:
#
#   python3 + stdlib      the whole application is Python; nothing to compile
#   gi (PyGObject)        every window is GTK4 via introspection
#   typelibs              the introspected namespaces, which are separate .typelib
#                         files, not libraries
#   GTK4 / libadwaita     the UI stack, pulled in transitively by the walker
#   httpx stack           the LLM transport; the assistant cannot reach a model
#                         without it, so unlike numpy/PIL it is not optional
#   bubblewrap            a HARD depends. Chronoa refuses to run a skill it
#                         cannot confine rather than pretending it was isolated.
#   tesseract + eng data  a HARD depends. The binary without language data reads
#                         nothing, and `ocr` reports itself unavailable rather
#                         than returning empty text.
#
# What this deliberately does NOT do, because the app guards it and degrades:
# cv2, matplotlib, mcp, sklearn, onnxruntime, symengine. Each is behind a
# guarded import in the package.
#
# Desktop integration is a real limitation, not a claim: the GNOME Shell
# extensions, the SearchProvider, the KRunner plugin and the systemd user units
# are all shipped inside the image and exported through XDG_DATA_DIRS, but
# gnome-shell caches its extension list and search providers, and systemd will
# not pick up units out of a squashfs mount. See README-appimage.md.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$REPO/build"
APPDIR="$BUILD/AppDir"
NAME="shani-chronoa"
VERSION="0.1.0"
DESKTOP_ID="dev.shani.chronoa.Chronoa"
ARCH="$(uname -m)"
OUT="$BUILD/${NAME}-${VERSION}-${ARCH}.AppImage"
# Kept inside build/ rather than /tmp: a build that depends on a scratch file is
# a build that stops being reproducible when the machine reboots.
APPIMAGETOOL="$BUILD/appimagetool-x86_64.AppImage"

# Distributions must not be bundled: the AppImage runtime provides its own, and
# shipping a second glibc is what makes AppImages fail in unpredictable ways.
GLIBC_RE='^(libc|libm|libdl|libpthread|librt|libutil|libresolv|libnsl|ld-linux.*)\.so'

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- scaffold ---

log "cleaning $APPDIR"
rm -rf "$APPDIR"
mkdir -p "$APPDIR"/{usr/bin,usr/lib,usr/share}

# ------------------------------------------------------------------ payload ---

log "copying the Chronoa payload (usr/)"
# __pycache__ is excluded on purpose: this repo has a test that fails if stray
# bytecode ships, and stale .pyc files are the classic cause.
tar -C "$REPO" -cf - \
    --exclude='__pycache__' --exclude='*.pyc' \
    usr | tar -C "$APPDIR" -xf -

# ------------------------------------------------------------------- python ---

log "bundling python3 and its standard library"
cp -L /usr/bin/python3.14 "$APPDIR/usr/bin/python3"
chmod 0755 "$APPDIR/usr/bin/python3"
cp -a /usr/lib/python3.14 "$APPDIR/usr/lib/python3.14"

log "bundling the python packages Chronoa imports unconditionally"
DP="$APPDIR/usr/lib/python3/dist-packages"
mkdir -p "$DP"
for p in gi httpx httpcore h11 h2 hyperframe anyio sniffio idna certifi \
         numpy PIL gguf cairo yaml _cffi_backend.py; do
    [ -e "/usr/lib/python3/dist-packages/$p" ] || continue
    cp -a "/usr/lib/python3/dist-packages/$p" "$DP/" 2>/dev/null || true
done
# certifi's CA bundle is data, and its cacert.pem lives at the package root.
[ -f /usr/lib/python3/dist-packages/certifi/cacert.pem ] && \
    cp -a /usr/lib/python3/dist-packages/certifi/cacert.pem "$DP/certifi/"

# The MCP server is an optional second entry point (the README says so
# explicitly), so a failure here must not fail the build - shani_chronoa.mcp
# reports "mcp package is not installed" and everything else still works. It is
# fetched rather than copied because the `mcp` wheel is not a system package.
# mcp 2.x is what pip resolves today, and shani_chronoa/mcp.py already prefers
# its MCPServer spelling, so no version pin is needed; if that ever changes the
# fallback to 1.x's FastMCP is right there in the module.
if command -v pip3 >/dev/null 2>&1; then
    log "adding the mcp SDK (optional entry point)"
    MCP_STAGE="$BUILD/.mcp-stage"
    rm -rf "$MCP_STAGE"
    if pip3 install --quiet --target "$MCP_STAGE" --no-compile mcp >/dev/null 2>&1; then
        find "$MCP_STAGE" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
        cp -a "$MCP_STAGE/." "$DP/"
        log "  mcp bundled"
    else
        log "  mcp unavailable (offline or no wheel) - the MCP server will report it"
    fi
    rm -rf "$MCP_STAGE"
else
    log "  pip3 not found - skipping mcp; the MCP server will report it"
fi

# ----------------------------------------------------------------- typelibs ---

log "bundling GObject introspection typelibs"
cp -a /usr/lib/x86_64-linux-gnu/girepository-1.0 "$APPDIR/usr/lib/"

# ------------------------------------------------------- hard system binaries ---

log "bundling bubblewrap (the skill sandbox)"
cp -L /usr/bin/bwrap "$APPDIR/usr/bin/bwrap"

log "bundling tesseract + english language data"
cp -L /usr/bin/tesseract "$APPDIR/usr/bin/tesseract"
TESS_SRC="$(dirname "$(find /usr/share -name eng.traineddata 2>/dev/null | head -1)")"
[ -n "$TESS_SRC" ] || die "eng.traineddata not found on this system"
mkdir -p "$APPDIR/usr/share/tesseract-ocr/5/tessdata"
cp -a "$TESS_SRC"/. "$APPDIR/usr/share/tesseract-ocr/5/tessdata/"

# ------------------------------------------------------- shared libraries ----

log "walking shared-library dependencies"

# Walk the transitive closure of every ELF we ship. Recursive on purpose: GTK4
# alone pulls in around forty libraries, and hand-listing them is how a build
# ends up working on the machine that wrote the list and nowhere else.
declare -A seen=()
queue=()

enqueue() {
    local f="$1"
    [ -n "$f" ] || return 0
    [ -f "$f" ] || return 0
    local base; base="$(basename "$f")"
    echo "$base" | grep -qE "$GLIBC_RE" && return 0
    [ -n "${seen[$base]:-}" ] && return 0
    seen[$base]=1
    queue+=("$f")
}

# Seeds: the three ELF binaries plus the C libraries behind every typelib we
# copied, since libgirepository dlopen()s those rather than linking them.
for seed in \
    /usr/bin/python3.14 /usr/bin/bwrap /usr/bin/tesseract \
    /usr/lib/python3/dist-packages/gi/_gi.cpython-314-x86_64-linux-gnu.so
do enqueue "$seed"; done

for tl in "$APPDIR"/usr/lib/girepository-1.0/*.typelib; do
    base="$(basename "$tl" .typelib)"          # Gtk-4.0
    mod="$(echo "$base" | sed 's/-/./g')"       # Gtk.4.0
    major="${mod##*.}"
    lib="lib$(echo "$mod" | cut -d. -f1 | tr 'A-Z' 'a-z')-$major.so.1"
    enqueue "$(ldconfig -p | grep -m1 -F "$lib " | awk '{print $NF}')"
done

mkdir -p "$APPDIR/usr/lib"
count=0
while [ ${#queue[@]} -gt 0 ]; do
    f="${queue[0]}"; queue=("${queue[@]:1}")
    cp -L "$f" "$APPDIR/usr/lib/" 2>/dev/null || continue
    count=$((count + 1))
    while read -r _ dep _; do
        [ -n "$dep" ] && enqueue "$dep"
    done < <(ldd "$f" 2>/dev/null | grep '=> /' | awk '{print $1, $3, $4}')
done
log "bundled $count shared libraries"

# ------------------------------------------------------------------- AppRun ---

log "writing AppRun"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/usr/bin/env bash
# Chronoa AppImage entry point.
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"

export LD_LIBRARY_PATH="$HERE/usr/lib:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="$HERE/usr/lib/girepository-1.0"
# PYTHONHOME is what makes the copied interpreter find the copied stdlib; without
# it CPython resolves its prefix from the real /usr and imports the host's.
export PYTHONHOME="$HERE/usr"
export PYTHONPATH="$HERE/usr/lib/shani-chronoa:$HERE/usr/lib/python3/dist-packages"
export PYTHONDONTWRITEBYTECODE=1
export GSETTINGS_SCHEMA_DIR="$HERE/usr/share/glib-2.0/schemas"
# TESSDATA_PREFIX points AT the tessdata directory, not at its parent: pointing
# one level too high makes tesseract look for .../5/eng.traineddata and report
# "couldn't load any languages", which reads as a broken build rather than a
# wrong path.
export TESSDATA_PREFIX="$HERE/usr/share/tesseract-ocr/5/tessdata"
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export PATH="$HERE/usr/bin:$PATH"

# bubblewrap is the one bundled binary that cannot be relied on from inside the
# image. Ubuntu ships an AppArmor profile (bwrap-userns-restrict) that permits
# unprivileged user namespaces BY BINARY PATH, so the bundled copy is refused
# with "setting up uid map: Permission denied" on exactly the distributions that
# need a sandbox most. No permission, capability or setuid bit fixes that: the
# profile simply does not match the new path, and installing one needs root.
#
# So prefer the host's bwrap when there is one - which is the binary the
# distribution actually supports, and what the Arch/Debian package uses - and
# fall back to the bundled copy on systems with no such restriction. Without
# either, Chronoa refuses to run a skill it cannot confine rather than
# pretending it was isolated, which is the honest outcome.
for host in /usr/bin/bwrap /bin/bwrap; do
    if [ -x "$host" ]; then
        shim="$(mktemp -d "${TMPDIR:-/tmp}/chronoa-shim.XXXXXX")"
        ln -sf "$host" "$shim/bwrap" 2>/dev/null && PATH="$shim:$PATH"
        export PATH
        break
    fi
done

# ---------------------------------------------------------- --register-desktop ---
# The GNOME Shell extensions, the search-provider ini and both GSettings schemas
# are shipped inside this image and AppRun exports XDG_DATA_DIRS, which is enough
# for GIO: a synthetic desktop entry placed on that path IS visible to
# `Gio.AppInfo.get_all()`.
#
# gnome-shell reads NEITHER of them, and that was measured rather than assumed on
# GNOME Shell 50.1:
#
#   - Extensions come from ~/.local/share/gnome-shell/extensions and
#     /usr/share/gnome-shell/extensions. It does NOT walk XDG_DATA_DIRS: the
#     extensions bundled here sit at $HERE/usr/share/gnome-shell/extensions, which
#     AppRun puts on XDG_DATA_DIRS, and `gnome-extensions list` does not show
#     them - while `caffeine@patapon.info`, dropped in the user dir by hand, is
#     listed. Same mechanism, opposite answer, so the path is the cause.
#   - The shell's own XDG_DATA_DIRS was captured at login (measured in
#     /proc/<gnome-shell>/environ) and cannot contain a squashfs mount that did
#     not exist yet.
#
# So this flag copies the files to the two places the shell actually reads. It is
# OFF unless asked for, because it writes into the user's home directory, and it
# enables nothing: an extension still has to be switched on in the shell, which
# is a separate act with its own consequence.
#
# The desktop entry is deliberately NOT installed, and the reason is worth more
# than the feature: an absolute `Exec=` pointing into the mount is worse than a
# bare name, because $HERE is a transient /tmp/.mount_XXXX for a normal run and
# the entry would break the moment the app exits. A copy that outlives the mount
# needs a launcher in ~/.local/bin, which is a different piece of work.
register_desktop() {
    local data="${XDG_DATA_HOME:-$HOME/.local/share}"
    local ext_src="$HERE/usr/share/gnome-shell/extensions"
    local sp_src="$HERE/usr/share/gnome-shell/search-providers"
    local schema_src="$HERE/usr/share/glib-2.0/schemas"
    local dest dir uuid n=0

    echo "Chronoa desktop integration -> $data"

    mkdir -p "$data/gnome-shell/extensions" "$data/glib-2.0/schemas" || return 1

    for dir in "$ext_src"/*/; do
        [ -d "$dir" ] || continue
        uuid="$(basename "$dir")"
        rm -rf "${data}/gnome-shell/extensions/$uuid"
        cp -r "$dir" "${data}/gnome-shell/extensions/$uuid" || return 1
        chmod -R u+rw,go+r "${data}/gnome-shell/extensions/$uuid"
        echo "  extension   $uuid"
        n=$((n + 1))
    done
    [ "$n" -gt 0 ] || { echo "  NO EXTENSIONS FOUND IN $ext_src" >&2; return 1; }

    for dir in "$sp_src"/*.ini; do
        [ -f "$dir" ] || continue
        mkdir -p "$data/gnome-shell/search-providers" || return 1
        cp "$dir" "$data/gnome-shell/search-providers/" || return 1
        echo "  search      $(basename "$dir")"
    done

    # Only the XML: copying gschemas.compiled would put a binary built against
    # this image's GLib in a directory the host's GLib will read.
    for dir in "$schema_src"/*.gschema.xml; do
        [ -f "$dir" ] || continue
        cp "$dir" "$data/glib-2.0/schemas/" || return 1
        echo "  schema      $(basename "$dir")"
    done

    if command -v glib-compile-schemas >/dev/null 2>&1; then
        glib-compile-schemas "$data/glib-2.0/schemas" \
            && echo "  schemas     compiled" \
            || { echo "  schemas     DID NOT COMPILE - the shell will ignore them" >&2; return 1; }
    else
        echo "  schemas     COPIED BUT NOT COMPILED - glib-compile-schemas is not on this machine."
        echo "               Run: glib-compile-schemas $data/glib-2.0/schemas"
    fi

    echo
    echo "Not enabled - enabling is the shell's decision and is not done for you:"
    echo "  gnome-extensions list          # are they there?"
    echo "  gnome-extensions enable <uuid> # one at a time, or use Extensions"
    echo "Extensions already enabled keep working; nothing was disabled or removed."
    return 0
}

args=(); want_register=0
for a in "$@"; do
    case "$a" in
        --register-desktop) want_register=1 ;;
        *) args+=("$a") ;;
    esac
done

if [ "$want_register" -eq 1 ]; then
    if [ "$#" -ne "${#args[@]}" ]; then
        register_desktop
        exit $?
    fi
    echo "--register-desktop takes no other arguments." >&2
    exit 2
fi

exec "$HERE/usr/bin/shani-chronoa" ${args[@]+"${args[@]}"}
APPRUN
chmod 0755 "$APPDIR/AppRun"

# -------------------------------------------------- desktop integration ------
# Shipped and exported through XDG_DATA_DIRS above. Whether gnome-shell picks
# any of it up is a separate question, answered by running it -- not by shipping.

log "installing the desktop entry and icon"
# Named after the application's actual application_id (dev.shani.chronoa), NOT
# after the binary. Gio.Notification is surfaced by GNOME only when a desktop
# file carries the app id's name; a file called shani-chronoa.desktop is found
# by the launcher but does not make Chronoa's own notifications appear.
# The content is the shipped entry verbatim, so the desktop actions and the
# single-instance hint survive.
APP_ID="$(grep -oP 'application_id="\K[^"]+' \
    "$REPO/usr/lib/shani-chronoa/shani_chronoa/app/application.py" | head -1)"
APP_ID="${APP_ID:-dev.shani.chronoa}"
cp -a "$REPO/usr/share/applications/shani-chronoa.desktop" \
      "$APPDIR/usr/share/applications/shani-chronoa.desktop"
cp -a "$REPO/usr/share/applications/shani-chronoa.desktop" \
      "$APPDIR/${APP_ID}.desktop"
log "desktop entry named ${APP_ID}.desktop (notifications depend on this)"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a "$REPO/usr/share/icons/hicolor/scalable/apps/shani-chronoa.svg" \
      "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
# appimagetool resolves the icon key at the AppDir root, so it must point at the
# real file. A relative "shani-chronoa.svg" here would point at itself.
ln -sfn usr/share/icons/hicolor/scalable/apps/shani-chronoa.svg \
       "$APPDIR/shani-chronoa.svg"
ln -sfn "${APP_ID}.desktop" "$APPDIR/shani-chronoa.desktop"

log "compiling GSettings schemas"
glib-compile-schemas "$APPDIR/usr/share/glib-2.0/schemas" || \
    die "glib-compile-schemas failed"

# ------------------------------------------------------------------ summary ---

du -sh "$APPDIR" | awk '{print "AppDir size: " $1}'
log "AppDir ready at $APPDIR"

# -------------------------------------------------------------------- squash ---

if [ "${1:-}" = "--squash" ]; then
    if [ ! -x "$APPIMAGETOOL" ]; then
        log "fetching appimagetool"
        curl -sL -o "$APPIMAGETOOL" \
          https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage \
          || die "could not download appimagetool"
        chmod +x "$APPIMAGETOOL"
    fi

    log "building $OUT"
    ARCH="$ARCH" "$APPIMAGETOOL" "$APPDIR" "$OUT" \
        "gh-releases-zsync|shani8dev|shani-chronoa|latest|${ARCH}.AppImage.zsync"

    printf '\n\033[1;32m==> %s\033[0m\n' "$(du -h "$OUT" | awk '{print $1}')  $OUT"
    echo "    test it:  $OUT --help"
else
    echo "    run it directly:  $APPDIR/AppRun"
    echo "    then build:       $0 --squash"
fi