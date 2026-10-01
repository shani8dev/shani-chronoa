"""Skill: read a PDF or a picture of text aloud-ready - pdftotext (poppler)
for PDFs, tesseract for images. Both are already on every Shanios install;
nothing leaves the machine. Plain text files are read_text_file's job."""

import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

MAX_CHARS = 6000
IMAGES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_document",
        "description": "Read the text of a PDF (optionally a page range) or of an image (OCR), "
                       "e.g. to summarise it or read it out.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The file, e.g. '~/Documents/letter.pdf'."},
            "first_page": {"type": "integer"}, "last_page": {"type": "integer"},
        }, "required": ["path"]},
    },
}


def _run(arguments: dict) -> str:
    try:
        path = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not path.is_file():
        return f"{path} is not a file."
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            if not shutil.which("pdftotext"):
                return "Reading PDFs needs pdftotext (the poppler package)."
            argv = ["pdftotext", "-layout"]
            for flag, key in (("-f", "first_page"), ("-l", "last_page")):
                if arguments.get(key):
                    argv += [flag, str(int(arguments[key]))]
            r = subprocess.run(argv + [str(path), "-"], capture_output=True, text=True, timeout=60)
        elif suffix in IMAGES:
            if not shutil.which("tesseract"):
                return "Reading pictures needs tesseract."
            r = subprocess.run(["tesseract", str(path), "-", "-l", "eng"], capture_output=True, text=True, timeout=120)
        else:
            return f"I read PDFs and pictures; for {suffix or 'this'} files use read_text_file."
    except subprocess.TimeoutExpired:
        return f"Reading {path.name} took too long."
    text = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", r.stdout)).strip()
    if r.returncode != 0 and not text:
        return f"Could not read {path.name}: {(r.stderr.strip().splitlines() or ['no message'])[-1][:160]}"
    if not text:
        return f"{path.name} has no readable text (a scanned PDF needs to be read as a picture)."
    more = f"\n... ({len(text) - MAX_CHARS} more characters)" if len(text) > MAX_CHARS else ""
    return f"{path.name}:\n{text[:MAX_CHARS]}{more}"


SKILLS = [Skill(name="read_document", schema=_SCHEMA, run=_run)]
