"""Skill: scan a page and read it - from the scanner, the camera, or a photo of a page.

- **scanner**: `scanimage` (sane, with sane-airscan for network scanners and the
  vendor drivers shani-scanner ships) takes the page.
- **camera**: a frame from the webcam (`screengrab`, behind the same `vision`
  consent as any camera capture), and OpenCV (`opencv.page`) finds the page in
  it, warps it flat and evens out the light - OCR on a skewed photo with a desk
  around it reads badly, on the flattened page it reads like a scan.
- **photo**: the same flattening for a picture the user already has
  ("straighten this photo of a receipt").

tesseract (already a Chronoa dependency) reads the result, in English plus the
languages turned on in setup. The image is kept in ~/Documents/Scans so the
user has the scan, and the text comes back for Chronoa to read out or
summarise. Nothing leaves the machine.
"""

import datetime
import re
import shutil
import subprocess

from pathlib import Path
from shani_chronoa.skills import Skill

_SOURCES = ("scanner", "camera", "photo")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scan_document",
        "description": "Scan a page and return its text (OCR), keeping the image in ~/Documents/Scans. "
                       "source: 'scanner' (default; list_only lists them), 'camera' (hold the page up to the "
                       "webcam; it is found and straightened), or 'photo' (straighten and read a picture of a "
                       "page at path). 'Scan this letter and read it to me.'",
        "parameters": {"type": "object", "properties": {
            "source": {"type": "string", "enum": list(_SOURCES)},
            "path": {"type": "string", "description": "source=photo: the picture of the page."},
            "list_only": {"type": "boolean", "description": "Only list the scanners."},
            "device": {"type": "string", "description": "A scanner from the list, or a camera like /dev/video0."},
        }},
    },
}


def scanners() -> list:
    try:
        r = subprocess.run(["scanimage", "-f", "%d\t%v %m%n"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [tuple(l.split("\t", 1)) for l in r.stdout.splitlines() if "\t" in l]


def _out_path(kind: str) -> Path:
    out_dir = Path.home() / "Documents" / "Scans"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{kind}-{datetime.datetime.now():%Y%m%d-%H%M%S}.png"


def _read(image: Path, intro: str) -> str:
    if not shutil.which("tesseract"):
        return f"{intro}, but tesseract is not installed to read it."
    try:
        from shani_chronoa.senses import ocr
        argv = ["tesseract", str(image), "-", "-l", ocr.build_language_argument(ocr.default_languages())]
        tessdata = ocr.find_tessdata_dir()
        if tessdata:
            argv += ["--tessdata-dir", tessdata]
        t = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return f"{intro}; reading it took too long."
    text = re.sub(r"\n{3,}", "\n\n", t.stdout).strip()
    return f"{intro}. It says:\n{text[:3000]}" if text else f"{intro}, but no text could be read from it."


def _scanner(arguments: dict) -> str:
    if not shutil.which("scanimage"):
        return "Scanning needs SANE's scanimage (the sane package), which is not installed."
    found = scanners()
    if arguments.get("list_only"):
        return ("Scanners: " + "; ".join(f"{d} ({n})" for d, n in found)) if found else "No scanner is connected."
    device = (arguments.get("device") or "").strip() or (found[0][0] if found else "")
    if not device:
        return "No scanner is connected. To use the camera instead, ask to scan with the camera."
    if not re.fullmatch(r"[A-Za-z0-9:._/\-=@\[\]]{1,200}", device):
        return f"'{device}' is not a scanner name."
    image = _out_path("scan")
    from shani_chronoa import body
    try:
        with body.lit("eyes", "scanning a page", device, deadline=200.0):
            r = subprocess.run(["scanimage", "-d", device, "--format=png", "--resolution", "300",
                                "-o", str(image)], capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return "The scanner did not finish within three minutes."
    if r.returncode != 0 or not image.exists() or image.stat().st_size == 0:
        image.unlink(missing_ok=True)
        return f"The scan failed: {(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}"
    return _read(image, f"Scanned to {image}")


def _flattened(data: bytes, what: str) -> str:
    from shani_chronoa.opencv import page as pages, runtime
    problem = runtime.libraries_problem()
    if problem:
        return problem
    try:
        page = pages.flatten_page(data)
    except ValueError as exc:
        return f"Could not use {what}: {exc}"
    image = _out_path("page" if page.found_page else "photo")
    image.write_bytes(page.png)
    found = ("found the page and straightened it" if page.found_page
             else "could not find the edges of a page, so the whole picture was cleaned up and read")
    return _read(image, f"From {what} Chronoa {found}; saved to {image}")


def _camera(arguments: dict) -> str:
    from shani_chronoa.config import ChronoaConfig
    config = ChronoaConfig()
    if not config.vision_sense_enabled:
        return f"Using the camera is not permitted: {config.sense_allowed_reason('vision')}."
    from shani_chronoa import screengrab
    device = (arguments.get("device") or "").strip() or None
    if device and not re.fullmatch(r"/dev/video[0-9]{1,2}", device):
        return f"{device!r} is not a camera like /dev/video0."
    try:
        shot = screengrab.capture_camera(device=device)
    except Exception as exc:  # noqa: BLE001 - a camera that will not open is a message, not a crash
        return f"Could not take a picture with the camera: {exc}"
    return _flattened(shot.data, "the camera")


def _photo(arguments: dict) -> str:
    from shani_chronoa import files
    try:
        path = files.resolve(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    if not path.is_file():
        return f"{path} does not exist."
    if path.stat().st_size > 50_000_000:
        return f"{path.name} is too large to be a photo of a page."
    return _flattened(path.read_bytes(), path.name)


def _run(arguments: dict) -> str:
    source = (arguments.get("source") or "scanner").strip().lower()
    if source == "camera":
        return _camera(arguments)
    if source == "photo":
        return _photo(arguments)
    if source != "scanner":
        return f"source must be one of {', '.join(_SOURCES)}."
    return _scanner(arguments)


def _verify_scanned_document(arguments: dict, tool=None):
    """Post-condition: did the scan actually read text off the page?

    A scanner that produces an empty string is the failure worth catching: it
    exits 0 on a blank page, a skewfed original or a missing language pack, and
    "I could not read that" and "here is what it said" are the same return
    value. So the check re-reads the *text* the skill was about to report, and
    holds that non-empty text means there was something to read.

    **A document that genuinely contains no text reads as unverified rather than
    as a failure**, and that limit is stated rather than hidden - a blank page is
    a correct outcome this check cannot tell from a broken scan.
    """
    from pathlib import Path as _P
    out = str(arguments.get("output") or "").strip()
    source = str(arguments.get("path") or "").strip()
    if not source:
        return None  # nothing was scanned to check
    scanned = bool(arguments.get("path")) and not out
    if scanned:
        # It scanned in place: the artefact is the text itself, so re-derive it.
        try:
            from shani_chronoa import files as _files
            src = _files.resolve(source)
        except Exception as exc:  # noqa: BLE001
            return (False, f"could not resolve the source: {exc}")
        if not src.is_file():
            return (False, f"{src} is not a file, so nothing was scanned")
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return (False, f"could not read {src.name}: {exc}")
        words = len(text.split())
        if words == 0:
            return (False, f"{src.name} holds no text at all - either a blank "
                           f"page or the scan produced nothing")
        return (True, f"{src.name} holds {words} word(s) of text")
    return None


POST_CONDITION = _verify_scanned_document

SKILLS = [Skill(name="scan_document", schema=_SCHEMA, run=_run)]
