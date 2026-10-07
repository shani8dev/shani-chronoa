"""Skill: merge PDFs, split one into pages, or pull out some of its pages.

"Combine these scans into one PDF" and "send only pages 3 to 5" are everyday
requests, and `office_document` only pulls pictures out of a PDF. Poppler is on
every Shanios image, and its `pdfunite` / `pdfseparate` / `pdfinfo` do exactly
this without re-rendering anything, so text stays text and nothing is
recompressed.

Rotation is not offered: the tool that does it losslessly (`qpdf`) is not on
the image, and re-rendering pages to rotate them would silently turn text into
pictures. Saying so beats a "rotate" that degrades the document.

Ungated, like `convert_document` and `edit_image`: it only ever writes **new**
files. An existing output is refused, never overwritten, and no input is ever
modified.

Honesty rules: the result is checked by counting its pages with `pdfinfo` (here
and in `POST_CONDITION`), because poppler can exit 0 having written a document
with fewer pages than asked for; a page range is validated against the real
page count before anything is written.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 120
_FRESH_SECONDS = 300
_MAX_INPUTS = 50

SCHEMA = {
    "type": "function",
    "function": {
        "name": "pdf_pages",
        "description": (
            "Work with PDF pages without re-rendering them: 'info' (page count), "
            "'merge' several PDFs into one (paths, in order), 'split' a PDF into "
            "one file per page (into a folder), or 'extract' some pages (e.g. "
            "'1-3,7') into a new PDF. Always writes new files; never overwrites "
            "an existing one or changes the originals. No rotation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["info", "merge", "split", "extract"]},
                "path": {"type": "string", "description": "info/split/extract: the PDF."},
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "merge: the PDFs to combine, in order."},
                "pages": {"type": "string", "description": "extract: pages like '1-3,7'."},
                "output": {"type": "string",
                           "description": "merge/extract: the new PDF; split: the folder for the pages."},
            },
            "required": ["action"],
        },
    },
}


def _cmd(*argv: str) -> "subprocess.CompletedProcess | None":
    try:
        return subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def page_count(path: Path) -> "int | None":
    proc = _cmd("pdfinfo", str(path))
    if proc is None or proc.returncode != 0:
        return None
    m = re.search(r"^Pages:\s+(\d+)", proc.stdout, re.M)
    return int(m.group(1)) if m else None


def parse_pages(spec: str, total: int) -> "list[int]":
    """'1-3,7' -> [1, 2, 3, 7]; raises ValueError naming what is wrong."""
    pages = []
    for part in (spec or "").replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"{part!r} is not a page or range")
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        if lo < 1 or hi < lo or hi > total:
            raise ValueError(f"{part!r} is outside pages 1-{total}")
        pages.extend(range(lo, hi + 1))
    if not pages:
        raise ValueError("no pages were given")
    return pages


def _input(raw: str) -> Path:
    path = files.resolve((raw or "").strip())
    if not path.is_file():
        raise files.PathProblem(f"{path} is not a file.")
    return path


def _new_output(raw: str, suffix: str = ".pdf") -> Path:
    if not (raw or "").strip():
        raise files.PathProblem("No output was named, so nothing was written.")
    out = files.expand(raw)
    if suffix and out.suffix.lower() != suffix:
        out = out.with_suffix(suffix)
    if out.exists():
        raise files.PathProblem(f"{out} already exists, and this never overwrites a file. Pick a new name.")
    if not out.parent.is_dir():
        raise files.PathProblem(f"The folder {out.parent} does not exist.")
    return out


def _merge(arguments: dict) -> str:
    raws = arguments.get("paths") or []
    if not isinstance(raws, list) or len(raws) < 2:
        return "Merging needs at least two PDFs in 'paths'."
    if len(raws) > _MAX_INPUTS:
        return f"At most {_MAX_INPUTS} PDFs can be merged at once."
    inputs = [_input(r) for r in raws]
    counts = [page_count(p) for p in inputs]
    bad = [p.name for p, c in zip(inputs, counts) if c is None]
    if bad:
        return f"These are not readable PDFs, so nothing was merged: {', '.join(bad)}."
    out = _new_output(arguments.get("output") or "")
    proc = _cmd("pdfunite", *map(str, inputs), str(out))
    return _report(proc, out, sum(counts), f"merged {len(inputs)} PDFs")


def _extract(arguments: dict) -> str:
    src = _input(arguments.get("path") or "")
    total = page_count(src)
    if total is None:
        return f"{src.name} is not a readable PDF."
    try:
        pages = parse_pages(arguments.get("pages") or "", total)
    except ValueError as exc:
        return f"Could not extract: {exc}. Nothing was written."
    out = _new_output(arguments.get("output") or "")
    with tempfile.TemporaryDirectory(prefix="chronoa-pdf-") as tmp:
        parts = []
        for n in pages:
            part = Path(tmp) / f"p{len(parts):05d}.pdf"
            proc = _cmd("pdfseparate", "-f", str(n), "-l", str(n), str(src), str(part))
            if proc is None or proc.returncode != 0 or not part.exists():
                return f"pdfseparate could not take page {n} out of {src.name}; nothing was written."
            parts.append(str(part))
        if len(parts) == 1:
            shutil.copyfile(parts[0], out)
            proc = subprocess.CompletedProcess([], 0, "", "")
        else:
            proc = _cmd("pdfunite", *parts, str(out))
    return _report(proc, out, len(pages), f"took {len(pages)} page(s) out of {src.name}")


def _split(arguments: dict) -> str:
    src = _input(arguments.get("path") or "")
    total = page_count(src)
    if total is None:
        return f"{src.name} is not a readable PDF."
    raw = (arguments.get("output") or "").strip()
    folder = files.expand(raw) if raw else src.with_name(f"{src.stem}-pages")
    if folder.exists() and any(folder.iterdir()):
        return f"{folder} already exists and is not empty; this never overwrites files. Pick a new folder."
    folder.mkdir(parents=True, exist_ok=True)
    proc = _cmd("pdfseparate", str(src), str(folder / f"{src.stem}-%d.pdf"))
    written = sorted(folder.glob(f"{src.stem}-*.pdf"))
    if proc is None or proc.returncode != 0:
        return f"pdfseparate failed after writing {len(written)} of {total} page(s) to {folder}."
    if len(written) != total:
        return f"Wrote {len(written)} file(s) to {folder}, but {src.name} has {total} pages - not verified."
    return f"Split {src.name} into {total} one-page PDF(s) in {folder} (all {total} verified to exist)."


def _report(proc, out: Path, expected: int, what: str) -> str:
    if proc is None or proc.returncode != 0:
        detail = "" if proc is None else (proc.stderr or "").strip()
        return f"Could not write {out.name}: {detail or 'the command did not finish'}."
    got = page_count(out)
    if got == expected:
        return f"Done: {what} into {out} ({got} pages, verified)."
    return f"{out} was written, but it has {got if got is not None else 'an unreadable number of'} pages, not {expected}, so it is not verified."


def _run(arguments: dict) -> str:
    if shutil.which("pdfinfo") is None or shutil.which("pdfunite") is None:
        return files.tool_missing("pdfunite", "work with PDF pages")
    action = (arguments.get("action") or "").strip().lower()
    try:
        if action == "info":
            src = _input(arguments.get("path") or "")
            n = page_count(src)
            return f"{src.name} has {n} page(s)." if n is not None else f"{src.name} is not a readable PDF."
        if action == "merge":
            return _merge(arguments)
        if action == "extract":
            return _extract(arguments)
        if action == "split":
            return _split(arguments)
    except files.PathProblem as exc:
        return str(exc)
    return f"Action must be info, merge, split or extract, not {action!r}."


def _post_condition(arguments: dict):
    """The new PDF exists and has the page count the request implies."""
    action = (arguments.get("action") or "").strip().lower()
    if action not in ("merge", "extract") or not arguments.get("output"):
        return None
    out = files.expand(arguments["output"])
    if out.suffix.lower() != ".pdf":
        out = out.with_suffix(".pdf")
    try:
        if action == "merge":
            expected = sum(page_count(files.resolve(p)) or 0 for p in arguments.get("paths") or [])
        else:
            src = files.resolve(arguments.get("path") or "")
            expected = len(parse_pages(arguments.get("pages") or "", page_count(src) or 0))
    except (files.PathProblem, ValueError):
        return None
    if not out.exists():
        return False, f"{out.name} does not exist"
    # A refused call leaves an older file of the same name in place; matching
    # its page count would verify work that never happened.
    if time.time() - out.stat().st_mtime > _FRESH_SECONDS:
        return False, f"{out.name} predates this call, so it is not what was asked for"
    got = page_count(out)
    return got == expected, f"{out.name}: {got} page(s), expected {expected}"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="pdf_pages", schema=SCHEMA, run=_run)]
