"""Join recorded window frames into one video timeline at their real timing.

Frames are `main_NNNNN.png` (the Chronoa window, or the demo window) and,
for the real app, `browser_NNNNN.png` for the same tick. Each tick becomes one
picture - the two windows side by side - shown for exactly as long as it was
on screen, taken from the files' write times, so the video plays in real time.
"""

import os
import sys
from pathlib import Path

from PIL import Image

HEIGHT = 720
BROWSER_WIDTH = 1220


def _fit(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGB")
    return image.resize((round(image.width * HEIGHT / image.height), HEIGHT), Image.LANCZOS)


def compose(out: Path) -> float:
    """Write the joined frames and `concat.txt`; return the first frame's time."""
    frames, comp = out / "frames", out / "comp"
    comp.mkdir(exist_ok=True)
    ticks = sorted({f.split("_")[1][:5] for f in os.listdir(frames)})
    two = any(f.startswith("browser_") for f in os.listdir(frames))
    last = {"main": None, "browser": None}
    times, main_width = [], None
    for i, tick in enumerate(ticks):
        stamp = None
        for kind in ("main", "browser"):
            path = frames / f"{kind}_{tick}.png"
            if path.exists():
                last[kind] = _fit(path)
                stamp = stamp or path.stat().st_mtime
        times.append(stamp)
        main = last["main"]
        main_width = main_width or (main.width if main else 520)
        width = main_width + (BROWSER_WIDTH + 12 if two else 0)
        width += width % 2
        canvas = Image.new("RGB", (width, HEIGHT), (36, 36, 40))
        if main:
            canvas.paste(main, (0, 0))
        if two and last["browser"]:
            b = last["browser"]
            canvas.paste(b.crop((0, 0, min(BROWSER_WIDTH, b.width), HEIGHT)), (main_width + 12, 0))
        canvas.save(comp / f"c{i:05d}.jpg", quality=88)
    gaps = [max(0.02, b - a) for a, b in zip(times, times[1:])] + [4.0]
    with open(out / "concat.txt", "w") as f:
        for i, gap in enumerate(gaps):
            f.write(f"file 'comp/c{i:05d}.jpg'\nduration {gap:.3f}\n")
        f.write(f"file 'comp/c{len(gaps) - 1:05d}.jpg'\n")
    print(f"{len(ticks)} ticks over {times[-1] - times[0]:.1f}s", flush=True)
    return times[0]


if __name__ == "__main__":
    compose(Path(sys.argv[1]))
