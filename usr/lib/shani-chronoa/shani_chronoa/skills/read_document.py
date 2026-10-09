"""Skill: read a PDF or a picture of text aloud-ready - pdftotext (poppler)
for PDFs, tesseract for images. Both are already on every Shanios install;
nothing leaves the machine. Plain text files are read_text_file's job.

**A PDF with no text layer is read as a picture, not refused.** `pdftotext` on a
scanned document returns two bytes - the form feeds between pages and nothing
else - so the old answer here was *"has no readable text (a scanned PDF needs to
be read as a picture)"*, which pointed at something the user cannot do: nothing
in the package could turn a PDF into a picture, so the sentence was a dead end
with a confident voice. Both halves were already here and already depended on:
`pdftoppm` ships in **poppler**, the same package as `pdftotext`, and tesseract
is the branch below. Render, OCR, and say so.

**The answer names how it was read.** Text recognised from a page image is not
the same as a text layer, and a summary built from it should not look like one -
so the output says the document has no text layer and the words come from
reading the page.

**The rendered pages are kept**, in `~/Documents/Scans/<name>-pages` beside
where `scan_document` puts a scan, and the answer says so. They are what makes a
long scan usable rather than a dead end: only the first few pages are read, and
when reading fails outright - no language pack installed, which is the state the
Plasma image is in - they are the part that is still worth having.
"""

import pathlib
import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

MAX_CHARS = 6000
IMAGES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")

#: Rendered at 200 DPI. Measured: 150 DPI gives tesseract noticeably worse
#: lines on body text, and 300 roughly doubles the render and the OCR time for
#: no gain this package could measure.
_RENDER_DPI = 200

#: How many pages one call will OCR. OCR is seconds per page and a whole
#: scanned book would otherwise be one request that eats the skill's whole
#: timeout, so the cap is reported rather than applied silently.
_MAX_OCR_PAGES = 5


def _tesseract_argv(image: str) -> list:
    """tesseract's argv for one image, in the user's languages.

    **One definition, shared by the image branch and the scanned-PDF branch.**
    The image branch already knew about `default_languages()` and
    `find_tessdata_dir()`; copying that into a second branch is how a language
    setting stops applying to one kind of file and not the other.

    **Read in the user's languages, not silently just English.** This used to
    hardcode `-l eng`, so a person who turned on Hindi or Marathi in setup got
    tesseract forcing every Latin-only guess onto a Devanagari page -
    confident-looking garbage, the same failure `scan_document` already refuses
    by routing through the OCR sense.
    """
    try:
        from shani_chronoa.senses import ocr
        argv = ["tesseract", image, "-", "-l",
                ocr.build_language_argument(ocr.default_languages())]
        tessdata = ocr.find_tessdata_dir()
        if tessdata:
            argv += ["--tessdata-dir", tessdata]
        return argv
    except Exception:  # noqa: BLE001 - a settings problem must not stop reading English
        return ["tesseract", image, "-", "-l", "eng"]


def _ocr_image(image: str, timeout: int = 120):
    """`(returncode, text)` for one image, or `(None, reason)`."""
    try:
        r = subprocess.run(_tesseract_argv(image), capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "reading the page as a picture took too long"
    except (subprocess.SubprocessError, OSError) as exc:
        return None, f"could not read the page as a picture ({exc})"
    return r.returncode, r.stdout


def _render_dir(pdf) -> pathlib.Path:
    """Where the rendered pages of `pdf` are kept.

    **`~/Documents/Scans`, the same place `scan_document` keeps a scanned page** -
    a function, not an import-time constant, for the reason every other data path
    here is one: resolved at import it points a test run at a real user's
    Documents.

    **They are kept, not deleted.** My first version rendered into
    `tempfile.mkdtemp()` and removed it in a `finally`, reasoning that a page
    image of somebody's document is their document and should not be left behind.
    That has the direction backwards: it threw away the expensive artefact and
    both things it was for. A forty-page scan OCRs five, and the other
    thirty-five were rendered and then deleted, so asking for page 7 re-renders
    from scratch; and when OCR *fails* - a missing language pack, which is the
    state the Plasma image is in - the rendered pages are exactly what is still
    useful, because they are what any other OCR, or the user themselves, can
    read. `scan_document` keeps its image for the same reason, and two skills
    should not disagree about it.
    """
    out = pathlib.Path.home() / "Documents" / "Scans" / (pdf.stem + "-pages")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _render_and_read(path, first, last) -> str:
    """OCR a PDF that has no text layer, or say precisely why it could not be."""
    missing = [b for b in ("pdftoppm", "tesseract") if shutil.which(b) is None]
    if missing:
        names = ", ".join(missing)
        return ("This PDF has no text layer - it is a scan, or the text is drawn "
                "as pictures - so there is nothing to extract. Reading it needs "
                f"{names} (the poppler and tesseract packages). Nothing was "
                "guessed from a document that has no words in it.")

    workdir = _render_dir(path)
    if True:
        argv = ["pdftoppm", "-r", str(_RENDER_DPI), "-png"]
        if first:
            argv += ["-f", str(first)]
        if last:
            argv += ["-l", str(last)]
        try:
            r = subprocess.run(argv + [str(path), f"{workdir}/page"],
                               capture_output=True, text=True, timeout=120)
        except (subprocess.SubprocessError, OSError) as exc:
            return f"Could not render the pages of {path.name} ({exc})."
        if r.returncode != 0:
            detail = (r.stderr.strip().splitlines() or ["no message"])[-1][:160]
            return f"Could not render the pages of {path.name}: {detail}"

        # Sorted numerically, not as strings: pdftoppm names them page-1, -2,
        # -10, and a plain sort reads "page-10" as page 2. The number is
        # computed once here and used for the message, because `glob` returns
        # paths and the first version called `.rsplit` on one.
        numbered = sorted((int(p.stem.rsplit("-", 1)[-1] or 0), p)
                          for p in pathlib.Path(workdir).glob("page-*.png"))
        pages = [p for _n, p in numbered]
        numbers = [n for n, _p in numbered]
        if not pages:
            return (f"{path.name} has no text layer and no page could be "
                    f"rendered from it, so there is nothing to read.")
        shown = pages[:_MAX_OCR_PAGES]
        shown_numbers = numbers[:_MAX_OCR_PAGES]

        out = []
        for page in shown:
            code, text = _ocr_image(page)
            if code is None:
                return text
            body = re.sub(r"[ \t]+\n", "\n", text or "").strip()
            if body:
                out.append(body)
            elif code != 0:
                # **A non-zero exit is not a blank page.** Measured on the
                # Plasma image, where `tesseract --list-langs` answers
                # `afr osd` and `-l eng` gives exit 1, empty stdout and
                # "Failed loading language 'eng'": treating that as "no text
                # on the page" would report a document full of words as blank,
                # which is the confident wrong answer this whole path exists to
                # stop. The cause is almost always a missing language pack -
                # tesseract depends on the *virtual* `tessdata`, ~128 packages
                # provide it, and an image that pins none resolves to whichever
                # provider comes first (Afrikaans, on Plasma).
                return (f"Could not read {path.name} as a picture: tesseract "
                        f"failed (exit {code}). The usual cause is a missing "
                        f"language pack - tesseract needs one, and this "
                        f"machine may not have English installed. Nothing was "
                        f"guessed from the page.")
        if not out:
            return (f"{path.name} has no text layer, and reading page "
                    f"{shown_numbers[0]} as a picture found no text either. It "
                    f"may be a blank page, or an image with no writing on it.")

        where = (f"page {shown_numbers[0]}" if len(shown) == 1
                 else f"pages {shown_numbers[0]}-{shown_numbers[-1]}")
        note = (f"[{path.name} has no text layer; these words come from "
                f"reading {where} as {_RENDER_DPI} DPI pictures, so numbers and "
                f"names are more likely to be wrong than from a real text "
                f"layer. The page images are kept in {workdir}.]\n\n")
        more = (f"\n\n({len(pages) - len(shown)} further page(s) not read, and "
                f"already rendered in {workdir} - ask for a page range to read "
                f"one.)" if len(pages) > len(shown) else
                f"\n\n(Page images kept in {workdir}.)")
        return note + "\n\n".join(out)[:MAX_CHARS] + more

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
            # **A PDF with no text layer is read as a picture rather than
            # refused.** `pdftotext` on a scan returns the form feeds between
            # pages and nothing else - measured, 2 bytes for a 2-page scan -
            # and the answer used to be "a scanned PDF needs to be read as a
            # picture", which named something this package could not do.
            if not (r.stdout or "").strip():
                return _render_and_read(
                    path,
                    int(arguments["first_page"]) if arguments.get("first_page") else None,
                    int(arguments["last_page"]) if arguments.get("last_page") else None)
        elif suffix in IMAGES:
            if not shutil.which("tesseract"):
                return "Reading pictures needs tesseract."
            # The argv builder is `_tesseract_argv` above, shared with the
            # scanned-PDF branch: it was this code, inline, and duplicating it
            # is how a language setting stops applying to one kind of file.
            argv = _tesseract_argv(str(path))
            r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
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
