#!/usr/bin/env bash
# Photograph every panel (or the ones named) in the real app, one run each.
#
#   tools/live_panels.sh OUT_DIR [WxH] [panel ...]
#
# One `tools/live_window.sh -e -p ID` per panel, serially (each run owns a
# headless gnome-shell and a private bus; running them in parallel only makes
# them fight for CPU and the settle timeouts lie). Writes OUT_DIR/ID.png and
# ID.log, then OUT_DIR/sheet-N.png contact sheets, four panels per sheet, so a
# whole sweep can be read a few images at a time.
set -u
OUT=${1:?usage: tools/live_panels.sh OUT_DIR [WxH] [panel ...]}
SIZE=${2:-1000x760}
shift $(( $# >= 2 ? 2 : 1 ))
cd "$(dirname "$0")/.." || exit 1
mkdir -p "$OUT"
if [ $# -gt 0 ]; then
  ids="$*"
else
  ids=$(PYTHONPATH=usr/lib/shani-chronoa python3 -c \
    "from shani_chronoa.gui import surfaces; print(' '.join(surfaces.SURFACE_IDS))")
fi
for id in $ids; do
  if tools/live_window.sh -e -s "$SIZE" -p "$id" "$OUT/$id.png" > /dev/null; then
    echo "ok    $id"
  else
    echo "FAIL  $id  (see $OUT/$id.log)"
  fi
done
python3 - "$OUT" $ids <<'EOF'
import sys
from pathlib import Path
from PIL import Image, ImageDraw
out, ids = Path(sys.argv[1]), sys.argv[2:]
shots = [(i, out / f"{i}.png") for i in ids if (out / f"{i}.png").exists()]
for n in range(0, len(shots), 4):
    group = shots[n:n + 4]
    tiles = [Image.open(p).convert("RGB") for _, p in group]
    w, h = tiles[0].size
    sheet = Image.new("RGB", (w * 2, (h + 24) * 2), "white")
    draw = ImageDraw.Draw(sheet)
    for k, ((name, _), tile) in enumerate(zip(group, tiles)):
        x, y = (k % 2) * w, (k // 2) * (h + 24)
        draw.text((x + 8, y + 4), name, fill="black")
        sheet.paste(tile, (x, y + 24))
    sheet.save(out / f"sheet-{n // 4 + 1}.png")
print(f"{len(shots)} panels, {(len(shots) + 3) // 4} sheets in {out}")
EOF
