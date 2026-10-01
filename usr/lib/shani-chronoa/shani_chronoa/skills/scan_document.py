"""Skill: scan a page and read it - the scanner through SANE, the text through tesseract.

`scanimage` (sane, with sane-airscan for network scanners and the vendor
drivers shani-scanner ships) takes the page; tesseract (already a Chronoa
dependency, for OCR) reads it. The image is kept in ~/Documents/Scans so the
user has the scan, and the text comes back for Chronoa to read out or
summarise. Nothing leaves the machine.
"""

import datetime
import os
import re
import shutil
import subprocess

from pathlib import Path
from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scan_document",
        "description": "Scan a page on the attached or network scanner and return its text "
                       "(OCR), keeping the image in ~/Documents/Scans. 'Scan this letter and "
                       "read it to me.' Use list_only to see which scanners are connected.",
        "parameters": {"type": "object", "properties": {
            "list_only": {"type": "boolean", "description": "Only list the scanners."},
            "device": {"type": "string", "description": "A device name from the list; default: the first."},
        }},
    },
}


def scanners() -> list:
    try:
        r = subprocess.run(["scanimage", "-f", "%d\t%v %m%n"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [tuple(l.split("\t", 1)) for l in r.stdout.splitlines() if "\t" in l]


def _run(arguments: dict) -> str:
    if not shutil.which("scanimage"):
        return "Scanning needs SANE's scanimage (the sane package), which is not installed."
    found = scanners()
    if arguments.get("list_only"):
        return ("Scanners: " + "; ".join(f"{d} ({n})" for d, n in found)) if found else "No scanner is connected."
    device = (arguments.get("device") or "").strip() or (found[0][0] if found else "")
    if not device:
        return "No scanner is connected."
    if not re.fullmatch(r"[A-Za-z0-9:._/\-=@\[\]]{1,200}", device):
        return f"'{device}' is not a scanner name."
    out_dir = Path.home() / "Documents" / "Scans"
    out_dir.mkdir(parents=True, exist_ok=True)
    image = out_dir / f"scan-{datetime.datetime.now():%Y%m%d-%H%M%S}.png"
    try:
        r = subprocess.run(["scanimage", "-d", device, "--format=png", "--resolution", "300",
                            "-o", str(image)], capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return "The scanner did not finish within three minutes."
    if r.returncode != 0 or not image.exists() or image.stat().st_size == 0:
        image.unlink(missing_ok=True)
        return f"The scan failed: {(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}"
    if not shutil.which("tesseract"):
        return f"Scanned to {image}, but tesseract is not installed to read it."
    try:
        t = subprocess.run(["tesseract", str(image), "-", "-l", "eng"], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return f"Scanned to {image}; reading it took too long."
    text = re.sub(r"\n{3,}", "\n\n", t.stdout).strip()
    return (f"Scanned to {image}. It says:\n{text[:3000]}" if text
            else f"Scanned to {image}, but no text could be read from it.")


SKILLS = [Skill(name="scan_document", schema=_SCHEMA, run=_run)]
