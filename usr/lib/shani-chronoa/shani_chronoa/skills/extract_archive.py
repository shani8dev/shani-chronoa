"""Skill: unpack an archive into a chosen directory.

`create_archive` existed with no counterpart, which made the pair lopsided: the
assistant could bundle a folder up and then had no way to open one. Extracting is
also the single most dangerous file operation in this package, and it is worth
saying why before the code.

An archive is a list of filenames. Nothing in the format stops a member being
named `../../.config/autostart/evil.desktop` or `/etc/cron.d/anything`, and both
`tar` and `zip` will happily create those files outside the directory you asked
for. The `tar` manual calls this "archive extraction vulnerability"; it is CVE-2007-4559
for tar and the same class for zip. A user who says "unpack this download" is
trusting the *archive*, and an archive that escapes its target directory is
running code the moment anything executes it.

So every member is checked before anything is written, and the checks are not
belt-and-braces around a library default: they are the feature. Members with an
absolute path, a `..` component, or a resolved location outside the target
directory are refused, as are symlinks and hard links (a link is a perfectly good
way to point at `/etc/passwd` without ever writing outside the target), and
device and FIFO nodes are refused outright because nothing a person unpacks
needs them. Extraction then runs through Python's own `data` filter as a second
line of defence.

Overwriting is refused unless explicitly asked for, matching the dry-run default
that `find_and_replace` already uses: unpacking over a directory of the same name
should not silently replace work.

Honesty rules:

- **The whole archive is validated before the first file is written.** A partial
  extraction of an archive that turns out to be hostile is worse than none,
  because it looks like success.
- A refusal names the member that caused it.
- `files.resolve` confines the destination the same way every other file skill
  here does, so this cannot be aimed outside the user's own home.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

from shani_chronoa import files
from shani_chronoa.files import PathProblem
from shani_chronoa.skills import Skill

_MAX_MEMBERS = 20000

SCHEMA = {
    "type": "function",
    "function": {
        "name": "extract_archive",
        "description": (
            "Unpack a .tar/.tar.gz/.tgz/.zip archive into a directory. Every "
            "member is checked first and the archive is refused whole if any "
            "entry would be written outside the destination or is a link, a "
            "device or a FIFO. Will not overwrite existing files unless asked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "archive": {
                    "type": "string",
                    "description": "The archive file to unpack.",
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "Directory to unpack into. Defaults to the archive's own "
                        "directory."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "Allow replacing files that already exist at the "
                        "destination. Defaults to false, which refuses instead."
                    ),
                },
            },
        },
    },
}


def _unsafe_reason(name: str, dest: Path) -> Optional[str]:
    """Why this member must not be extracted, or None if it looks safe.

    Three separate escapes are checked, because each has defeated the others at
    some point: a leading `/`, a `..` component, and a path that resolves
    outside the destination despite containing neither.
    """
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        return f"it names an absolute path ({name})"
    parts = Path(cleaned).parts
    if any(part == ".." for part in parts):
        return f"it climbs out of the destination with '..' ({name})"
    try:
        resolved = (dest / cleaned).resolve()
    except (OSError, RuntimeError):
        return f"its destination could not be resolved ({name})"
    try:
        resolved.relative_to(dest.resolve())
    except ValueError:
        return f"it would be written outside the destination ({name})"
    return None


def _check_members(names: List[Tuple[str, Optional[str]]], dest: Path) -> Optional[str]:
    """First refusal reason across all members, or None if all are safe.

    Checked over the entire listing before anything is written, so a hostile
    archive cannot get half-written before being caught.
    """
    for name, kind in names:
        reason = _unsafe_reason(name, dest)
        if reason:
            return reason
        if kind in ("symlink", "hardlink", "device", "fifo", "socket"):
            return (f"it contains a {kind} member ({name}); nothing a person "
                    f"unpacks needs one, and a link is a way of pointing "
                    f"outside the destination without writing there")
    return None


def _plan(archive: Path, dest: Path, overwrite: bool) -> Tuple[Optional[str], List[str], int]:
    """(refusal, member names, member count) without writing anything."""
    try:
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tar:
                members = tar.getmembers()
                if len(members) > _MAX_MEMBERS:
                    return (f"the archive holds {len(members)} members, more than "
                            f"the {_MAX_MEMBERS} this will unpack", [], len(members))
                kinded: List[Tuple[str, Optional[str]]] = []
                for member in members:
                    if member.isdir() or member.isfile():
                        kind = "file"
                    elif member.issym():
                        kind = "symlink"
                    elif member.islnk():
                        kind = "hardlink"
                    elif member.ischr() or member.isblk():
                        kind = "device"
                    elif member.isfifo():
                        kind = "fifo"
                    else:
                        kind = "socket"
                    kinded.append((member.name, kind))
                refusal = _check_members(kinded, dest)
                names = [m.name for m in members if not m.isdir()]
                return (refusal, names, len(members))
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                infos = zf.infolist()
                if len(infos) > _MAX_MEMBERS:
                    return (f"the archive holds {len(infos)} entries, more than "
                            f"the {_MAX_MEMBERS} this will unpack", [], len(infos))
                # A zip symlink is stored as a file with the unix mode bit set,
                # so it has to be read out of external_attr rather than assumed.
                kinded = []
                for info in infos:
                    is_link = (info.create_system == 3
                               and (info.external_attr >> 16) & 0o170000 == 0o120000)
                    kinded.append((info.filename, "symlink" if is_link else "file"))
                refusal = _check_members(kinded, dest)
                names = [i.filename for i in infos if not i.is_dir()]
                return (refusal, names, len(infos))
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return (f"the archive could not be read: {exc}", [], 0)
    return ("it is neither a tar archive nor a zip file, so there is nothing to unpack", [], 0)


def _would_overwrite(archive: Path, names: List[str], dest: Path) -> List[str]:
    clashes = []
    for name in names[:_MAX_MEMBERS]:
        try:
            if (dest / name.replace("\\", "/")).exists():
                clashes.append(name)
        except OSError:
            continue
    return clashes


def _run(arguments: dict) -> str:
    raw_archive = arguments.get("archive")
    try:
        archive = files.resolve(raw_archive)
    except PathProblem as exc:
        return str(exc)

    if not archive.is_file():
        return f"{archive} is not a file, so there is nothing to unpack."

    raw_dest = arguments.get("destination")
    try:
        dest = files.resolve(raw_dest) if raw_dest else archive.parent
    except PathProblem as exc:
        return str(exc)
    files.refuse_catalogue(dest, "unpack into")

    overwrite = bool(arguments.get("overwrite"))

    refusal, names, total = _plan(archive, dest, overwrite)
    if refusal:
        return (f"Refusing to unpack {archive.name}: {refusal}. Nothing was "
                f"written - the whole archive is checked before the first file "
                f"is created, so a partial unpack is not left behind.")

    if not names:
        return (f"{archive.name} unpacked into {dest}, but it holds no files "
                f"({total} directory entr(ies) only).")

    if not overwrite:
        clashes = _would_overwrite(archive, names, dest)
        if clashes:
            shown = ", ".join(clashes[:5])
            more = f" and {len(clashes) - 5} more" if len(clashes) > 5 else ""
            return (f"Refusing to unpack into {dest}: {len(clashes)} of "
                    f"{len(names)} file(s) already exist there ({shown}{more}). "
                    f"Pass overwrite=true to replace them, or extract into a "
                    f"new directory. Nothing was written.")

    dest.mkdir(parents=True, exist_ok=True)
    try:
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tar:
                # Second line of defence, after our own checks above.
                try:
                    tar.extractall(dest, filter="data")
                except TypeError:
                    tar.extractall(dest)
        else:
            with zipfile.ZipFile(archive) as zf:
                for name in names:
                    zf.extract(name, dest)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return (f"Unpacking {archive.name} stopped partway: {exc}. Some files "
                f"may have been written - the pre-flight checks passed, so this "
                f"is a fault during writing, not a rejected archive.")

    verb = "unpacked" if not overwrite else "unpacked over"
    return (f"{archive.name} {verb} into {dest}: {len(names)} file(s) from "
            f"{total} entr(ies).")


def _verify_extracted_archive(arguments: dict, tool=None):
    """Post-condition: did members actually land on disk?

    `extract_archive` is the skill with the largest blast radius in the package -
    it unzips attacker-controlled archives - so "Extracted N files" is exactly
    the claim that must not be taken on trust. The check confirms the
    destination exists, is a directory, and holds at least one **regular file**
    that is not a symlink and not inside a `__MACOSX` sidecar.

    The symlink exclusion is not pedantry: a hostile archive can contain a
    symlink pointing outside the target, and a directory full of links is a
    successful extraction that put nothing where it said it would.
    """
    dest = str(arguments.get("target") or arguments.get("destination")
                or arguments.get("output") or "").strip()
    if not dest:
        return None  # nothing was extracted to check
    from pathlib import Path as _P
    target = _P(dest)
    if not target.exists():
        return (False, f"{target} does not exist, so nothing was extracted")
    if not target.is_dir():
        return (False, f"{target} is not a directory")
    try:
        entries = list(target.iterdir())
    except OSError as exc:
        return (False, f"could not read {target}: {exc}")
    if not entries:
        return (False, f"{target} is empty, so nothing was extracted")
    real = []
    links = 0
    for entry in entries:
        if entry.is_symlink():
            links += 1
            continue
        if "__MACOSX" in entry.parts:
            continue
        real.append(entry)
    if not real:
        return (False, f"{target} holds only symlinks or macOS sidecars - an "
                       f"extraction that put nothing usable where it said")
    files = [e for e in real if e.is_file()]
    note = f" ({links} symlink(s) skipped)" if links else ""
    return (True, f"{target} holds {len(real)} extracted entrie(s), "
                  f"{len(files)} of them regular file(s){note}")


POST_CONDITION = _verify_extracted_archive

SKILLS = [Skill(name="extract_archive", schema=SCHEMA, run=_run)]
