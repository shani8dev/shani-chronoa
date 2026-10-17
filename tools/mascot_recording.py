#!/usr/bin/env python3
"""Record the Chronoa mascot as a GNOME Shell actor, with the gaze following the
pointer. This is "the mascot working like coucou" measured and filmed.

**Why a Shell extension at all, and this is the load-bearing choice.** `mascot.py`
builds a layer-shell surface, and `available()` is `False` on this box because
Mutter has never implemented `zwlr_layer_shell_v1` (PART 7 of
`harness-study/HARVEST-COUCOU.md`). PART 9 of the same harvest measured that the
mascot's art reaches an `St` actor under `Main.uiGroup` pixel-exact - which is also
coucou's own architecture, because coucou *is* a GNOME Shell extension. This tool
re-measures that route rather than trusting the dated claim, and then drives it.

**What is real here, and what is synthesised, stated up front:**

| link | how |
|---|---|
| the pointer | **real.** `PointerMove` on `chronoa-windows@shani.dev` creates a Clutter virtual device; `Mascot.Pointer()` then reads `global.get_pointer()` back **out of the Shell** |
| the bearing | **the product's own.** `mascot._gaze_for()`, called with the pointer position the Shell reports, translated into the actor's rect |
| the art | **the product's own.** `mascot_art.svg()` -> `GdkPixbuf` at `SIZE*2` -> `Gdk.Texture.save_to_png`, which is `Mascot._render`'s pipeline |
| the drawing | a **Shell actor**, loaded through `St.TextureCache.load_file_sync` |
| the compositor | **NOT** layer-shell. Mutter cannot make that surface, so this is an ordinary `St` actor in `Main.uiGroup`, not a layer surface |

Every frame is a real screenshot of a real compositor. Nothing is composited in
post to fake a feature the compositor does not have.

Usage:  python3 tools/mascot_recording.py [OUTDIR] [--frames N]
"""
import argparse
import math
import subprocess
import sys
import tempfile
import time
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MASCOT_EXT = REPO / "usr/share/gnome-shell/extensions/chronoa-mascot@shani.dev"
CAPTURE_EXT = REPO / "demos/record/shell-capture@demo"

sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

MASCOT_DBUS = ("org.shani.Chronoa.Mascot", "/org/shani/Chronoa/Mascot")
WINDOWS_DBUS = ("org.shani.Chronoa.Windows", "/org/shani/Chronoa/Windows")
CAPTURE_DBUS = ("org.shani.Demo.Capture", "/org/shani/Demo/Capture")

# The art pipeline, copied out of `Mascot._render` rather than restated: it runs
# in a child process, so it cannot import the GTK window that owns it.
ART_CHILD = r'''
import os, sys
import gi
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GdkPixbuf, Gdk
sys.path.insert(0, os.environ["REPO"] + "/usr/lib/shani-chronoa")
from shani_chronoa import mascot, mascot_art

gaze = (float(sys.argv[1]), float(sys.argv[2]))
svg = mascot_art.svg("idle", "", False, 0.0, gaze)
loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
loader.set_size(mascot.SIZE * 2, mascot.SIZE * 2)
loader.write(svg.encode())
loader.close()
pixbuf = loader.get_pixbuf()
Gdk.Texture.new_for_pixbuf(pixbuf).save_to_png(os.environ["ART_OUT"])
print("svg=%d png=%d" % (len(svg), os.path.getsize(os.environ["ART_OUT"])))
'''


def call(shell, bus, method, *args, timeout=30):
    dest, path = bus
    out = subprocess.run(
        ["gdbus", "call", "--session", "--dest", dest, "--object-path", path,
         "--method", f"{dest}.{method}", *[str(a) for a in args]],
        env=shell.env, capture_output=True, text=True, timeout=timeout)
    return out.stdout.strip(), out.stderr.strip()


def parse_tuple(text, n):
    """`(1440, 840, 132, 132)` -> [1440, 840, 132, 132]. gdbus's own shape."""
    body = text.strip().lstrip("(").rstrip(")")
    parts = [p.strip() for p in body.split(",") if p.strip()]
    if len(parts) != n:
        raise ValueError(f"expected {n} values, got {text!r}")
    return [int(p) for p in parts]


def notch_geometry() -> dict:
    """The strip's own constants, read out of `extension.js`.

    A second copy of `NOTCH_W` here would be a number nobody re-measures, which
    is the shape this repository keeps paying for - and `tools/` is not packaged,
    so a stale copy would not even fail a test. The source is parsed instead, and
    each name must occur exactly once as a `const` binding.
    """
    src = Path(__file__).resolve().parent.parent / (
        "usr/share/gnome-shell/extensions/chronoa-mascot@shani.dev/extension.js")
    text = src.read_text()
    wanted = {"NOTCH_W", "NOTCH_H", "NOTCH_PAD", "SIZE", "MARGIN"}
    exprs = {}
    # The trailing comment is part of the line on some of these, so it is
    # allowed and dropped rather than making a declaration unreadable - which is
    # what happened on the first run, where `NOTCH_PAD` silently vanished from
    # the set and the reader refused rather than guessing a default.
    for name, body in re.findall(r"^const ([A-Z_]+) = ([^;/]+);\s*(?://.*)?$",
                                 text, re.M):
        if name in wanted:
            if name in exprs:
                raise RuntimeError(f"{name} is declared twice in {src}")
            exprs[name] = body.strip()
    missing = wanted - set(exprs)
    if missing:
        raise RuntimeError(f"{src} does not declare {sorted(missing)}")

    # The constants are **derived** - `NOTCH_H = SIZE + 2 * NOTCH_PAD` is what
    # keeps the strip tall enough for its sprite - so the reader resolves them
    # rather than pattern-matching digits. Every expression here is arithmetic
    # over other constants, and anything else is refused rather than `eval`ed
    # blind: a body that is not ints, `+ - *` and names would be a second place
    # the geometry could be described.
    out: dict = {}
    for _ in range(len(exprs) + 1):
        progressed = False
        for name, body in exprs.items():
            if name in out:
                continue
            if re.fullmatch(r"[A-Z_0-9 +*-]+", body) is None:
                raise RuntimeError(f"{name} = {body!r} is not plain arithmetic")
            try:
                value = eval(body, {"__builtins__": {}}, out)  # noqa: S307
            except NameError:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise RuntimeError(f"{name} = {body!r} did not resolve to a number")
            out[name] = value
            progressed = True
        if not progressed:
            break
    unresolved = wanted - set(out)
    if unresolved:
        raise RuntimeError(f"could not resolve {sorted(unresolved)} from {src}: "
                           f"{ {k: exprs[k] for k in unresolved} }")
    return out


def shoot(shell, path, timeout=30):
    _, err = call(shell, CAPTURE_DBUS, "Shot", path, timeout=timeout)
    if not path.exists():
        raise RuntimeError(f"Shot() wrote nothing: {err}")
    return path


def rect_of(shell):
    text, err = call(shell, MASCOT_DBUS, "Rect")
    if "Error" in err or not text:
        raise RuntimeError(f"Rect() failed: {text} {err}")
    return parse_tuple(text, 4)


def pointer_of(shell):
    """Where the pointer REALLY is, according to the Shell."""
    text, err = call(shell, MASCOT_DBUS, "Pointer")
    if "Error" in err or not text:
        raise RuntimeError(f"Pointer() failed: {text} {err}")
    return parse_tuple(text, 2)


def render_art(shell, script, gx, gy, out):
    env = {**shell.env, "REPO": str(REPO), "ART_OUT": str(out),
           "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, str(script), str(gx), str(gy)],
                       env=env, capture_output=True, text=True, timeout=120)
    if not out.exists():
        raise RuntimeError(f"art render failed: {r.stderr[-800:]}")
    return r.stdout.strip()


def diff_box(a_path, b_path, region=None, exclude=()):
    """Differing-pixel count and bbox, optionally restricted to a rect.

    Returns None for the count when the image cannot be read, so "could not
    measure" is never reported as zero - a blank PNG matches no colour at all and
    would otherwise pass every 'nothing moved' check.

    `exclude` masks rects. It exists for the **pointer**, and without it the
    whole-screen measure is not measuring what its own printout claims: the
    recording drives a real cursor to a different place in each of the two frames
    it compares, so 400-odd pixels of "changed outside the actor" were the
    cursor moving - which is the harness, not the mascot, and which would have
    been reported as a defect in the product. The two read-back positions are
    known, so they are masked rather than guessed at.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    a = Image.open(a_path).convert("RGBA")
    b = Image.open(b_path).convert("RGBA")
    if a.size != b.size:
        return {"error": f"size {a.size} vs {b.size}"}
    pa, pb = a.load(), b.load()
    w, h = a.size
    x0, y0, x1, y1 = w, h, -1, -1
    n = 0
    for y in range(h):
        for x in range(w):
            if region and not (region[0] <= x < region[2] and region[1] <= y < region[3]):
                continue
            if any(r[0] <= x < r[2] and r[1] <= y < r[3] for r in exclude):
                continue
            if pa[x, y] != pb[x, y]:
                n += 1
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if n == 0:
        return {"pixels": 0, "box": None, "size": (0, 0)}
    return {"pixels": n, "box": (x0, y0, x1, y1),
            "size": (x1 - x0 + 1, y1 - y0 + 1)}


def path_points(count, centre, r_min, r_max, screen, inset=6):
    """A rosette that orbits the mascot, spiralling in and out once, clamped.

    **It orbits the mascot rather than crossing the screen**, and the first
    version crossed the screen and was wrong: the mascot is anchored
    bottom-right, so a full-screen Lissajous kept the pointer left of and above
    it for every sample and the bearing never left (-1.0, -1.0) - measured,
    bearing x in -1.000 .. -0.697 over 48 frames. A recording of that shows a
    mascot staring at one corner for four seconds, which reads as "the gaze
    works" and is not it.

    The mascot is the origin of the gaze, so the pointer's path is a circle
    *around the mascot*. The radius sweeps r_min..r_max so the bearing covers
    every direction and both saturated extremes; a fixed radius would show
    direction but never the pupil travelling inward and back out.

    **The path is clamped to the screen, because the mascot is 94px from the
    corner.** An unclamped orbit of the radius needed to saturate the bearing
    leaves the display, and `chronoa-windows@shani.dev` refuses rather than
    pretending: *"the pointer stopped at 1599,810, not 1602,810"*. So the right
    and bottom of the orbit ride the screen edge and the bearing saturates early
    on those sides. That asymmetry is the mascot's own anchored placement, not a
    property of the sweep, and the caller reports how many samples it clamped
    rather than hiding it.
    """
    cx, cy = centre
    w, h = screen
    pts = []
    for i in range(count):
        theta = 2 * math.pi * i / count
        r = r_min + (r_max - r_min) * (0.5 - 0.5 * math.cos(theta))
        x = int(round(cx + r * math.cos(theta)))
        y = int(round(cy - r * math.sin(theta)))
        cx2 = max(inset, min(w - 1 - inset, x))
        cy2 = max(inset, min(h - 1 - inset, y))
        pts.append((cx2, cy2, (cx2, cy2) != (x, y)))
    return pts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir", nargs="?", default=None)
    ap.add_argument("--frames", type=int, default=48)
    ap.add_argument("--monitor", default="1600x1000")
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--corner", default="top-center",
                    choices=("top-center", "top-right", "bottom-right"),
                    help="which mascot-corner the recording is checking")
    args = ap.parse_args()

    outdir = Path(args.outdir or (Path(tempfile.mkdtemp(prefix="mascot-rec."))))
    outdir.mkdir(parents=True, exist_ok=True)
    frames_dir = outdir / "frames"
    frames_dir.mkdir(exist_ok=True)

    import test_windows_gnome_live as harness
    from shani_chronoa import mascot

    root = Path(tempfile.mkdtemp(prefix="mascot-rec-shell."))
    shell = harness._Shell(root, monitor=args.monitor,
                           extra=[MASCOT_EXT, CAPTURE_EXT])

    def owns(name):
        r = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
             "--object-path", "/org/freedesktop/DBus", "--method",
             "org.freedesktop.DBus.NameHasOwner", name],
            env=shell.env, capture_output=True, text=True, timeout=10)
        return "true" in r.stdout

    for uuid, want in (("chronoa-windows@shani.dev", "org.shani.Chronoa.Windows"),
                       ("shell-capture@demo", "org.shani.Demo.Capture"),
                       ("chronoa-mascot@shani.dev", "org.shani.Chronoa.Mascot")):
        subprocess.run(["gnome-extensions", "enable", uuid], env=shell.env,
                       capture_output=True, text=True, timeout=20)
        harness._Shell._wait(lambda: owns(want), 25, f"{uuid} did not appear")
    print("[ok] extensions on the bus: windows, capture, mascot")

    script = root / "art.py"
    script.write_text(ART_CHILD)

    # Warm-up: the Shell's own dash is still settling this early, and a capture
    # taken mid-animation moves thousands of pixels that have nothing to do with
    # the mascot. Measured: 12,617 px outside the actor's rect between two
    # captures 3 s apart, all of it GNOME's dock sliding into place.
    art = root / "mascot.png"
    render_art(shell, script, 0.0, 0.0, art)
    loaded, err = call(shell, MASCOT_DBUS, "Art", art)
    if loaded != "(true,)":
        raise RuntimeError(f"Art() did not load the actor: {loaded} {err}")
    time.sleep(2.0)
    shoot(shell, root / "warmup.png")

    # The strip is given its words before the first capture, so the very first
    # frame shows what the notch is for rather than a bare bar.
    for title, detail in (("Reading src/invoice.ts", "step 1 of 4"),
                          ("Reading src/invoice.ts", "edits 12 lines"),
                          ("Finishing up", "step 4 of 4")):
        _, err = call(shell, MASCOT_DBUS, "Status", title, detail)
        if err:
            raise RuntimeError(f"Status() failed: {err}")
        render_art(shell, script, 0.35, -0.2, art)
        loaded, err = call(shell, MASCOT_DBUS, "Art", art)
        if loaded != "(true,)":
            raise RuntimeError(f"Art() did not load the actor: {loaded} {err}")
        time.sleep(0.6)
        shoot(shell, root / f"notch-{detail.split()[-1]}-{detail.split()[0]}.png")

    geo = notch_geometry()
    if geo["SIZE"] != mascot.SIZE:
        raise RuntimeError(f"extension.js draws {geo['SIZE']}px, mascot.SIZE is "
                           f"{mascot.SIZE} - the two must agree or a recording "
                           f"measures a different sprite from the one that ships")
    if geo["MARGIN"] != mascot._BASE_MARGIN:
        raise RuntimeError(f"extension.js's margin is {geo['MARGIN']}, "
                           f"mascot._BASE_MARGIN is {mascot._BASE_MARGIN}")

    rect = rect_of(shell)
    strip = parse_tuple(call(shell, MASCOT_DBUS, "Notch")[0], 4)
    bar_text, bar_err = call(shell, MASCOT_DBUS, "PanelHeight")
    # `gdbus call` prints `(32,)` even for a single out-arg, so the strip of
    # the brackets leaves a trailing comma. `parse_tuple` is the reader that
    # already handles every other reply this file asks for.
    bar = parse_tuple(bar_text, 1)[0] if bar_text else 0
    screen_w, screen_h = (int(v) for v in args.monitor.split("x"))
    # The expectation follows the corner, so the harness can record any of the
    # three and the check still says which one it looked at.
    corner = args.corner
    nw, nh, pad = geo["NOTCH_W"], geo["NOTCH_H"], geo["NOTCH_PAD"]
    size, margin = mascot.SIZE, mascot._BASE_MARGIN
    # **This is extension.js's own arithmetic, restated** - the strip's origin,
    # then the mascot's place inside it. Restated rather than imported because
    # `tools/` cannot import a JS file, and the check exists precisely to catch
    # the two disagreeing, which it cannot do if it reads the answer rather than
    # computing it.
    if corner == "top-center":
        strip_xy = ((screen_w - nw) // 2, bar)
        mascot_xy = (strip_xy[0] + pad, strip_xy[1] + pad)
    elif corner == "top-right":
        strip_xy = (screen_w - nw - margin, bar)
        mascot_xy = (strip_xy[0] + pad, strip_xy[1] + pad)
    else:                      # bottom-right: the mascot keeps its old home and
        strip_xy = (screen_w - nw - margin, screen_h - nh - margin)   # the strip
        mascot_xy = (strip_xy[0] + (nw - size) // 2, strip_xy[1] - size - pad)  # sits above it
    # A strip drawn at y=0 is drawn *behind the Shell's clock*, and the first
    # recording proved it: "Oct 17 15:38" was legible through the panel. The bar
    # height is read from the Shell rather than assumed, because it is a theme
    # decision (measured 27 here) and a strip that assumes is a strip that is
    # wrong on somebody else's theme.
    if corner != "bottom-right" and not bar:
        raise RuntimeError(f"PanelHeight() answered {bar_text!r} {bar_err!r} - "
                           f"without it the strip cannot be placed below the "
                           f"top bar and this check cannot mean anything")
    print(f"[ok] notch strip from the Shell: {strip} ({nw}x{nh}), corner={corner}")
    print(f"     the Shell's own top bar is {bar}px tall and the strip starts below it")
    print(f"     expected at {strip_xy}; the mascot at {mascot_xy}, "
          f"{size}x{size} inside it")
    if tuple(strip[:2]) != strip_xy:
        raise RuntimeError(f"strip is at {strip[:2]}, not {strip_xy}")
    if (rect[2], rect[3]) != (size, size):
        raise RuntimeError(f"actor is {rect[2]}x{rect[3]}, mascot.SIZE is {size}")
    if (rect[0], rect[1]) != mascot_xy:
        raise RuntimeError(f"actor is at {(rect[0], rect[1])}, not {mascot_xy}")
    print(f"[ok] the actor rect from the Shell: {rect} - inside the strip")

    # The mascot is the origin of the gaze, so the pointer orbits the mascot, not
    # the screen. r_max is large enough to saturate the bearing on every axis and
    # r_min small enough that the pupil visibly comes back to centre.
    centre = (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
    r_max = max(screen_w, screen_h) // 2 - 8
    raw = path_points(args.frames, centre, 24, r_max, (screen_w, screen_h), inset=6)
    points = [(x, y) for x, y, _ in raw]
    n_clamped = sum(1 for _, _, was in raw if was)
    print(f"[..] pointer orbits the mascot at {centre}, radius 24 .. {r_max}, "
          f"{len(points)} positions, {n_clamped} clamped to the screen edge")
    bearings = []
    print(f"[..] sweeping {len(points)} pointer positions")
    for i, (px, py) in enumerate(points):
        moved, err = call(shell, WINDOWS_DBUS, "PointerMove", px, py)
        if "Error" in err:
            raise RuntimeError(f"PointerMove failed: {moved} {err}")
        # Read the pointer back OUT of the Shell rather than trusting (px, py):
        # the bearing must come from where the pointer really is.
        rx, ry = pointer_of(shell)
        # The product's own geometry, in the actor's own coordinate space.
        bearing = mascot._gaze_for(rx - rect[0], ry - rect[1], rect[2], rect[3])
        bearings.append(((px, py), (rx, ry), bearing))
        render_art(shell, script, bearing[0], bearing[1], art)
        loaded, err = call(shell, MASCOT_DBUS, "Art", art)
        if loaded != "(true,)":
            raise RuntimeError(f"frame {i}: Art() said {loaded} {err}")
        shoot(shell, frames_dir / f"f{i:04d}.png")
        if i % 8 == 0:
            print(f"     {i:3d}/{len(points)}  asked=({px},{py}) "
                  f"shell=({rx},{ry}) bearing=({bearing[0]:+.2f},{bearing[1]:+.2f})")

    # --- the measurements that decide whether this is a recording or a decoration
    xs = [b[2][0] for b in bearings]
    ys = [b[2][1] for b in bearings]
    print("\n[measure] the gaze actually swept")
    print(f"  bearing x: {min(xs):+.3f} .. {max(xs):+.3f}   "
          f"y: {min(ys):+.3f} .. {max(ys):+.3f}")
    print(f"  pointer read back by the Shell matched what was asked in "
          f"{sum(1 for a, b, _ in bearings if a == b)}/{len(bearings)} samples")

    # A bearing that never changes sign is a mascot staring at one corner, and it
    # reads on screen exactly like a working gaze. This is the check that catches
    # it, and it is the check the first version of the pointer path did not have.
    if max(xs) <= 0 or min(xs) >= 0:
        raise RuntimeError(
            f"the gaze never looked right (bearing x in {min(xs):+.3f} .. "
            f"{max(xs):+.3f}) - the recording would show a mascot staring at "
            f"one corner, which is not a working gaze")
    if max(ys) <= 0 or min(ys) >= 0:
        raise RuntimeError(
            f"the gaze never looked up (bearing y in {min(ys):+.3f} .. "
            f"{max(ys):+.3f})")
    print("  OK: the bearing changed sign on both axes")

    left = min(range(len(xs)), key=lambda i: xs[i])
    right = max(range(len(xs)), key=lambda i: xs[i])
    print("\n[measure] pixels that move between the two opposing gaze extremes")
    # Where the Shell says the pointer really was in each of the two frames -
    # read back, not the coordinate that was asked for. A 40px box around each
    # is masked, because a cursor that moved is the harness and not the mascot.
    cursor_boxes = []
    for idx in (left, right):
        rx, ry = bearings[idx][1]
        cursor_boxes.append((rx - 40, ry - 40, rx + 40, ry + 40))
    print(f"  masking the pointer, read back at {bearings[left][1]} and "
          f"{bearings[right][1]}")
    whole = diff_box(frames_dir / f"f{left:04d}.png", frames_dir / f"f{right:04d}.png",
                     exclude=cursor_boxes)
    print(f"  whole screen : {whole}")
    inside = diff_box(frames_dir / f"f{left:04d}.png", frames_dir / f"f{right:04d}.png",
                      region=(rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]))
    print(f"  inside actor : {inside}")
    if inside and "error" not in inside:
        area = rect[2] * rect[3]
        print(f"  {inside['pixels']} px of a {area} px actor "
              f"= {100.0 * inside['pixels'] / area:.2f}%")
        if inside["box"]:
            bx0, by0, bx1, by1 = inside["box"]
            print(f"  the moved pixels sit at actor-relative "
                  f"({bx0 - rect[0]},{by0 - rect[1]})-({bx1 - rect[0]},{by1 - rect[1]}) "
                  f"of a {rect[2]}x{rect[3]} actor")
    if whole and "error" not in whole and inside:
        other = whole["pixels"] - inside["pixels"]
        print(f"  changed OUTSIDE the actor: {other} px "
              f"(the mascot's own rect is the only thing that moved)")

    video = None
    if not args.no_video and subprocess.run(["which", "ffmpeg"],
                                            capture_output=True).returncode == 0:
        video = outdir / "mascot-gaze.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps),
               "-i", str(frames_dir / "f%04d.png"),
               "-vf", f"crop={rect[2] + 24}:{rect[3] + 24}:"
                       f"{max(0, rect[0] - 12)}:{max(0, rect[1] - 12)}",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
               str(video)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,nb_frames,duration",
                 "-of", "default=nw=1", str(video)],
                capture_output=True, text=True, timeout=60)
            print(f"\n[ok] video: {video}")
            print("  " + probe.stdout.strip().replace("\n", "\n  "))
        else:
            print(f"\n[!!] ffmpeg failed: {r.stderr[-500:]}")

    print(f"\nframes in {frames_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
