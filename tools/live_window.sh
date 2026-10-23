#!/usr/bin/env bash
# Photograph and measure the REAL Chronoa main window, off the person's screen.
#
#   tools/live_window.sh [-s WxH] [-e] [-t EXPR[,EXPR]] [-k] OUT.png
#
#   -s WxH   window size (default 460x640, the size the app opens at)
#   -e       empty window - no seeded conversation
#   -c       a coding turn instead: one real file edit, inline diff and all
#   -a       an edit waiting for approval, with the change shown in the question
#   -t EXPR  print the widget tree of EXPR (over `win`), e.g. -t win._mode_strip
#   -p ID    open this panel first (see `--list` in render_ui.py / SURFACE_IDS)
#   -P       open the command palette, so it can be looked at
#   -M JSON  preload the `mcp-servers` setting (and let discovery run)
#   -k       keep the app running after the shot (kill it yourself)
#
# What it sets up, and why each piece is load-bearing (all measured):
#
# - **A headless gnome-shell with a virtual monitor**, on its own Wayland
#   display name per run. On the person's desktop the window takes keyboard
#   focus and their typing lands in it (and is saved as a draft). Broadway left
#   the render node empty. A fixed display name raced the previous run's
#   compositor while it exited: "Gtk couldn't be initialized".
# - **A private D-Bus session**, because `dev.shani.chronoa` is a single-instance
#   name and any other run (a pytest building the window, another session) holds
#   it - a second launch exits with "Failed to register".
# - **A throwaway gnome-keyring in that session.** Without a secrets service each
#   lookup blocks the main loop for 25s, so the window maps in its pre-layout
#   state (split view 665px wide inside a 460px window) and nothing queued on
#   the loop - the narrow-layout fix included - runs. That looked exactly like a
#   clipping bug in the product.
# - **Its own XDG dirs** under the output's directory, so nothing touches the
#   person's conversations, settings or drafts.
#
# Output: OUT.png, and OUT.log with the app's log plus PROBE/TREE/SHOT lines.
set -u
SIZE=460x640 SEED=1 TREE="" QUIT=1 PAGE="" MCP=""
while getopts "s:ecamt:kp:M:P" opt; do
  case $opt in
    s) SIZE=$OPTARG ;;
    e) SEED=0 ;;
    c) SEED=code ;;
    P) SEED=palette ;;
    a) SEED=ask ;;
    m) SEED=media ;;
    t) TREE=$OPTARG ;;
    k) QUIT=0 ;;
    p) PAGE=$OPTARG ;;
    M) MCP=$OPTARG ;;
    *) sed -n '2,10p' "$0"; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
OUT=${1:?usage: tools/live_window.sh [-s WxH] [-e] [-t EXPR] [-k] OUT.png}
OUT=$(realpath -m "$OUT")
LOG=${OUT%.png}.log
REPO=$(cd "$(dirname "$0")/.." && pwd)
STATE=$(dirname "$OUT")/.live-window-state
mkdir -p "$STATE/st" "$STATE/da" "$STATE/cf"
# **No settings schema, on purpose.** Tried and reverted (2026-10-14): with the
# real schema the app's defaults start real services - measured, it opened a
# Bluetooth OBEX session to the person's paired phone and advertised over BLE
# as "Chronoa Remote" from inside a capture. Python defaults keep those off.
: > "$LOG"
DISPLAY_NAME=chronoa-live-$$

setsid env -u DISPLAY -u WAYLAND_DISPLAY GDK_BACKEND=wayland PYTHONDONTWRITEBYTECODE=1 \
  XDG_STATE_HOME="$STATE/st" XDG_DATA_HOME="$STATE/da" XDG_CONFIG_HOME="$STATE/cf" \
  PALETTE_SEARCH="${PALETTE_SEARCH:-}" \
  SIZE="$SIZE" SEED="$SEED" SHOT="$OUT" TREE="$TREE" QUIT="$QUIT" PAGE="$PAGE" MCP="$MCP" \
  dbus-run-session -- bash -c '
    echo -n "" | gnome-keyring-daemon --unlock --components=secrets >/dev/null
    gnome-shell --headless --wayland --no-x11 --virtual-monitor 1600x1000 \
      --wayland-display "$0" >/dev/null 2>&1 &
    shell=$!
    for _ in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/$0" ] && break; sleep 0.2; done
    WAYLAND_DISPLAY="$0" python3 "$1/tools/live_window.py" "$1"
    kill "$shell" 2>/dev/null' "$DISPLAY_NAME" "$REPO" >> "$LOG" 2>&1 < /dev/null &
# `setsid` made it a session leader, so its pid is the process group of
# everything below it: the app, its private bus, the headless shell, and any
# service the bus activated (an obexd was found outliving a run).
GROUP=$!
# **Always reaped.** Stopping the wait on a traceback used to leave the app
# running - measured, one kept a Bluetooth session and a BLE advertisement up
# after the capture had "finished". Whatever happens below, the group goes.
trap 'sleep 3; kill -- -"$GROUP" 2>/dev/null; sleep 1; kill -9 -- -"$GROUP" 2>/dev/null' EXIT

for _ in $(seq 120); do
  grep -aqE "^(SHOT|NO NODE)|Traceback" "$LOG" && break
  sleep 1
done
sleep 1
grep -aE "^(SHOT|NO NODE|PROBE|PAGE)|Traceback" "$LOG"
grep -aq "^SHOT" "$LOG"
