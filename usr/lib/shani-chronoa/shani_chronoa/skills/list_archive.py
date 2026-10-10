"""Skill: what is inside this archive, before extracting it?

`extract_archive` unpacks. Nothing **listed** - so "what's in this file
before I open it", which is both the more common question and the safer one,
had no answer. A user who has just downloaded something has no reason to
extract it first; the reason to list it is to decide.

It also reuses `extract_archive`'s own safety model rather than inventing a
second one. That module exists because `tar` and `zip` will happily write
outside the directory they were given - the manual calls it the "archive
extraction vulnerability" (CVE-2007-4559) - and it refuses any member with an
absolute path, a `..` component, or a resolved location outside the target.
**Listing applies the same test, and says so when a member would be refused**,
because an archive that would be refused on extraction is exactly the one a
person most wants to know about *before* asking for extraction.

**The formats, and what this does not do:**

- **tar and zip are read with Python's stdlib.** Not `tar`/`unzip`/`7z`: the
  stdlib gives a member list with no extraction step at all, which is the
  property this skill needs, and it cannot be tricked into writing anything
  because it never writes.
- **Everything else is `7z l -slt`**, because the stdlib reads tar and zip and
  nothing else - so a `.7z`, `.rar` or `.cab` would be answered with "this is
  neither a tar archive nor a zip file", a true sentence and a dead end.
  `7z` ships on both images.
- **Measured on the image (`chronoa-cli-formats.sh`, `@blue`, 2026-10-10):**
  `7z x` extracts a symlink member with **exit 0 and no warning**, which is
  why `extract_archive` checks the listing before delegating. This module
  inherits that: an archive containing `escape -> /etc/passwd` is reported
  here as *containing something that would be refused*, not as a clean file
  list.
"""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path

from shani_chronoa import archives, files
from shani_chronoa.skills import Skill
from shani_chronoa.skills.extract_archive import _unsafe_reason

_TIMEOUT = 60
_MAX_LISTED = 40


def _via_stdlib(archive: Path) -> "tuple[list, str]":
    """([(name, size)], refusal). `refusal` empty means the file was read."""
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tar:
            rows = [(m.name, m.size) for m in tar.getmembers()]
        return rows, ""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            rows = [(i.filename, i.file_size) for i in zf.infolist()]
        return rows, ""
    return [], ""


def _via_sevenzip(archive: Path) -> "tuple[list, str]":
    """`7z l -slt`, the same call `extract_archive` makes.

    `-slt` is the parseable listing: `Path = ...` / `Size = ...` records,
    rather than the aligned columns `7z l` prints by default.
    """
    import subprocess

    try:
        proc = subprocess.run(["7z", "l", "-slt", str(archive)],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return [], f"7z did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return [], str(exc)
    if proc.returncode != 0:
        # 7z's own wording is the reason; guessing from the status is how a
        # "wrong password" gets reported as a "corrupt archive".
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return [], (detail[-1] if detail else f"7z exited {proc.returncode}")

    rows = []
    path = size = None
    for line in (proc.stdout or "").splitlines():
        key, sep, value = line.partition(" = ")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "Path":
            if path is not None:
                rows.append((path, size))
            path, size = value, None
        elif key == "Size":
            size = value
    if path is not None:
        rows.append((path, size))
    # The archive itself is the first record; it is not a member.
    return [r for r in rows if r[0] != str(archive)], ""


def _human(size) -> str:
    if size is None:
        return ""
    try:
        value = int(size)
    except (TypeError, ValueError):
        return str(size)
    if value < 1024:
        return f"{value} B"
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024.0
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
    return ""


def _run(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    if not raw:
        return ("I need the path to an archive. Ask me about a .tar, .zip, "
                ".7z, .rar or .cab file and I will show what is inside it.")
    path = Path(files.expand(raw))
    if not path.is_absolute() or not path.exists():
        return f"There is no file at {path}. Nothing was guessed."
    if path.is_dir():
        return f"{path} is a folder, not an archive file."

    rows, problem = _via_stdlib(path)
    source = "the standard library"
    if not rows and not problem:
        # **Two formats 7-Zip was measured unable to open** (rc=2 against
        # archives their own tools had created), while the tools that can open
        # them are installed in the same image. They are asked first, by
        # extension, so `.lzo` and `.lrz` stop being dead ends.
        own, own_problem = archives.members(path)
        if own:
            rows, source = own, "lzop" if archives.needs_own_tool(path) and \
                path.name.lower().endswith((".lzo", ".lzo.d")) else "lrzcat"
        elif own_problem:
            return (f"I could not read {path.name}: {own_problem} Nothing was "
                    "extracted.")
        elif shutil.which("7z") is None:
            return ("This is neither a tar archive nor a zip file, and 7z "
                    "(the p7zip package) is not installed to read the rest. "
                    "Nothing was extracted and nothing was guessed.")
        else:
            rows, problem = _via_sevenzip(path)
            source = "7z"
    if problem:
        return (f"I could not read {path.name}: {problem}. Nothing was "
                "extracted.")

    # `extract_archive` refuses a member that would escape its destination.
    # The same test, applied to the listing, is the thing worth telling a
    # person *before* they ask for extraction.
    unsafe = [(name, _unsafe_reason(name, path.parent)) for name, _ in rows]
    unsafe = [(name, why) for name, why in unsafe if why]

    shown = rows[:_MAX_LISTED]
    lines = [f"{path.name} contains {len(rows)} item(s):"]
    for name, size in shown:
        text = _human(size)
        lines.append(f"  {name}" + (f"  ({text})" if text else ""))
    if len(rows) > _MAX_LISTED:
        lines.append(f"  ... and {len(rows) - _MAX_LISTED} more")

    total = sum(int(s) for _, s in rows if s is not None and str(s).isdigit())
    if total:
        lines.append("")
        lines.append(f"About {_human(total)} uncompressed, read with {source}.")

    if unsafe:
        lines.append("")
        lines.append(f"**{len(unsafe)} of these would be refused if you asked "
                     "to extract it**, because they point outside the "
                     "destination:")
        for name, why in unsafe[:5]:
            lines.append(f"  {name} - {why}")
        lines.append("Nothing has been extracted. This is the archive "
                     "extraction vulnerability (CVE-2007-4559) for tar and "
                     "zip; extraction refuses it, and so does this listing.")

    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_archive",
        "description": (
            "List what is inside an archive - .tar, .tar.gz, .zip, .7z, .rar "
            "or .cab - without extracting anything. Use for 'what's in this "
            "archive', 'does this tarball contain what I need'. Names any "
            "member that extraction would refuse because it points outside "
            "the destination, which is how a malicious archive is recognised "
            "before it is opened. Reads only; writes nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Full path to the archive."},
            },
            "required": ["path"],
        },
    },
}

SKILLS = [Skill(name="list_archive", schema=SCHEMA, run=_run)]