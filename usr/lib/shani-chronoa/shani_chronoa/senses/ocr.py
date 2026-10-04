"""OCR sense: faithful text extraction from an image the user pointed at.

**This sense extracts text. It does not see.** There is a separate vision
sense for "what is in this picture"; conflating the two is how an assistant
ends up confidently describing a scene it never looked at. tesseract returns
glyphs and their coordinates, and that is the whole of what this module
consumes: `run()` hands back a transcription plus optional word-level boxes
and never a description of the image.

Why port the GNOME extension rather than write a fresh implementation: the
`shani-circle-to-search` extension
(`shani-pkgbuilds/shani-circle-to-search/.../ocr.js`) already runs tesseract
in production on Shanios, against Arch's `tesseract` + `tesseract-data-eng`.
Its language discovery and normalisation logic
(`_listInstalledTesseractLanguages`, `_normalizeLanguageSelection`,
`_buildTesseractLanguageArgument`) and its TSV word-box filter are ported here
almost line for line, so a language the extension can read, this sense can
read too. Three things are deliberately *not* copied, and the reasons are in
`parse_tsv` and `find_tessdata_dir` below rather than left implicit.

**Consent.** `ocr-sense-enabled` defaults to false, and `run()` refuses when
`ChronoaConfig.sense_allowed("ocr")` is false. That is a product decision, not
an oversight: Chronoa's sibling project advertises "No OCR" as a property, so
"which perceptions may this assistant form?" has to be answerable one sense
at a time. Everything here is local - tesseract is a local binary and no
request leaves the machine - which is why `ocr` is not one of
`config._NETWORKED_SENSES`. The *output* can still be sent onward, because the
text of an image may be forwarded as context to whichever LLM is configured;
that is what the `private` sensitivity and the short TTL below are for.

No pip dependency: this project is packaged for pacman and DEB, and adding
`pytesseract` would break both. tesseract is invoked as a subprocess with an
argv list, never `shell=True`.
"""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Sequence

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PRIVATE, Percept, Sense

# The precedent's `TESSDATA_DIR` constant, kept only as the last-resort
# fallback - `find_tessdata_dir` honours TESSDATA_PREFIX ahead of it.
FALLBACK_TESSDATA_DIR = "/usr/share/tessdata"
# Arch's `tesseract` installs /usr/bin/tesseract; the hardcoded fallback
# exists for the same reason `stt.py` hardcodes /usr/bin/whisper-cli - a
# stripped PATH (a systemd unit, a bare `su`) must not make the binary look
# absent. A wrong binary name shipped whisper.cpp broken for months, so the
# name here is resolved, never assumed from a package name.
FALLBACK_TESSERACT = "/usr/bin/tesseract"

# tesseract's own default. `tesseract-data-eng` on Arch provides exactly this.
DEFAULT_LANGUAGES = ("eng",)
TRAINEDDATA_SUFFIX = ".traineddata"

# A tesseract language code is a filename stem: ASCII alphanumerics and
# underscore. Anything else is rejected before it can reach an argv, so a
# request for `-l "; rm -rf ~"` cannot become an option.
_LANGUAGE_CODE = re.compile(r"^[A-Za-z0-9_]+$")
# Page segmentation modes. 3 = "fully automatic, no OSD", tesseract's own
# default and the right choice for a document or a photo of a page. The
# precedent uses 11 (sparse text) because its input is a screenshot region,
# which is a different job; 11 is selectable here for that case.
_PAGE_SEGMENTATION = re.compile(r"^\d{1,2}$")

# The precedent's DEFAULT_OCR.confidence. Low, deliberately: it is a
# highlight-quality filter for a click-to-search box, and dropping a real word
# is worse than showing a shaky one. Kept as a per-call parameter.
DEFAULT_MIN_CONFIDENCE = 10.0
# 30s is generous for a single page and still bounded: without a timeout a
# pathological image ties up the assistant's turn indefinitely.
DEFAULT_TIMEOUT_SECONDS = 30.0
# `context.py` documents that a full-screen OCR dump can eat a large share of
# the per-turn character budget (MAX_HISTORY_MESSAGES is 40 and Ollama drops
# rather than errors on overflow). Cap the transcription and the box list so
# one image cannot crowd out the conversation.
MAX_TEXT_CHARS = 2000
MAX_BOXES = 200

# OCR text is a transcription of something the user chose to show, so its
# provenance is trustworthy - but an image can hold anything, including a
# password on screen or a printed key. `private` is the honest tier: it ranks
# last for inclusion and is shed first when `ContextBuilder`'s character
# budget is tight, so a user who has not asked about the image is the one
# least likely to have their context displaced by it.
#
# The TTL is short and non-None for the same reason. A transient percept is
# held in memory only and never written to disk, so an hour-old transcription
# of a screenshot that contained a token does not outlive the conversation
# that produced it. 120s still covers follow-up questions about the same
# image, which is the realistic use.
TTL_SECONDS = 120.0


class OcrError(Exception):
    """Tesseract could not be run, or ran and failed. Carries a user-facing reason."""


@dataclass(frozen=True)
class OcrRequest:
    """One OCR job: which image, and how to read it."""

    image_path: str
    languages: Sequence[str] = DEFAULT_LANGUAGES
    page_segmentation: str = "3"
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def default_languages() -> "tuple[str, ...]":
    """English, then every language the person turned on in setup whose data is installed."""
    try:
        from shani_chronoa import languages as _languages
        return tuple(_languages.ocr_languages())
    except Exception:  # noqa: BLE001 - a settings problem must not stop reading English
        return DEFAULT_LANGUAGES


def resolve_tesseract() -> Optional[str]:
    """Return the tesseract binary, or None if this machine has none."""
    found = shutil.which("tesseract")
    if found:
        return found
    if os.path.isfile(FALLBACK_TESSERACT) and os.access(FALLBACK_TESSERACT, os.X_OK):
        return FALLBACK_TESSERACT
    return None


def _usable_tessdata(directory: str) -> bool:
    """True if `directory` exists and holds at least one language file."""
    try:
        return any(name.endswith(TRAINEDDATA_SUFFIX) for name in os.listdir(directory))
    except OSError:
        return False


def find_tessdata_dir() -> Optional[str]:
    """Resolve the tessdata directory, or None if no usable one exists.

    `TESSDATA_PREFIX` is honoured ahead of `FALLBACK_TESSDATA_DIR`. The
    precedent hardcodes `/usr/share/tessdata` and reads only that - correct on
    a stock Arch install and wrong everywhere else (a `$HOME/.local/share`
    tessdata, a snap, a `tesseract-data-*` set installed under a prefix, a
    test that needs its own language files). tesseract itself accepts
    `TESSDATA_PREFIX` as either the tessdata directory or its parent, and
    disambiguates on the trailing name; both spellings are handled here.

    Returning None is a real answer, not a failure to be worked around: a
    tesseract with no language data installed does not complain at startup,
    it exits non-zero on the first `-l eng`, which is a miserable way to
    discover the problem.
    """
    candidates = []
    prefix = os.environ.get("TESSDATA_PREFIX", "").strip()
    if prefix:
        candidates.append(
            prefix
            if os.path.basename(prefix.rstrip(os.sep)) == "tessdata"
            else os.path.join(prefix, "tessdata")
        )
    # the user's own directory, which setup fills with extra languages beside
    # links to the system's English data; tesseract reads only one directory
    from shani_chronoa import languages as _languages
    candidates.append(str(_languages.tessdata_dir()))
    candidates.append(FALLBACK_TESSDATA_DIR)
    return next((c for c in candidates if _usable_tessdata(c)), None)


def list_installed_languages(tessdata_dir: Optional[str] = None) -> list[str]:
    """Every language code installed in `tessdata_dir`, sorted.

    Ported from the precedent's `_listInstalledTesseractLanguages`, which
    enumerates `*.traineddata` and keeps the stems matching `[A-Za-z0-9_]+`.
    """
    directory = tessdata_dir if tessdata_dir is not None else find_tessdata_dir()
    if not directory:
        return []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    stems = (n[: -len(TRAINEDDATA_SUFFIX)] for n in names if n.endswith(TRAINEDDATA_SUFFIX))
    return [stem for stem in stems if _LANGUAGE_CODE.match(stem)]


def normalize_languages(
    requested: Sequence[str], installed: Optional[Sequence[str]] = None
) -> list[str]:
    """Filter, dedupe and fall back a requested language list.

    A direct port of the precedent's `_normalizeLanguageSelection`, including
    its three fallbacks: keep the first configured default that is actually
    installed, else the first installed language, else the default itself.
    `installed` being empty means "not known", so no filtering happens - the
    same distinction the precedent draws with `installedLanguages?.length > 0`.
    """
    installed_set = set(installed) if installed else None
    normalized: list[str] = []
    seen: set[str] = set()
    for language in requested or ():
        code = str(language).strip()
        if not code or not _LANGUAGE_CODE.match(code) or code in seen:
            continue
        if installed_set is not None and code not in installed_set:
            continue
        normalized.append(code)
        seen.add(code)
    if normalized:
        return normalized
    fallback = next(
        (c for c in DEFAULT_LANGUAGES if installed_set is None or c in installed_set), None
    )
    if fallback:
        return [fallback]
    return [installed[0]] if installed else list(DEFAULT_LANGUAGES)


def build_language_argument(requested: Sequence[str]) -> str:
    """The `-l` value: validated codes joined with `+`."""
    return "+".join(normalize_languages(requested, list_installed_languages()))


def _number(value: str) -> Optional[float]:
    """Parse a TSV cell as a number, or None if it is not one.

    Deliberate divergence from the precedent. In JS, `parseFloat("junk")` is
    NaN and `NaN < minConfidence` is false, so a malformed confidence silently
    *passed* the filter; here a cell that does not parse drops its row.
    """
    try:
        return float(value)
    except ValueError:
        return None


def parse_tsv(tsv: str, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> list[dict]:
    """Extract level-5 (word) boxes with sufficient confidence.

    Ported from the precedent's `_parseTsv`, minus the coordinate
    rescaling: that one had to map OCR-space pixels back onto the screen from
    a HiDPI screenshot, this one has the file the user named and no scale to
    undo. Columns are tesseract's: level, page, block, par, line, word, left,
    top, width, height, confidence, text. Level 5 is a single word; the
    levels above it are page/block/paragraph/line containers whose "text" is
    a run-on of their children.
    """
    boxes: list[dict] = []
    for line in tsv.split("\n")[1:]:  # row 0 is the TSV header
        columns = line.rstrip().split("\t")
        if len(columns) < 12:
            continue
        cells = [_number(c) for c in (columns[0], *columns[6:11])]
        if any(cell is None for cell in cells):
            continue
        level, left, top, width, height, confidence = cells
        text = "\t".join(columns[11:]).strip()
        if level != 5 or not text or confidence < min_confidence:
            continue
        boxes.append(
            dict(text=text, left=int(left), top=int(top), width=int(width),
                 height=int(height), confidence=confidence)
        )
    return boxes


def join_words(boxes: Sequence[dict]) -> str:
    """Rebuild readable text from word boxes.

    Ported from the precedent's line-break heuristic in `_rebuildHitboxes`: a
    word whose top edge drops past the midpoint of the previous word's height
    starts a new line, otherwise it is a space. tesseract's TSV has no
    whitespace of its own, so without this a paragraph returns as one
    unpunctuated run.
    """
    parts: list[str] = []
    previous: Optional[dict] = None
    for box in boxes:
        if previous is not None:
            parts.append("\n" if box["top"] - previous["top"] > previous["height"] * 0.5 else " ")
        parts.append(box["text"])
        previous = box
    return "".join(parts).strip()


def run_tesseract(request: OcrRequest, executable: str) -> str:
    """Run tesseract for TSV output and return its stdout.

    Raises `OcrError` with a message fit to show the user; `run()` turns that
    into the percept's content. An argv list, never `shell=True` - the image
    path is user-supplied and gets no chance to be word-split.
    """
    tessdata_dir = find_tessdata_dir()
    argv = [executable, request.image_path, "stdout",
            "-l", build_language_argument(request.languages),
            "--psm", request.page_segmentation]
    if tessdata_dir:
        # Passed explicitly rather than left to tesseract's own search, so the
        # directory that was checked for languages is the one that is used.
        argv += ["--tessdata-dir", tessdata_dir]
    argv.append("tsv")

    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=request.timeout_seconds
        )
    except subprocess.TimeoutExpired:
        raise OcrError(
            f"tesseract took longer than {request.timeout_seconds:.0f}s and was stopped"
        ) from None
    except OSError as e:
        raise OcrError(f"could not run tesseract: {e}") from e
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip().splitlines()
        raise OcrError(stderr[-1] if stderr else f"tesseract exited with {completed.returncode}")
    return completed.stdout


def is_available() -> bool:
    """True when both the binary and usable language data are present.

    Checking only the binary is how a tesseract with no `tesseract-data-*`
    package installed looks healthy until the first real request fails.
    """
    return resolve_tesseract() is not None and find_tessdata_dir() is not None


def _refusal(reason: str) -> str:
    return f"Text extraction unavailable: {reason}."


def _require(condition: object, message: str) -> None:
    if not condition:
        raise OcrError(message)


def _extract_text(arguments: dict) -> OcrRequest:
    """Read and validate the sense's arguments. Raises OcrError on bad input."""
    path = str(arguments.get("path") or "").strip()
    _require(path and os.path.isfile(path), f"{path or 'no path'} is not a readable file")
    languages = arguments.get("languages") or default_languages()
    _require(
        isinstance(languages, (list, tuple)) and not isinstance(languages, str),
        "languages must be a list of tesseract language codes",
    )
    page_segmentation = str(arguments.get("page_segmentation") or "3").strip()
    _require(
        bool(_PAGE_SEGMENTATION.match(page_segmentation)),
        f"{page_segmentation!r} is not a tesseract page segmentation mode",
    )
    min_confidence = arguments.get("min_confidence", DEFAULT_MIN_CONFIDENCE)
    _require(
        isinstance(min_confidence, (int, float)) and not isinstance(min_confidence, bool),
        "min_confidence must be a number",
    )
    _require(0 <= min_confidence <= 100, "min_confidence must be between 0 and 100")
    return OcrRequest(path, tuple(languages), page_segmentation, min_confidence)


def run(arguments: dict) -> "str | Percept":
    """Extract the text of one image. Returns a Percept, or a refusal string.

    The refusal path is a plain string on purpose: `Sense.to_percept` wraps it
    with this sense's declared `kind`/`ttl_seconds`/`sensitivity`, so a denied
    request is still audited with the same shape as a granted one.
    """
    config = ChronoaConfig()
    if not config.sense_allowed("ocr"):
        return _refusal(config.sense_allowed_reason("ocr"))

    try:
        request = _extract_text(arguments)
    except OcrError as e:
        return _refusal(str(e))

    executable = resolve_tesseract()
    if executable is None:
        return _refusal(
            "tesseract is not installed (Arch: 'tesseract' + 'tesseract-data-eng'; "
            "Debian: 'tesseract-ocr' + 'tesseract-ocr-eng')"
        )
    if find_tessdata_dir() is None:
        return _refusal("tesseract has no language data; install a tesseract-data-* package")

    try:
        tsv = run_tesseract(request, executable)
    except OcrError as e:
        return _refusal(str(e))

    boxes = parse_tsv(tsv, request.min_confidence)
    languages = normalize_languages(request.languages, list_installed_languages())
    if boxes:
        text = join_words(boxes)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + " [...] (truncated)"
        content = f"Text extracted from {request.image_path} [tesseract {'+'.join(languages)}]:\n{text}"
    else:
        content = f"No text was found in {request.image_path} [tesseract {'+'.join(languages)}]."
    return Percept(
        sense="ocr",
        kind="image_text",
        content=content,
        created_at=time.time(),
        ttl_seconds=TTL_SECONDS,
        source=request.image_path,
        sensitivity=SENSITIVITY_PRIVATE,
        metadata={
            "languages": languages,
            "page_segmentation": request.page_segmentation,
            "min_confidence": request.min_confidence,
            "word_count": len(boxes),
            "boxes": boxes[:MAX_BOXES],
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ocr",
        "description": "Extract the text of an image with local tesseract. Transcribes text only; it does not describe the image. Needs the ocr sense enabled.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the image file to read."},
                "languages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tesseract codes, e.g. ['eng']; uninstalled ones are dropped.",
                },
                "page_segmentation": {
                    "type": "string",
                    "description": "Tesseract mode: '3' automatic (default, documents), '6' block, '11' sparse (screenshots).",
                },
                "min_confidence": {
                    "type": "number",
                    "description": "Drop words below this 0-100 confidence. Default 10.",
                },
            },
            "required": ["path"],
        },
    },
}

_OCR_SENSE = Sense(
    # poll_interval=None: reactive only - it reads the image you name, on request
    name="ocr",
    kind="image_text",
    ttl_seconds=TTL_SECONDS,
    sensitivity=SENSITIVITY_PRIVATE,
    schema=_SCHEMA,
    run=run,
)

SENSES = [_OCR_SENSE]
