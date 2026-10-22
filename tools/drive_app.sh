#!/usr/bin/env bash
# Drive the real Chronoa app through a scripted run, off the person's screen.
#
#   tools/drive_app.sh [-m MODE] [-o OUTDIR] SCRIPT.json
#
#   -m MODE   answering mode to seed: cloud (default, free keyless gateways) or local
#   -o DIR    where screenshots land (default: a temp dir, printed at the end)
#   SCRIPT    a JSON list of steps for tools/drive_app.py (see its docstring)
#
# It stands up the same private headless GNOME + D-Bus + keyring as
# tools/live_window.sh, with one difference: a **schema-backed, clean** config
# in a throwaway XDG, seeded as a fresh user who picked the chosen mode. Radios
# and the phone senses are left at their shipped defaults (now off), so a run on
# a box with no real Bluetooth does not start OBEX threads.
#
# The app's own single-instance name means only one run at a time per bus; the
# private bus per run keeps concurrent runs apart. Everything is reaped on exit.
set -u
MODE=cloud OUT=""
while getopts "m:o:" opt; do
  case $opt in
    m) MODE=$OPTARG ;;
    o) OUT=$OPTARG ;;
    *) sed -n '2,12p' "$0"; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
SCRIPT=${1:?usage: tools/drive_app.sh [-m cloud|local] [-o OUTDIR] SCRIPT.json}
SCRIPT=$(realpath "$SCRIPT")
REPO=$(cd "$(dirname "$0")/.." && pwd)
STATE=$(mktemp -d /tmp/drive-app.XXXXXX)
[ -n "$OUT" ] || OUT="$STATE/shots"
mkdir -p "$OUT" "$STATE/st" "$STATE/da" "$STATE/cf"
LOG="$STATE/run.log"

# A fresh-user config seeded with dconf - the real backend, and unlike a
# pre-written keyfile it does not break `config.set` (a keyfile settings file
# created before the backend opens it makes every later write a silent no-op,
# which is a trap worth never stepping in again). Cloud = the free keyless path
# a new user gets by picking "In the cloud" and continuing; no key, no model to
# pick. `setup-complete` so the window opens on the conversation, not the wizard.
if [ "$MODE" = cloud ]; then
  SEED="answer-mode 'cloud'
setup-mode 'cloud'
setup-complete true
cloud-fallback-enabled true"
else
  SEED="answer-mode 'local'
setup-mode 'local'
setup-complete true"
fi

DISP=chronoa-drive-$$
setsid env -u DISPLAY -u WAYLAND_DISPLAY GDK_BACKEND=wayland PYTHONDONTWRITEBYTECODE=1 \
  XDG_STATE_HOME="$STATE/st" XDG_DATA_HOME="$STATE/da" XDG_CONFIG_HOME="$STATE/cf" \
  GSETTINGS_SCHEMA_DIR="$REPO/usr/share/glib-2.0/schemas" \
  REPO="$REPO" DRIVE_OUT="$OUT" DRIVE_SCRIPT="$SCRIPT" SEED="$SEED" \
  dbus-run-session -- bash -c '
    echo -n "" | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1
    while IFS= read -r line; do [ -n "$line" ] && dconf write "/org/shani/chronoa/${line%% *}" "${line#* }"; done <<< "$SEED"
    gnome-shell --headless --wayland --no-x11 --virtual-monitor 1600x1000 \
      --wayland-display "$0" >/dev/null 2>&1 &
    shell=$!
    for _ in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/$0" ] && break; sleep 0.2; done
    WAYLAND_DISPLAY="$0" python3 "$1/tools/drive_app.py"
    kill "$shell" 2>/dev/null' "$DISP" "$REPO" > "$LOG" 2>&1 &
GROUP=$!
trap 'sleep 2; kill -- -"$GROUP" 2>/dev/null; sleep 1; kill -9 -- -"$GROUP" 2>/dev/null' EXIT

for _ in $(seq 150); do grep -aqE "^DONE" "$LOG" && break; sleep 1; done
sleep 1
# The run's own lines: what it asked, switched, shot, and the audit.
grep -aE "^(ASK|CLOUD_MODEL|THINKING|SHOT|AUDIT|  (WIRED|UNWIRED)|ACTION ERR|STATE ERR|NO NODE|DONE)" "$LOG"
echo "shots: $OUT"
grep -aqE "^(SHOT|DONE)" "$LOG"
