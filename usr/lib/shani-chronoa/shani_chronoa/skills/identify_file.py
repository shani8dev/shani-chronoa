"""Skill: what is this file, really?

`get_file_info` reports `stat` - size, timestamps, permissions. Nothing here
reads the **bytes**, so nothing answers "why won't this image open" when the
answer is that the file named `.png` has never been a PNG.

**An extension is a claim about content, and a claim is not evidence.** A file's
name carries no weight: it can be typed by hand, changed by a download, or
inherited from a zip that had it wrong. This reads the leading bytes and
compares them to the published signatures, then says whether the name agrees.

**Implemented here, not by shelling out to `file`.** `file` is not in any of the
three `shani-tools` packages, so it cannot be relied on to be present - and
measured on this box its magic database is minimal enough that it answers
**`data`** for a real PNG and for a real gzip, while still correctly naming a
JPEG. A reader that answers "data" for the formats people actually ask about is
not better than a table that answers, so the table is here and it is small
enough to check by eye.

**What a table cannot say, it does not say.** An unrecognised signature is
reported as unrecognised - never as "not an image", because a format absent
from the table and a file that is not an image are different facts.
"""

from __future__ import annotations

import string
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: Bytes read from the head. tar's signature sits at offset 257, so this has to
#: be at least 512 for tar to be detectable at all.
_HEAD = 512

#: (kind, description, alternatives). **Each alternative is a list of
#: (offset, magic) pairs that must ALL hold, and the entry matches if ANY
#: alternative does.** The distinction is real and the first version had it
#: wrong: a Zip archive begins with `PK\x03\x04`, `PK\x05\x06` or `PK\x07\x08`
#: depending on how it was written, so those are *alternatives* and `any` is
#: right - while a WAV needs `RIFF` at 0 **and** `WAVE` at 8, so that is one
#: alternative with two marks and `all` is right inside it. Requiring all three
#: zip spellings to hold at once meant a real zip was reported as unrecognised.
_SIGNATURES = [
    ("png", "a PNG image", [[(0, b"\x89PNG\r\n\x1a\n")]]),
    ("gif", "a GIF image", [[(0, b"GIF87a")], [(0, b"GIF89a")]]),
    ("jpeg", "a JPEG image", [[(0, b"\xff\xd8\xff")]]),
    ("pdf", "a PDF document", [[(0, b"%PDF-")]]),
    ("gzip", "a gzip-compressed stream", [[(0, b"\x1f\x8b")]]),
    ("bzip2", "a bzip2-compressed stream", [[(0, b"BZh")]]),
    ("xz", "an xz-compressed stream", [[(0, b"\xfd7zXZ\x00")]]),
    ("zstd", "a zstd-compressed stream", [[(0, b"\x28\xb5\x2f\xfd")]]),
    ("lz4", "an lz4 frame", [[(0, b"\x04\x22\x4d\x18")]]),
    ("7z", "a 7-Zip archive", [[(0, b"7z\xbc\xaf\x27\x1c")]]),
    ("rar", "a RAR archive", [[(0, b"Rar!\x1a\x07\x00")],
                              [(0, b"Rar!\x1a\x07\x01\x00")]]),
    ("zip", "a Zip archive", [[(0, b"PK\x03\x04")], [(0, b"PK\x05\x06")],
                              [(0, b"PK\x07\x08")]]),
    ("elf", "an ELF executable or library", [[(0, b"\x7fELF")]]),
    ("pe", "a Windows executable", [[(0, b"MZ")]]),
    ("sqlite", "a SQLite database", [[(0, b"SQLite format 3\x00")]]),
    ("wasm", "a WebAssembly module", [[(0, b"\x00asm")]]),
    ("ogg", "an Ogg media container", [[(0, b"OggS")]]),
    ("wav", "a WAV audio file", [[(0, b"RIFF"), (8, b"WAVE")]]),
    ("avi", "an AVI video file", [[(0, b"RIFF"), (8, b"AVI ")]]),
    ("webp", "a WebP image", [[(0, b"RIFF"), (8, b"WEBP")]]),
    ("flac", "a FLAC audio file", [[(0, b"fLaC")]]),
    ("bmp", "a BMP image", [[(0, b"BM")]]),
    ("class", "a Java class file", [[(0, b"\xca\xfe\xba\xbe")]]),
    # tar's signature is at offset 257, which is why _HEAD is 512 - a shorter
    # read makes every tar archive unrecognised.
    ("tar", "a tar archive", [[(257, b"ustar")]]),
]

#: The extension each kind is *usually* named with. Several are legitimate for
#: one kind, and a mismatch is reported, not asserted.
_EXTENSIONS = {
    "png": {"png"},
    "gif": {"gif"},
    "jpeg": {"jpg", "jpeg", "jpe", "jfif"},
    "pdf": {"pdf"},
    "gzip": {"gz", "gzip"},
    "bzip2": {"bz2", "bz"},
    "xz": {"xz", "txz"},
    "zstd": {"zst", "zstd"},
    "lz4": {"lz4"},
    "7z": {"7z"},
    "rar": {"rar"},
    "zip": {"zip", "jar", "apk", "docx", "xlsx", "pptx", "odt", "whl"},
    "elf": {"", "so", "bin", "elf"},
    "pe": {"exe", "dll", "sys"},
    "sqlite": {"db", "sqlite", "sqlite3"},
    "wasm": {"wasm"},
    "ogg": {"ogg", "oga", "ogv", "opus"},
    "wav": {"wav"},
    "avi": {"avi"},
    "webp": {"webp"},
    "flac": {"flac"},
    "tar": {"tar", "tgz", "tar.gz"},
    "bmp": {"bmp", "dib"},
    "class": {"class"},
    "png-in-jar": {"png"},
}

#: A text file, detected by having no binary control bytes in its head.
_PRINTABLE = set(bytes(string.printable, "ascii")) - {b"\x0b", b"\x0c"}


def _extension(path: Path) -> str:
    """The last suffix, lowercased and without the dot. A file with two
    suffixes (`archive.tar.gz`) reports only the last, which is the one the
    name is making a claim about."""
    suffix = path.suffix
    return suffix[1:].lower() if suffix.startswith(".") else suffix.lower()


def _read_head(path: Path) -> "tuple[bytes, str]":
    """(head bytes, reason it could not be read)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(_HEAD), ""
    except OSError as exc:
        return b"", str(exc)


def _is_text(head: bytes) -> bool:
    """No NUL and no control bytes other than tab/newline/return.

    **A file containing a NUL is not text**, even if the rest is printable - and
    this is the check that catches a binary misnamed `.txt`, which is the
    version of the mismatch that actually corrupts something when it is acted
    on.
    """
    if not head:
        return True                      # an empty file is not "not text"
    # **There was an explicit `if b"\x00" in head: return False` here, and a
    # mutation removing it left the suite green - because NUL is not in
    # `_PRINTABLE`, so the test below already rejects it.** An earlier version
    # of this function only tested a NUL-bearing payload, so the printable test
    # was the untested half. Both branches are now exercised by
    # `test_a_binary_misnamed_as_text_is_the_dangerous_case`; the explicit NUL
    # branch is gone, because two copies of one rule is two places for it to be
    # wrong, and the printable test is the one that holds.
    return all(byte in _PRINTABLE or byte in (9, 10, 13) for byte in head)


def _detect(head: bytes) -> "tuple[str, str]":
    """(kind, description). An unrecognised head is ('unknown', ...) - never
    guessed at, because a format absent from the table and a file that is not
    an image are different facts."""
    for kind, description, alternatives in _SIGNATURES:
        for marks in alternatives:
            if all(offset + len(magic) <= len(head)
                   and head[offset:offset + len(magic)] == magic
                   for offset, magic in marks):
                return kind, description
    if _is_text(head):
        return "text", "text"
    return "binary", "an unrecognised binary format"


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    if not raw:
        return "Which file should I look at?"
    try:
        path = files.expand(raw)
    except files.PathProblem as exc:
        return str(exc)
    if not path.exists():
        return f"{path} does not exist."

    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"I could not read {path}: {exc}"
    if size == 0:
        # **An empty file is not "text".** The read succeeds with zero bytes,
        # `_is_text` returns True for an empty head, and the first version
        # therefore reported a 0-byte file's *name* as if it described its
        # contents - which is the exact confusion this skill exists to catch,
        # applied to the easiest case.
        return (f"{path} is **empty** - 0 bytes, so there is no content to "
                "identify and its name says nothing about anything. That is a "
                "real answer, and different from a file whose bytes could not "
                "be read.")

    head, problem = _read_head(path)
    if problem and not head:
        return f"I could not read {path}: {problem}"

    kind, description = _detect(head)
    suffix = _extension(path)

    if kind == "text":
        lines = [f"{path} contains **{description}**."]
        lines.append("")
        lines.append("Its name says it is a "
                     + (f"`.{suffix}`" if suffix else "file with no suffix")
                     + ", and the contents agree with that only insofar as the "
                       "bytes carry no binary - what *kind* of text it is comes "
                       "from the name, not from the content.")
        return "\n".join(lines)

    if kind == "binary":
        lines = [f"{path} is **{description}** - nothing in its leading bytes "
                 "matches a signature this knows."]
        lines.append("")
        lines.append("That is not the same as its name being wrong, and it is "
                     "not the same as it being broken: an unrecognised format "
                     "is a limit of this reader, not a fact about the file.")
        if suffix:
            lines.append(f"Its name says `.{suffix}`.")
        return "\n".join(lines)

    # A recognised binary kind: does the name agree?
    expected = _EXTENSIONS.get(kind, set())
    lines = [f"{path} is **{description}**."]
    if suffix and expected and suffix not in expected:
        # **The finding this skill exists for.**
        lines.append("")
        lines.append(f"**Its name says `.{suffix}`, but its bytes say "
                     f"{description.split(' ', 1)[1]}.** A mismatch here is the "
                     "usual reason a file will not open in the program its name "
                     "implies - the name is a claim about the content, and this "
                     "is the case where the claim is wrong.")
        lines.append("Renaming it will not fix it; opening it with whatever "
                     "reads this format will.")
    elif suffix and expected:
        lines.append("")
        lines.append(f"Its name (`.{suffix}`) agrees with its content.")
    elif not suffix:
        lines.append("")
        lines.append("It has no suffix, so there was no claim to check.")
    else:
        lines.append("")
        lines.append(f"`.{suffix}` is not a name this would have expected for "
                     f"{description}, but several formats share a suffix and "
                     "this is not a claim of a mismatch.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "identify_file",
        "description": (
            "Identify a file by reading its first bytes rather than trusting its "
            "name. Use for 'why won't this open', 'what is this file', 'is this "
            "really a PNG', 'this file has no extension'. Reports the detected "
            "format and whether the filename agrees with it, which is the usual "
            "cause of a file refusing to open in the program its name implies. "
            "Says plainly when a signature is unrecognised. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "The file to identify."},
            },
            "required": ["path"],
        },
    },
}

SKILLS = [Skill(name="identify_file", schema=SCHEMA, run=_run_skill)]
