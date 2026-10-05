"""When a tool is missing, say how to still get there.

`capability.recommend()` - and any honest report of a missing dependency -
ends at a dead end: "magick is not installed, nothing will work." That is
accurate and useless. A person asking for an image edit does not want to be told
the answer is no; they want the route that still works.

So every capability this module knows about carries up to **three** of them,
in the order a person would actually take:

1. **install** - the Arch package that provides it. Usually one command, and
   usually already on the machine's ISO, so this is the cheapest answer.
2. **substitute** - something *already here* that does the same job. `convert`
   for `magick`, `gst-launch` for a missing player, the stdlib for a missing
   library. This is the answer that works with no network and no root.
3. **fallback** - do it in Python, with no external tool at all. This always
   exists for anything that is a file format rather than a device.

**A route is only offered if its own prerequisites are here.** Suggesting
`ffmpeg` to convert audio when `ffmpeg` is the thing that is missing is a
recommendation that wastes the reader's time, so each route names what it needs
and the resolver checks those against the live machine before offering it.

The rule that keeps this honest: **never offer a route whose dependency is the
one that is missing.** Every entry below is verified against a real
`shutil.which` before it is printed.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple


class Route(NamedTuple):
    """One way to get there, and what it needs."""

    #: What the user does.
    action: str
    #: Commands or modules this route depends on. Empty means "works already".
    needs: Tuple[str, ...] = ()
    #: Which of the three kinds this is, so the reply can group them.
    kind: str = "substitute"


#: capability -> the ways round it. Deliberately small and deliberate: a table
#: of routes is only worth reading if every entry has been checked.
ROUTES: Dict[str, Tuple[Route, ...]] = {
    "magick": (
        Route("install the 'imagemagick' package", ("magick",), "install"),
        Route("use GraphicsMagick's 'gm convert', if installed", ("gm",)),
        Route("use 'ffmpeg', which is present on most images and reads and "
              "writes images", ("ffmpeg",)),
        Route("use Pillow from Python - it is on the image as 'python-pillow' "
              "and covers resize, rotate, convert and basic filters",
              ("python:PIL",)),
    ),
    "ffmpeg": (
        Route("install the 'ffmpeg' package", ("ffmpeg",), "install"),
        Route("use 'sox', which handles audio conversion on its own",
              ("sox",)),
        Route("use Python's stdlib 'wave' and 'audioop' for uncompressed "
              "WAV - enough to read, write and trim", ("python:wave",)),
    ),
    "sox": (
        Route("install the 'sox' package", ("sox",), "install"),
        Route("use 'ffmpeg', which covers audio filters and effects",
              ("ffmpeg",)),
        Route("use Python's stdlib 'wave' module for plain WAV work", ("python:wave",)),
    ),
    "tesseract": (
        Route("install the 'tesseract' and 'tesseract-data-eng' packages",
              ("tesseract",), "install"),
        Route("the 'scanimage' already here gives a page image; text still "
              "needs a recogniser", ("scanimage",)),
    ),
    "zbarimg": (
        Route("install the 'zbar' package", ("zbarimg",), "install"),
        Route("decode a QR in Python with the 'pyzbar' or 'opencv' module if "
              "either is installed", ("python:cv2",)),
        Route("encode one with 'qrencode' if that is installed", ("qrencode",)),
    ),
    "qrencode": (
        Route("install the 'qrencode' package", ("qrencode",), "install"),
        Route("generate the QR in Python with 'qrcode' or 'segno' if installed",
              ("python:qrcode", "python:segno")),
    ),
    "piper": (
        Route("install 'piper-tts' and a voice model, or use the "
              "setup wizard's Speech page", ("piper",), "install"),
        Route("use 'espeak-ng', which is present and needs no model file",
              ("espeak-ng",)),
        Route("speak with the browser's own speech synthesis through D-Bus, if "
              "a desktop portal is available", ()),
    ),
    "espeak-ng": (
        Route("install the 'espeak-ng' package", ("espeak-ng",), "install"),
    ),
    "pdftotext": (
        Route("install the 'poppler' package", ("pdftotext",), "install"),
        Route("read the PDF in Python with 'pypdf' or 'pdfminer' if installed",
              ("python:pypdf", "python:pdfminer")),
        Route("render pages with 'pdftoppm' and read those as images, if "
              "poppler is installed", ("pdftoppm",)),
    ),
    "libreoffice": (
        Route("install the 'libreoffice-fresh' package", ("libreoffice",), "install"),
        Route("read the file directly - a .docx is a zip of XML that Python's "
              "stdlib opens, and a .csv needs nothing at all", ()),
    ),
    "whisper-cli": (
        Route("install 'whisper-cpp' and a ggml model", ("whisper-cli",), "install"),
        Route("transcribe with a cloud provider, if one is configured and "
              "privacy mode allows it", ()),
    ),
    "virsh": (
        Route("install 'libvirt' and run 'virtqemud'", ("virsh",), "install"),
        Route("use 'qemu-system-x86_64' directly, which needs no libvirt at all",
              ("qemu-system-x86_64",)),
        Route("use 'podman', which is present, for anything that is really a "
              "container", ("podman",)),
    ),
    "lsof": (
        Route("install the 'lsof' package", ("lsof",), "install"),
        Route("read /proc directly - port_owner already does exactly this and "
              "needs nothing installed", ()),
    ),
    "rsync": (
        Route("install the 'rsync' package", ("rsync",), "install"),
        Route("copy with Python's shutil, which handles most one-off copies", ()),
        Route("copy with 'cp -a' from coreutils, which is always present",
              ("cp",)),
    ),
    "zstd": (
        Route("install the 'zstd' package", ("zstd",), "install"),
        Route("use Python's stdlib 'lzma' for .xz, or 'gzip' for .gz", ()),
    ),
}


def _available(need: str, cap) -> bool:
    """Is this route's own prerequisite here?

    `python:name` means an importable module; anything else is a command.

    **The one thing this may never do is suppress an install route.** An install
    route exists precisely *because* the command is absent, so filtering it on
    the command's presence removes the only route that fixes the problem - and
    the reply then reads "there is no way to install it". That bug removed the
    install option from every capability in the table at once.
    """
    if need.startswith("python:"):
        return bool(cap.python.get(need[7:], False))
    return need in cap.commands


def _route_is_viable(route: Route, missing: str, cap) -> bool:
    """Whether a route can be taken right now.

    An install route is always viable *because* the thing is missing. A
    substitute is viable only when what it substitutes with is actually here -
    offering ffmpeg to someone who has no ffmpeg wastes their time, which is the
    failure the whole table exists to avoid.
    """
    if route.kind == "install":
        return True
    return all(_available(need, cap) for need in route.needs)


def learned_first(missing: str, cap) -> List[Route]:
    """Routes ordered by what has actually worked on *this* machine.

    **This is where the non-deterministic part belongs.** The table below is
    hand-written and therefore a guess about every machine; the bandit is a
    measurement of this one. A machine where `ffmpeg` has handled twenty image
    edits should be told about `ffmpeg` first, and a machine where it failed
    fifteen times should be told to install the package instead.

    So: viable routes first, in the order this machine's own record supports,
    with the table's order kept among equals. The table is still what makes a
    cold start possible - there is nothing to learn from on day one.
    """
    viable = [r for r in ROUTES.get(missing, ()) if _route_is_viable(r, missing, cap)]
    if not viable:
        return []
    scores: Dict[str, float] = {}
    try:
        from shani_chronoa.bandit import Bandit
        arms = Bandit().arms()
        for route in viable:
            best = 0.0
            for need in route.needs:
                arm = arms.get(need)
                if arm and arm.pulls >= 3:
                    rate = arm.wins / arm.pulls
                    best = max(best, rate) if route.kind != "install" else best
            scores[id(route)] = best
    except Exception:  # noqa: BLE001 - no bandit means the table decides
        return viable
    return sorted(viable, key=lambda r: -scores.get(id(r), 0.0))


def routes_for(missing: str, cap) -> List[Route]:
    """Every route to `missing` whose own prerequisites are satisfied here."""
    return learned_first(missing, cap)


def install_hint(missing: str) -> Optional[str]:
    """The Arch package name for a command, when this module knows one."""
    for route in ROUTES.get(missing, ()):
        if route.kind == "install" and route.needs == (missing,):
            # The action text already carries the package name; keep this as the
            # machine-readable half for callers that want to offer a button.
            return route.action.split("'")[1] if "'" in route.action else None
    return None


def explain(missing: str, cap, tool: str = "") -> str:
    """A reply that ends in a route rather than a refusal.

    Three shapes, in the order a person would take them: install one package,
    use something already here, or do it in Python. When nothing is viable it
    says so plainly - a wrong route is worse than none.
    """
    found = routes_for(missing, cap)
    if not found:
        return (f"{tool + ' ' if tool else ''}needs {missing}, which this machine "
                f"does not have, and no alternative route is available here.")

    count = len(found)
    lines = [f"{tool + ' ' if tool else ''}needs {missing}, which this machine does "
             f"not have. "
             + (f"There {'is a way' if count == 1 else f'are {count} ways'} "
                f"round it:")]
    kinds = {"install": "install it", "substitute": "or use what is already here",
             "fallback": "or do it without an external tool"}
    shown_kind = None
    for route in found:
        if route.kind != shown_kind and route.kind in kinds:
            lines.append(f"  - {kinds[route.kind]}:")
            shown_kind = route.kind
        lines.append(f"      {route.action}")
    return "\n".join(lines)
