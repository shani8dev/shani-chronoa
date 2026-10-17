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


def _widest_main(frames: Path) -> int:
    """The widest main frame of the take, at the video's height.

    **Not the first frame's width.** The window opens at 460x640 and the
    recorder then resizes it (to `CHRONOA_DEMO_MAIN_WIDTH`, 1300 for the coding
    demo); sizing the canvas from the first frame cut every later frame to its
    left 518 px - measured 2026-10-15, the orb, the request and every tool row
    ran off the right edge for the whole take.
    """
    widest = 0
    for name in os.listdir(frames):
        if name.startswith("main_"):
            with Image.open(frames / name) as image:
                w, h = image.size
            widest = max(widest, round(w * HEIGHT / h))
    return widest or 520


def compose(out: Path) -> float:
    """Write the joined frames and `concat.txt`; return the first frame's time."""
    frames, comp = out / "frames", out / "comp"
    comp.mkdir(exist_ok=True)
    ticks = sorted({f.split("_")[1][:5] for f in os.listdir(frames)})
    two = any(f.startswith("browser_") for f in os.listdir(frames))
    last = {"main": None, "browser": None}
    times, main_width = [], _widest_main(frames)
    for i, tick in enumerate(ticks):
        stamp = None
        for kind in ("main", "browser"):
            path = frames / f"{kind}_{tick}.png"
            if path.exists():
                last[kind] = _fit(path)
                stamp = stamp or path.stat().st_mtime
        times.append(stamp)
        main = last["main"]
        width = main_width + (BROWSER_WIDTH + 12 if two else 0)
        width += width % 2
        canvas = Image.new("RGB", (width, HEIGHT), (36, 36, 40))
        if main:
            # A narrower frame (before the window was resized) is centred in
            # the main column, never stretched and never cropped.
            canvas.paste(main, ((main_width - main.width) // 2, 0))
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
